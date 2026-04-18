from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.enums import ChatType
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
from tgbot.services.membership_access import has_payment_exemption
from tgbot.services.membership_access import is_user_blocked
from tgbot.services.admin_access import get_effective_admin_ids, is_admin_user
from tgbot.services.chat_config_sync import resolve_membership_chat_id
from tgbot.services.notify import notify_admins_bind_confirmation_request

"""
Membership entry handlers.

Drives /start branching by application status and exposes bind-confirm request action.
"""

membership_router = Router()

TOKEN_TTL_HOURS = 24
LEGACY_IMPORT_VOTING_MEMBER_PREFIX = "legacy_import_voting_member="
LEGACY_IMPORT_EXPIRY_PREFIX = "legacy_import_expiry="
USER_START_WELCOME_TEXT = (
    "👋 Вітаємо вас!\n"
    "Це офіційний бот UAVDI.\n\n"
    "Тут ви зможете:\n"
    "🔹 отримувати актуальні новини та анонси подій\n"
    "🔹 дізнаватися про навчання, лекції та курси\n"
    "🔹 знаходити корисні матеріали та рекомендації\n"
    "🔹 бути частиною професійної спільноти, яка реально рухає стандарти "
    "ветеринарної візуальної діагностики в Україні вперед\n\n"
    "Раді бачити вас серед нас!\n\n"
    "Якщо ви вже є членом асоціації, натисніть кнопку "
    "«🔗 Я вже подавав(-ла) анкету на сайті» нижче."
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


def parse_legacy_import_voting_member(application: dict[str, Any]) -> bool:
    # Check legacy-import marker that flags a voting-group user seed.
    raw_candidates = [
        application.get("weblium_referer"),
        application.get("specialization"),
    ]
    true_values = {"1", "true", "yes", "y", "on"}
    for raw in raw_candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        marker_index = text.find(LEGACY_IMPORT_VOTING_MEMBER_PREFIX)
        if marker_index < 0:
            continue
        marker_chunk = text[
            marker_index + len(LEGACY_IMPORT_VOTING_MEMBER_PREFIX):
        ].strip()
        if not marker_chunk:
            return False
        marker_value = marker_chunk.split(";", 1)[0].strip().split()[0].strip().lower()
        return marker_value in true_values
    return False


def parse_legacy_import_expiry(application: dict[str, Any]) -> datetime | None:
    # Parse legacy import expiry marker from application metadata.
    raw_candidates = [
        application.get("weblium_referer"),
        application.get("specialization"),
    ]
    for raw in raw_candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        marker_index = text.find(LEGACY_IMPORT_EXPIRY_PREFIX)
        if marker_index < 0:
            continue
        date_chunk = text[marker_index + len(LEGACY_IMPORT_EXPIRY_PREFIX):].strip()
        if not date_chunk:
            continue
        date_token = date_chunk.split(";", 1)[0].strip().split()[0].strip()
        try:
            parsed_date = date.fromisoformat(date_token)
        except ValueError:
            continue
        return datetime.combine(parsed_date, time(23, 59, 59, tzinfo=timezone.utc))
    return None


def normalize_tg_username(username: str | None) -> str | None:
    # Normalize Telegram username for deterministic DB lookup.
    if username is None:
        return None
    value = str(username).strip().lstrip("@").lower()
    return value or None


async def find_legacy_voting_candidate_by_username(
    repo: PostgresRepo,
    username: str | None,
) -> dict[str, Any] | None:
    # Find latest approved unlinked legacy voting seed by username.
    normalized = normalize_tg_username(username)
    if normalized is None:
        return None

    candidates: list[dict[str, Any]] = []
    for candidate_email in (normalized, f"@{normalized}"):
        rows = await repo.get_unlinked_application_candidates_by_email(
            email=candidate_email,
            limit=10,
        )
        candidates.extend(rows)

    unique_by_id: dict[int, dict[str, Any]] = {}
    for row in candidates:
        app_id_raw = row.get("id")
        if app_id_raw is None:
            continue
        unique_by_id[int(app_id_raw)] = row

    for row in sorted(
        unique_by_id.values(),
        key=lambda item: item.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    ):
        if str(row.get("status") or "") != "UNLINKED_APPLICATION_APPROVED":
            continue
        if not parse_legacy_import_voting_member(row):
            continue
        return row
    return None


async def is_user_in_membership_group(
    message: Message,
    config: Config,
    tg_user_id: int,
    repo: PostgresRepo | None = None,
) -> bool:
    # Check whether user is already a member/admin/owner in membership group.
    membership_chat_id = await resolve_membership_chat_id(
        bot=message.bot,
        config=config,
        repo=repo,
    )
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


def _missing_group_ids(config: Config) -> list[str]:
    # Return list of missing critical chat-id configs.
    missing: list[str] = []
    if not int(config.voting.chat_id or 0):
        missing.append("VOTING_CHAT_ID")
    if not int(config.chat.membership_chat_id or 0):
        missing.append("CHAT_MEMBERSHIP_CHAT_ID")
    return missing


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


@membership_router.message(Command("chatid"))
async def get_chat_or_user_id(message: Message) -> None:
    # Utility command for users: returns personal Telegram ID in private chat.
    if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await message.reply(f"🆔 Chat ID: {message.chat.id}", parse_mode=None)
        return
    if message.from_user is None:
        await message.reply("⚠️ Не вдалося визначити Telegram ID.", parse_mode=None)
        return
    await message.reply(f"🆔 Ваш Telegram ID: {message.from_user.id}", parse_mode=None)


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
    is_admin = await is_admin_user(
        repo=repo,
        config=config,
        tg_user_id=from_user.id,
    )
    missing_group_ids = _missing_group_ids(config)
    if missing_group_ids:
        if is_admin:
            await message.answer(
                "⚠️ Бот ще не налаштовано для роботи з групами.\n"
                f"Відсутні параметри: {', '.join(missing_group_ids)}.\n\n"
                "Використайте команди:\n"
                "/set_voting_chat\n"
                "/set_membership_chat"
            )
        else:
            await message.answer(
                "⏳ Бот ще налаштовується адміністратором.\n"
                "Спробуйте трохи пізніше."
            )
        return

    is_payment_exempt = await has_payment_exemption(
        bot=message.bot,
        config=config,
        tg_user_id=from_user.id,
        repo=repo,
    )

    panel_data = await repo.get_user_panel_data(tg_user_id=from_user.id)
    status = str(panel_data.get("application_status") or "NEW") if panel_data else "NEW"
    application_id = (
        int(panel_data["application_id"])
        if panel_data and panel_data.get("application_id") is not None
        else None
    )
    in_membership_group = await is_user_in_membership_group(
        message=message,
        config=config,
        tg_user_id=from_user.id,
        repo=repo,
    )
    if in_membership_group:
        await repo.activate_membership_from_group_entry(
            tg_user_id=from_user.id,
            full_name=from_user.full_name,
            username=from_user.username,
            language_code=from_user.language_code,
        )
        await send_menu_entry(
            message,
            is_admin=is_admin,
            text="✅ Ви вже в групі спільноти. Тепер можете користуватися меню.",
        )
        return
    if not is_admin and is_user_blocked(panel_data):
        await message.answer("⛔️ Доступ до бота обмежено адміністратором.")
        return

    is_legacy_active_member = (
        has_active_subscription(panel_data)
        and status in {"NEW", "APPLICATION_REQUIRED"}
    )
    if is_legacy_active_member:
        await send_action_message(
            message,
            tg_user_id=from_user.id,
            text=(
                "✅ Ваш обліковий запис вже активний.\n"
                "Натисніть кнопку нижче, щоб отримати доступ до групи."
            ),
            reply_markup=group_access_keyboard(),
        )
        if is_admin:
            await send_menu_entry(message, is_admin=True)
        return

    if status in {"NEW", "APPLICATION_REQUIRED"}:
        legacy_voting_candidate = await find_legacy_voting_candidate_by_username(
            repo=repo,
            username=from_user.username,
        )
        if legacy_voting_candidate is not None:
            try:
                await repo.bind_application_to_tg_user(
                    application_id=int(legacy_voting_candidate["id"]),
                    tg_user_id=from_user.id,
                    new_status="ACTIVE_MEMBER",
                )
            except ValueError:
                legacy_voting_candidate = None

        if legacy_voting_candidate is not None:
            legacy_subscription_expires_at = parse_legacy_import_expiry(
                legacy_voting_candidate
            )
            if (
                legacy_subscription_expires_at is not None
                and legacy_subscription_expires_at > datetime.now(timezone.utc)
            ):
                await repo.set_user_subscription_until(
                    tg_user_id=from_user.id,
                    subscription_expires_at=legacy_subscription_expires_at,
                    full_name=from_user.full_name or "Невідомо",
                    username=from_user.username,
                    language_code=from_user.language_code,
                )
            await repo.upsert_voting_member(
                tg_user_id=from_user.id,
                username=from_user.username,
                full_name=from_user.full_name,
                language_code=from_user.language_code,
                member_status="ACTIVE",
                verified_at=datetime.now(timezone.utc),
            )
            await send_action_message(
                message,
                tg_user_id=from_user.id,
                text=(
                    "✅ Ви в групі голосування, тому заявка й оплата не потрібні.\n"
                    "Натисніть кнопку нижче, щоб отримати доступ до групи."
                ),
                reply_markup=group_access_keyboard(),
            )
            if is_admin:
                await send_menu_entry(message, is_admin=True)
            return

        if is_payment_exempt:
            is_in_group = await is_user_in_membership_group(
                message=message,
                config=config,
                tg_user_id=from_user.id,
                repo=repo,
            )
            if is_in_group:
                await repo.activate_membership_from_group_entry(
                    tg_user_id=from_user.id,
                    full_name=from_user.full_name,
                    username=from_user.username,
                    language_code=from_user.language_code,
                )
                await send_menu_entry(message, is_admin=is_admin)
                return
            await send_action_message(
                message,
                tg_user_id=from_user.id,
                text=(
                    "✅ Ви в групі голосування, тому заявка й оплата не потрібні.\n"
                    "Натисніть кнопку нижче, щоб отримати доступ до групи."
                ),
                reply_markup=group_access_keyboard(),
            )
            if is_admin:
                await send_menu_entry(message, is_admin=True)
            return
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

    if status == "APPROVED_AWAITING_PAYMENT" and is_payment_exempt:
        is_in_group = await is_user_in_membership_group(
            message=message,
            config=config,
            tg_user_id=from_user.id,
            repo=repo,
        )
        if is_in_group:
            await repo.activate_membership_from_group_entry(
                tg_user_id=from_user.id,
                full_name=from_user.full_name,
                username=from_user.username,
                language_code=from_user.language_code,
            )
            await send_menu_entry(message, is_admin=is_admin)
            return
        await send_action_message(
            message,
            tg_user_id=from_user.id,
            text=(
                "✅ Вашу заявку підтверджено. Для учасників групи голосування оплата не потрібна.\n"
                "Натисніть кнопку нижче, щоб отримати доступ до групи."
            ),
            reply_markup=group_access_keyboard(),
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
        if status == "ACTIVE_MEMBER" and not has_active_subscription(panel_data):
            if application_id is not None:
                app_row = await repo.get_application_by_id(application_id)
            else:
                app_row = None
            if app_row and parse_legacy_import_voting_member(app_row):
                legacy_subscription_expires_at = parse_legacy_import_expiry(app_row)
                if (
                    legacy_subscription_expires_at is not None
                    and legacy_subscription_expires_at > datetime.now(timezone.utc)
                ):
                    await repo.set_user_subscription_until(
                        tg_user_id=from_user.id,
                        subscription_expires_at=legacy_subscription_expires_at,
                        full_name=from_user.full_name or "Невідомо",
                        username=from_user.username,
                        language_code=from_user.language_code,
                    )
                    panel_data = await repo.get_user_panel_data(tg_user_id=from_user.id)
        if has_active_subscription(panel_data) or is_payment_exempt:
            is_in_group = await is_user_in_membership_group(
                message=message,
                config=config,
                tg_user_id=from_user.id,
                repo=repo,
            )
            if is_in_group:
                await repo.activate_membership_from_group_entry(
                    tg_user_id=from_user.id,
                    full_name=from_user.full_name,
                    username=from_user.username,
                    language_code=from_user.language_code,
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
    repo: PostgresRepo,
    config: Config,
):
    # Ask admins to manually confirm bind for previously unlinked approved application.
    await query.answer()

    payload: dict[str, Any] = {
        "tg_user_id": query.from_user.id if query.from_user else None,
        "username": query.from_user.username if query.from_user else None,
    }
    admin_ids = await get_effective_admin_ids(repo=repo, config=config)
    await notify_admins_bind_confirmation_request(
        bot=query.bot,
        admin_ids=admin_ids,
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
