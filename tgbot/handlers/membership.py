from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.membership import (
    application_entry_keyboard,
    bind_confirmation_keyboard,
    group_access_keyboard,
    payment_keyboard,
)
from tgbot.keyboards.menu import menu_entry_keyboard
from tgbot.services.menu_state import (
    MENU_STATE_ACTION,
    MENU_STATE_ENTRY,
    MENU_STATE_MENU,
    clear_tracked_keyboard,
    remember_tracked_message,
)
from tgbot.services.notify import notify_admins_bind_confirmation_request

"""
Membership entry handlers.

Drives /start branching by application status and exposes bind-confirm request action.
"""

membership_router = Router()

TOKEN_TTL_HOURS = 24
USER_START_WELCOME_TEXT = (
    "👋 Вітаємо вас!\n"
    "Це офіційний бот Української Асоціації Ветеринарної Візуальної Діагностики.\n\n"
    "Тут ви зможете:\n"
    "🔹 отримувати актуальні новини та анонси подій\n"
    "🔹 дізнаватися про навчання, лекції та курси\n"
    "🔹 знаходити корисні матеріали та рекомендації\n"
    "🔹 бути частиною професійної спільноти, яка реально рухає стандарти "
    "ветеринарної візуальної діагностики в Україні вперед\n\n"
    "Раді бачити вас серед нас!"
)
MENU_MEMBER_STATUSES = {"member", "administrator", "creator"}


def build_tokenized_url(base_url: str, token: str) -> str:
    # Keep existing query params and inject/replace tg_token.
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


def has_active_subscription(user_row: dict[str, Any] | None) -> bool:
    # Subscription is active only when status is ACTIVE and expiry is in future.
    if not user_row:
        return False
    if str(user_row.get("subscription_status")) != "ACTIVE":
        return False

    expires_at = user_row.get("subscription_expires_at")
    if not isinstance(expires_at, datetime):
        return False
    return expires_at > datetime.now(timezone.utc)


async def is_user_in_membership_group(
    message: Message,
    config: Config,
    tg_user_id: int,
) -> bool:
    # Check whether user is already a member/admin/owner in membership group.
    membership_chat_id = config.chat.membership_chat_id
    if not membership_chat_id:
        return False
    try:
        chat_member = await message.bot.get_chat_member(
            chat_id=membership_chat_id,
            user_id=tg_user_id,
        )
    except TelegramAPIError:
        return False
    return str(chat_member.status).lower() in MENU_MEMBER_STATUSES


async def send_menu_entry(
    message: Message,
    *,
    is_admin: bool,
    text: str = "📋 Тепер ви можете користуватися меню.",
) -> None:
    # Send menu entry button and track message for cleanup.
    if message.from_user is not None:
        await clear_tracked_keyboard(
            bot=message.bot,
            state=MENU_STATE_ENTRY,
            tg_user_id=message.from_user.id,
        )
        await clear_tracked_keyboard(
            bot=message.bot,
            state=MENU_STATE_MENU,
            tg_user_id=message.from_user.id,
        )
    sent = await message.answer(text, reply_markup=menu_entry_keyboard(is_admin=is_admin))
    if message.from_user is not None:
        remember_tracked_message(
            state=MENU_STATE_ENTRY,
            tg_user_id=message.from_user.id,
            chat_id=sent.chat.id,
            message_id=sent.message_id,
        )


async def send_action_message(
    message: Message,
    *,
    tg_user_id: int,
    text: str,
    reply_markup,
) -> Message:
    # Send main action message for current flow step and track it.
    await clear_tracked_keyboard(
        bot=message.bot,
        state=MENU_STATE_ACTION,
        tg_user_id=tg_user_id,
    )
    sent = await message.answer(text, reply_markup=reply_markup)
    remember_tracked_message(
        state=MENU_STATE_ACTION,
        tg_user_id=tg_user_id,
        chat_id=sent.chat.id,
        message_id=sent.message_id,
    )
    return sent


async def get_or_create_active_application_token(
    repo: PostgresRepo,
    tg_user_id: int,
) -> str:
    # Reuse unexpired token or create a new 24h token for application link.
    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            active = await repo.get_active_application_token(tg_user_id, conn=conn)
            if active:
                return active["token"]

            created = await repo.create_application_token(
                tg_user_id=tg_user_id,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
                metadata={"source": "membership_start"},
                conn=conn,
            )
            return created["token"]


