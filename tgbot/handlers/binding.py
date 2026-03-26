from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.membership import (
    application_entry_keyboard,
    bind_back_keyboard,
    payment_keyboard,
)
from tgbot.services.menu_state import (
    MENU_STATE_ACTION,
    clear_tracked_keyboard,
    remember_tracked_message,
)
from tgbot.services.notify import notify_admins

"""
Binding handlers for users who submitted website forms without tg_token.

This module resolves phone-based matching, safe auto-binding rules,
manual admin confirmation cases, and post-bind routing to next step.
"""

binding_router = Router()
TOKEN_TTL_HOURS = 24


class BindStates(StatesGroup):
    # Waiting for phone number entered by user after "I already applied".
    waiting_phone = State()


def build_tokenized_url(base_url: str, token: str) -> str:
    # Keep original query params, replace/add tg_token.
    parts = urlsplit(base_url)
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "tg_token"
    ]
    query_items.append(("tg_token", token))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query_items, doseq=True),
            parts.fragment,
        )
    )


async def get_or_create_active_application_token(
    repo: PostgresRepo,
    tg_user_id: int,
) -> str:
    # Reuse active token or create a new one for website application link.
    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            active = await repo.get_active_application_token(tg_user_id, conn=conn)
            if active:
                return active["token"]

            created = await repo.create_application_token(
                tg_user_id=tg_user_id,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
                metadata={"source": "binding_back"},
                conn=conn,
            )
            return created["token"]


def extract_phone_digits(value: str) -> str:
    # Normalize phone input to digits-only format.
    return "".join(ch for ch in value if ch.isdigit())


async def find_unlinked_candidates_by_phone(
    repo: PostgresRepo,
    phone_input: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    # Find unlinked applications that match phone by last 10 digits.
    digits = extract_phone_digits(phone_input)
    if len(digits) < 10:
        return []

    phone_tail10 = digits[-10:]
    async with repo.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM applications
            WHERE tg_user_id IS NULL
              AND status IN ('UNLINKED_APPLICATION_PENDING', 'UNLINKED_APPLICATION_APPROVED')
              AND right(regexp_replace(COALESCE(contact_phone, ''), '\\D', '', 'g'), 10) = $1
            ORDER BY created_at DESC
            LIMIT $2;
            """,
            phone_tail10,
            limit,
        )
    return [dict(row) for row in rows]


async def find_other_linked_users_by_phone_digits(
    repo: PostgresRepo,
    phone_digits: str,
    current_tg_user_id: int,
) -> list[int]:
    # Check if the same phone is already linked to other Telegram users.
    if len(phone_digits) < 10:
        return []

    tail10 = phone_digits[-10:]
    async with repo.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT tg_user_id
            FROM applications
            WHERE tg_user_id IS NOT NULL
              AND tg_user_id <> $2
              AND right(regexp_replace(COALESCE(contact_phone, ''), '\\D', '', 'g'), 10) = $1
            LIMIT 20;
            """,
            tail10,
            current_tg_user_id,
        )
    return [int(row["tg_user_id"]) for row in rows if row["tg_user_id"] is not None]


async def has_linked_membership_flow(repo: PostgresRepo, tg_user_id: int) -> bool:
    # Prevent duplicate binding when user already has active membership flow.
    async with repo.pool.acquire() as conn:
        found = await conn.fetchval(
            """
            SELECT 1
            FROM applications
            WHERE tg_user_id = $1
              AND status IN (
                'APPLICATION_PENDING',
                'APPROVED_AWAITING_PAYMENT',
                'PAID_AWAITING_JOIN',
                'ACTIVE_MEMBER'
              )
            LIMIT 1;
            """,
            tg_user_id,
        )
    return found is not None


def build_manual_bind_required_admin_text(
    tg_user_id: int,
    username: str | None,
    phone_input: str,
    reason: str,
    candidates: list[dict[str, Any]],
    other_linked_tg_users: list[int] | None = None,
) -> str:
    # Build compact admin message for manual bind review cases.
    lines = [
        "Manual bind confirmation required",
        f"tg_user_id={tg_user_id}",
        f"username={username or '-'}",
        f"phone={phone_input}",
        f"reason={reason}",
    ]
    if other_linked_tg_users:
        lines.append(
            f"other_linked_tg_users={','.join(str(v) for v in other_linked_tg_users)}"
        )
    lines.append("Candidates:")

    for app in sorted(candidates, key=lambda x: x["id"], reverse=True):
        lines.append(
            f"- id={app['id']} status={app['status']} "
            f"phone={app.get('contact_phone') or '-'} email={app.get('contact_email') or '-'}"
        )
    return "\n".join(lines)


