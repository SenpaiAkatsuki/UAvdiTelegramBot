from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramMigrateToChat
from aiogram.types import CallbackQuery

from tgbot.callbacks.voting import (
    ApplicationVoteContactCallbackData,
    ApplicationVoteCallbackData,
    VOTE_DECISION_APPROVE,
)
from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.services.application_voting import (
    VOTE_CAST_CLOSED,
    VOTE_CAST_EXPIRED,
    VOTE_CAST_NOT_FOUND,
    VOTE_CAST_OK,
    cast_admin_vote,
    close_due_votes,
    finalize_vote_if_target_reached,
    refresh_vote_message_markup,
)
from tgbot.services.chat_config_sync import resolve_voting_chat_id
from tgbot.services.chat_config_sync import RUNTIME_VOTING_CHAT_ID_KEY

"""
Application voting callbacks.

Handles inline approve/reject vote buttons in one-message voting-group flow.
"""

admin_applications_router = Router()


def _is_chat_member_status(status: str) -> bool:
    # Return True for active chat-member statuses.
    return status in {"creator", "administrator", "member", "restricted"}


async def _can_vote_in_voting_chat(
    query: CallbackQuery,
    config: Config,
    repo: PostgresRepo,
) -> bool:
    # Allow voting only from members of configured voting group.
    if query.from_user is None or query.message is None:
        return False

    voting_chat_id = await resolve_voting_chat_id(
        bot=query.bot,
        config=config,
        repo=repo,
    )
    if voting_chat_id is None:
        return False

    message_chat = query.message.chat
    if int(message_chat.id) != int(voting_chat_id):
        return False

    user_id = int(query.from_user.id)

    try:
        member = await query.bot.get_chat_member(
            chat_id=int(voting_chat_id),
            user_id=user_id,
        )
    except TelegramMigrateToChat as exc:
        config.voting.chat_id = int(exc.migrate_to_chat_id)
        await repo.set_setting(
            RUNTIME_VOTING_CHAT_ID_KEY,
            str(config.voting.chat_id),
        )
        try:
            member = await query.bot.get_chat_member(
                chat_id=int(config.voting.chat_id),
                user_id=user_id,
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            return False
    except (TelegramBadRequest, TelegramForbiddenError):
        return False

    is_active = _is_chat_member_status(str(member.status))
    if is_active:
        await repo.upsert_voting_member(
            tg_user_id=user_id,
            username=query.from_user.username,
            full_name=query.from_user.full_name,
            language_code=query.from_user.language_code,
            member_status="ACTIVE",
            verified_at=datetime.now(timezone.utc),
        )
        return True

    updated = await repo.set_voting_member_status(
        tg_user_id=user_id,
        member_status="LEFT",
        clear_admin=False,
    )
    if updated is None:
        await repo.upsert_voting_member(
            tg_user_id=user_id,
            username=query.from_user.username,
            full_name=query.from_user.full_name,
            language_code=query.from_user.language_code,
            member_status="LEFT",
            verified_at=datetime.now(timezone.utc),
        )
    return False


def _format_manual_contact_alert(
    application: dict,
    user_row: dict | None,
    *,
    tg_missing: bool = False,
) -> str:
    # Build short contact summary for callback alert popup.
    lines: list[str] = []
    if tg_missing:
        lines.append("Telegram-профіль за цією заявкою ще не прив'язано.")
    if False and tg_missing:
        lines.append("Користувача немає в Telegram.")
    lines.append("Контакт для ручної комунікації:")

    tg_user_id = application.get("tg_user_id")
    if tg_user_id is not None:
        username = (user_row or {}).get("username")
        if isinstance(username, str) and username.strip():
            lines.append(f"Telegram: @{username.strip()}")
        else:
            lines.append(f"Telegram ID: {int(tg_user_id)}")

    phone = application.get("contact_phone")
    if isinstance(phone, str) and phone.strip():
        lines.append(f"Телефон: {phone.strip()}")

    email = application.get("contact_email")
    if isinstance(email, str) and email.strip():
        lines.append(f"Email: {email.strip()}")

    if len(lines) == 1:
        lines.append("Дані контакту не вказані.")

    alert_text = "\n".join(lines)
    return alert_text[:200]


def _build_telegram_contact_url(
    application: dict,
    user_row: dict | None,
) -> str | None:
    # Build direct Telegram contact URL when account is linked.
    tg_user_id = application.get("tg_user_id")
    if tg_user_id is None:
        return None

    username = (user_row or {}).get("username")
    if isinstance(username, str) and username.strip():
        cleaned = username.strip().lstrip("@")
        if cleaned:
            return f"https://t.me/{cleaned}"

    try:
        return f"tg://user?id={int(tg_user_id)}"
    except (TypeError, ValueError):
        return None


@admin_applications_router.callback_query(ApplicationVoteCallbackData.filter())
async def handle_application_vote_callback(
    query: CallbackQuery,
    callback_data: ApplicationVoteCallbackData,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Accept voting-group member vote, persist it, and refresh inline counters.
    if query.from_user is None:
        await query.answer()
        return

    if not await _can_vote_in_voting_chat(query, config, repo):
        await query.answer(
            "Голосувати можуть лише учасники групи голосування.",
            show_alert=True,
        )
        return

    approve = callback_data.decision == VOTE_DECISION_APPROVE
    cast_status, application_row = await cast_admin_vote(
        repo,
        application_id=callback_data.application_id,
        tg_user_id=query.from_user.id,
        approve=approve,
    )

    if cast_status == VOTE_CAST_NOT_FOUND:
        await query.answer("Заявку не знайдено.", show_alert=True)
        if query.message is not None:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
        return

    if cast_status in {VOTE_CAST_CLOSED, VOTE_CAST_EXPIRED}:
        await query.answer("Голосування вже завершено.", show_alert=True)
        if cast_status == VOTE_CAST_EXPIRED:
            await close_due_votes(
                bot=query.bot,
                config=config,
                repo=repo,
            )
        return

    if cast_status == VOTE_CAST_OK and application_row is not None:
        finalized = await finalize_vote_if_target_reached(
            bot=query.bot,
            config=config,
            repo=repo,
            application_row=application_row,
        )
        if finalized:
            await query.answer("Ваш голос зараховано. Рішення прийнято.")
            return

        await refresh_vote_message_markup(
            bot=query.bot,
            repo=repo,
            application_row=application_row,
        )
        await query.answer("Ваш голос зараховано.")
        return

    await query.answer("Не вдалося обробити голос.", show_alert=True)


@admin_applications_router.callback_query(ApplicationVoteContactCallbackData.filter())
async def handle_application_manual_contact_callback(
    query: CallbackQuery,
    callback_data: ApplicationVoteContactCallbackData,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Show manual contact data from application/user records.
    if not await _can_vote_in_voting_chat(query, config, repo):
        await query.answer(
            "Кнопка доступна лише для учасників групи голосування.",
            show_alert=True,
        )
        return

    application = await repo.get_application_by_id(callback_data.application_id)
    if not application:
        await query.answer("Заявку не знайдено.", show_alert=True)
        return

    tg_user_id = application.get("tg_user_id")
    if tg_user_id is None:
        matched_tg_user_id = await repo.find_linked_tg_user_id_by_contacts(
            contact_phone=application.get("contact_phone"),
            contact_email=application.get("contact_email"),
        )
        if matched_tg_user_id is not None:
            tg_user_id = matched_tg_user_id
            application = dict(application)
            application["tg_user_id"] = matched_tg_user_id

    user_row = None
    if tg_user_id is not None:
        user_row = await repo.get_user_by_tg_user_id(int(tg_user_id))

    contact_url = _build_telegram_contact_url(application=application, user_row=user_row)
    if contact_url:
        try:
            await query.answer(url=contact_url)
            return
        except TelegramBadRequest:
            pass

    await query.answer(
        _format_manual_contact_alert(
            application=application,
            user_row=user_row,
            tg_missing=tg_user_id is None,
        ),
        show_alert=True,
    )


@admin_applications_router.callback_query(F.data.startswith("admin_application_"))
@admin_applications_router.callback_query(F.data.startswith("admin_unlinked_"))
async def admin_legacy_application_callbacks(query: CallbackQuery) -> None:
    # Keep old disabled callback namespace explicit for backward compatibility.
    await query.answer(
        "Застарілий формат кнопок. Використовуйте активні кнопки голосування у повідомленні заявки.",
        show_alert=True,
    )