@membership_router.message(CommandStart())
async def membership_start(
    message: Message,
    repo: PostgresRepo,
    config: Config,
):
    # Main /start router for membership lifecycle states.
    from_user = message.from_user
    if from_user is None:
        await message.answer("⚠️ Не вдалося визначити користувача.")
        return

    await repo.create_or_update_user(
        tg_user_id=from_user.id,
        full_name=from_user.full_name or "Невідомо",
        username=from_user.username,
        language_code=from_user.language_code,
    )
    await clear_tracked_keyboard(
        bot=message.bot,
        state=MENU_STATE_ACTION,
        tg_user_id=from_user.id,
    )
    await clear_tracked_keyboard(
        bot=message.bot,
        state=MENU_STATE_ENTRY,
        tg_user_id=from_user.id,
    )
    await clear_tracked_keyboard(
        bot=message.bot,
        state=MENU_STATE_MENU,
        tg_user_id=from_user.id,
    )
    is_admin = from_user.id in config.tg_bot.admin_ids

    panel_data = await repo.get_user_panel_data(tg_user_id=from_user.id)
    status = str(panel_data.get("application_status") or "NEW") if panel_data else "NEW"
    application_id = (
        int(panel_data["application_id"])
        if panel_data and panel_data.get("application_id") is not None
        else None
    )

    if status in {"NEW", "APPLICATION_REQUIRED"}:
        token = await get_or_create_active_application_token(repo, from_user.id)
        base_url = (
            config.membership.application_link_base_url
            or config.membership.application_url
        )
        tokenized_url = build_tokenized_url(base_url, token)
        sent = await send_action_message(
            message,
            tg_user_id=from_user.id,
            text=USER_START_WELCOME_TEXT,
            reply_markup=application_entry_keyboard(
                application_url=tokenized_url,
            ),
        )
        await repo.merge_application_token_metadata(
            token=token,
            metadata={
                "entry_chat_id": sent.chat.id,
                "entry_message_id": sent.message_id,
            },
        )
        return

    if status == "APPLICATION_PENDING":
        await message.answer(
            "⏳ Ваша заявка на розгляді. Будь ласка, дочекайтеся рішення адміністраторів."
        )
        if is_admin:
            await send_menu_entry(message, is_admin=True)
        return

    if status == "UNLINKED_APPLICATION_APPROVED":
        await send_action_message(
            message,
            tg_user_id=from_user.id,
            text=(
                "🔎 Знайдено підтверджену заявку із сайту, яка ще не привʼязана до вашого Telegram-акаунта.\n"
                "Будь ласка, запросіть підтвердження привʼязки перед оплатою."
            ),
            reply_markup=bind_confirmation_keyboard(),
        )
        if is_admin:
            await send_menu_entry(message, is_admin=True)
        return

    if status == "APPROVED_AWAITING_PAYMENT":
        await send_action_message(
            message,
            tg_user_id=from_user.id,
            text="✅ Вашу заявку підтверджено. Завершіть оплату, щоб продовжити.",
            reply_markup=payment_keyboard(),
        )
        if is_admin:
            await send_menu_entry(message, is_admin=True)
        return

    if status in {"PAID_AWAITING_JOIN", "ACTIVE_MEMBER"}:
        if has_active_subscription(panel_data):
            is_in_group = await is_user_in_membership_group(
                message=message,
                config=config,
                tg_user_id=from_user.id,
            )
            if is_in_group:
                if status == "PAID_AWAITING_JOIN" and application_id is not None:
                    await repo.update_application_status(
                        application_id=application_id,
                        status="ACTIVE_MEMBER",
                    )
                await send_menu_entry(
                    message,
                    is_admin=is_admin,
                    text=(
                        "✅ Ви вже в групі спільноти. Тепер можете користуватися меню."
                    ),
                )
                return

            if status in {"PAID_AWAITING_JOIN", "ACTIVE_MEMBER"}:
                await send_action_message(
                    message,
                    tg_user_id=from_user.id,
                    text="✅ Оплату підтверджено. Натисніть кнопку нижче, щоб отримати доступ до групи.",
                    reply_markup=group_access_keyboard(),
                )
        else:
            await send_action_message(
                message,
                tg_user_id=from_user.id,
                text=(
                    "⏳ Потрібно продовжити підписку: термін дії завершився.\n"
                    "Натисніть кнопку продовження, щоб активувати ще 365 днів."
                ),
                reply_markup=payment_keyboard(pay_button_text="💳 Продовжити підписку"),
            )
        if is_admin:
            await send_menu_entry(message, is_admin=True)
        return

    await message.answer(
        f"ℹ️ Поточний статус: {status}. ID заявки: {application_id or '-'}."
    )
    if is_admin:
        await send_menu_entry(message, is_admin=True)
    if status not in {"NEW", "APPLICATION_REQUIRED"}:
        return


@membership_router.callback_query(F.data == "membership_bind_confirmation_request")
async def membership_bind_confirmation_request(
    query: CallbackQuery,
    config: Config,
):
    # Ask admins to manually confirm bind for previously unlinked approved application.
    await query.answer()

    payload: dict[str, Any] = {
        "tg_user_id": query.from_user.id if query.from_user else None,
        "username": query.from_user.username if query.from_user else None,
    }
    await notify_admins_bind_confirmation_request(
        bot=query.bot,
        admin_ids=config.tg_bot.admin_ids,
        bind_request=payload,
    )
    if query.message is not None:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
    if query.from_user is not None:
        await clear_tracked_keyboard(
            bot=query.bot,
            state=MENU_STATE_ACTION,
            tg_user_id=query.from_user.id,
        )
    if query.message is not None:
        await query.message.answer("✅ Запит на підтвердження привʼязки надіслано адміністраторам.")