async def notify_admin_manual_bind_required(
    message: Message,
    config: Config,
    tg_user_id: int,
    username: str | None,
    phone_input: str,
    reason: str,
    candidates: list[dict[str, Any]],
    other_linked_tg_users: list[int] | None = None,
) -> None:
    # Send manual-bind alert to all admins.
    await notify_admins(
        bot=message.bot,
        admin_ids=config.tg_bot.admin_ids,
        text=build_manual_bind_required_admin_text(
            tg_user_id=tg_user_id,
            username=username,
            phone_input=phone_input,
            reason=reason,
            candidates=candidates,
            other_linked_tg_users=other_linked_tg_users,
        ),
        context={
            "event": "manual_bind_confirmation_required",
            "tg_user_id": tg_user_id,
            "candidates_count": len(candidates),
            "reason": reason,
        },
    )


async def edit_menu_message(
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup=None,
) -> None:
    # Edit stored action message or fallback to a new message.
    data = await state.get_data()
    chat_id = data.get("menu_chat_id")
    message_id = data.get("menu_message_id")

    if chat_id and message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
            )
            if message.from_user is not None:
                if reply_markup is None:
                    await clear_tracked_keyboard(
                        bot=message.bot,
                        state=MENU_STATE_ACTION,
                        tg_user_id=message.from_user.id,
                    )
                else:
                    remember_tracked_message(
                        state=MENU_STATE_ACTION,
                        tg_user_id=message.from_user.id,
                        chat_id=int(chat_id),
                        message_id=int(message_id),
                    )
            return
        except Exception:  # noqa: BLE001
            pass

    sent = await message.answer(text, reply_markup=reply_markup)
    if message.from_user is not None:
        if reply_markup is None:
            await clear_tracked_keyboard(
                bot=message.bot,
                state=MENU_STATE_ACTION,
                tg_user_id=message.from_user.id,
            )
        else:
            remember_tracked_message(
                state=MENU_STATE_ACTION,
                tg_user_id=message.from_user.id,
                chat_id=sent.chat.id,
                message_id=sent.message_id,
            )

@binding_router.callback_query(F.data == "membership_site_applied")
async def binding_entrypoint(query: CallbackQuery, state: FSMContext) -> None:
    # Start self-bind flow and request website phone number.
    await query.answer()
    if query.message is None:
        return

    await state.set_state(BindStates.waiting_phone)
    await state.update_data(
        menu_chat_id=query.message.chat.id,
        menu_message_id=query.message.message_id,
    )
    await query.message.edit_text(
        "Enter the phone number you used in the website form.",
        reply_markup=bind_back_keyboard(),
    )
    remember_tracked_message(
        state=MENU_STATE_ACTION,
        tg_user_id=query.from_user.id,
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
    )


@binding_router.callback_query(F.data == "membership_bind_back")
async def binding_back(
    query: CallbackQuery,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Return user from bind flow back to tokenized website application button.
    await query.answer()
    from_user = query.from_user
    if query.message is None or from_user is None:
        await state.clear()
        return

    token = await get_or_create_active_application_token(repo, from_user.id)
    base_url = config.membership.application_link_base_url or config.membership.application_url
    tokenized_url = build_tokenized_url(base_url, token)

    await state.clear()
    edited = await query.message.edit_text(
        "Please submit your application from this button.\n"
        "Submissions from this tokenized link are processed automatically.",
        reply_markup=application_entry_keyboard(application_url=tokenized_url),
    )
    remember_tracked_message(
        state=MENU_STATE_ACTION,
        tg_user_id=from_user.id,
        chat_id=edited.chat.id,
        message_id=edited.message_id,
    )
    await repo.merge_application_token_metadata(
        token=token,
        metadata={
            "entry_chat_id": edited.chat.id,
            "entry_message_id": edited.message_id,
        },
    )


@binding_router.message(BindStates.waiting_phone)
async def binding_receive_phone(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Validate phone, auto-bind safely when possible, otherwise notify admins.
    from_user = message.from_user
    if from_user is None:
        await message.answer("Unable to identify Telegram user.")
        return

    phone_input = (message.text or "").strip()
    phone_digits = extract_phone_digits(phone_input)
    if len(phone_digits) < 10:
        await edit_menu_message(
            message=message,
            state=state,
            text="Phone number looks invalid. Enter a valid phone number.",
            reply_markup=bind_back_keyboard(),
        )
        return

    await repo.create_or_update_user(
        tg_user_id=from_user.id,
        full_name=from_user.full_name or "Unknown",
        username=from_user.username,
        language_code=from_user.language_code,
    )

    if await has_linked_membership_flow(repo, from_user.id):
        await state.clear()
        await edit_menu_message(
            message=message,
            state=state,
            text=(
                "Your Telegram account already has an active membership flow.\n"
                "Please contact admin if you need to merge records."
            ),
        )
        return

    candidates = await find_unlinked_candidates_by_phone(repo, phone_input)
    if not candidates:
        await state.clear()
        await edit_menu_message(
            message=message,
            state=state,
            text=(
                "We could not find an unlinked application by this phone.\n"
                "Check the number and try again, or contact admin."
            ),
        )
        return

    other_linked_tg_users = await find_other_linked_users_by_phone_digits(
        repo=repo,
        phone_digits=phone_digits,
        current_tg_user_id=from_user.id,
    )
    if other_linked_tg_users:
        await state.clear()
        await notify_admin_manual_bind_required(
            message=message,
            config=config,
            tg_user_id=from_user.id,
            username=from_user.username,
            phone_input=phone_input,
            reason="phone_already_used_by_linked_account",
            candidates=candidates,
            other_linked_tg_users=other_linked_tg_users,
        )
        await edit_menu_message(
            message=message,
            state=state,
            text=(
                "This phone is already linked to another Telegram account.\n"
                "Admins were notified for manual verification."
            ),
        )
        return

    if len(candidates) > 1:
        await state.clear()
        await notify_admin_manual_bind_required(
            message=message,
            config=config,
            tg_user_id=from_user.id,
            username=from_user.username,
            phone_input=phone_input,
            reason="multiple_unlinked_candidates",
            candidates=candidates,
        )
        await edit_menu_message(
            message=message,
            state=state,
            text=(
                "We found multiple possible applications.\n"
                "Admins were notified to confirm binding manually."
            ),
        )
        return

    candidate = candidates[0]
    candidate_status = candidate["status"]
    if candidate_status == "UNLINKED_APPLICATION_PENDING":
        target_status = "APPLICATION_PENDING"
    elif candidate_status == "UNLINKED_APPLICATION_APPROVED":
        target_status = "APPROVED_AWAITING_PAYMENT"
    else:
        await state.clear()
        await edit_menu_message(
            message=message,
            state=state,
            text="This application cannot be bound automatically. Please contact admin.",
        )
        return

    try:
        bound = await repo.bind_application_to_tg_user(
            application_id=int(candidate["id"]),
            tg_user_id=from_user.id,
            new_status=target_status,
        )
    except ValueError:
        await state.clear()
        await edit_menu_message(
            message=message,
            state=state,
            text=(
                "Binding failed because the application was already updated.\n"
                "Please contact admin for manual confirmation."
            ),
        )
        return

    await state.clear()

    if target_status == "APPROVED_AWAITING_PAYMENT":
        await edit_menu_message(
            message=message,
            state=state,
            text=(
                "Your previous site application was linked successfully and is approved.\n"
                "You can proceed to payment now."
            ),
            reply_markup=payment_keyboard(),
        )
    else:
        await edit_menu_message(
            message=message,
            state=state,
            text=(
                "Your previous site application was linked successfully.\n"
                "Current status: under review."
            ),
        )

    await notify_admins(
        bot=message.bot,
        admin_ids=config.tg_bot.admin_ids,
        text=(
            "Application bound to Telegram user\n"
            f"application_id={bound.get('id')}\n"
            f"tg_user_id={from_user.id}\n"
            f"status={bound.get('status')}"
        ),
        context={
            "event": "application_bound_to_tg_user",
            "application_id": bound.get("id"),
            "tg_user_id": from_user.id,
        },
    )
