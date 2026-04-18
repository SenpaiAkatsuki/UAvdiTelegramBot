from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from tgbot.callbacks.menu import (
    MenuCallbackData,
    SCOPE_ADMIN,
    SCOPE_USER,
    VIEW_ADMIN_APPROVE_PENDING,
    VIEW_ADMIN_ADD_ADMIN,
    VIEW_ADMIN_BROADCAST,
    VIEW_ADMIN_EXPIRING_SETTINGS,
    VIEW_ADMIN_LIBRARY_ADD_ARTICLE,
    VIEW_ADMIN_LIBRARY_ADD_TOPIC,
    VIEW_ADMIN_LIBRARY_ARTICLE,
    VIEW_ADMIN_LIBRARY_ARTICLES,
    VIEW_ADMIN_LIBRARY_DELETE_ARTICLE,
    VIEW_ADMIN_LIBRARY_DELETE_TOPIC,
    VIEW_ADMIN_LIBRARY_EDIT_ARTICLE,
    VIEW_ADMIN_LIBRARY_EDIT_TOPIC,
    VIEW_ADMIN_LIBRARY_TOPICS,
    VIEW_ADMIN_MANAGEMENT,
    VIEW_ADMIN_SUBSCRIPTION_PRICE,
    VIEW_ADMIN_USER_DETAIL,
    VIEW_ADMIN_VOTING_SETTINGS,
    VIEW_ADMIN_ROOT,
    VIEW_LIBRARY_ARTICLE,
    VIEW_LIBRARY_ARTICLES,
    VIEW_LIBRARY_TOPICS,
    VIEW_USER_ROOT,
)
from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.filters.admin import AdminFilter
from tgbot.keyboards.reply import (
    LIBRARY_REPLY_BACK_TO_MENU,
    LIBRARY_REPLY_BACK_TO_TOPICS,
    LIBRARY_REPLY_NEXT_PAGE,
    LIBRARY_REPLY_PREV_PAGE,
    library_articles_reply_keyboard,
    library_topics_reply_keyboard,
)
from tgbot.services.membership_access import has_payment_exemption, is_user_blocked
from tgbot.services.admin_access import is_admin_user
from tgbot.services import broadcaster
from tgbot.services.menu_renderer import MenuScreen, render_menu_screen
from tgbot.services.menu_state import (
    MENU_STATE_ACTION,
    MENU_STATE_ENTRY,
    MENU_STATE_MENU,
    clear_tracked_keyboard,
    forget_tracked_message,
    get_tracked_message,
    remember_tracked_message,
)

"""
Inline menu handlers.

Provides one-message navigation for user/admin panels and admin price update flow.
"""

menu_router = Router()
logger = logging.getLogger(__name__)
MENU_MEMBER_STATUSES = {"member", "administrator", "creator"}
LIBRARY_REPLY_PAGE_SIZE = 8


class MenuPriceState(StatesGroup):
    # FSM state while admin enters custom subscription price in chat.
    waiting_price = State()


class MenuExpiringSettingsState(StatesGroup):
    # FSM state while admin enters expiring-members window (days).
    waiting_days = State()


class MenuVotingSettingsState(StatesGroup):
    # FSM states while admin enters voting setup values.
    waiting_target_votes = State()
    waiting_duration_seconds = State()


class MenuAdminAddState(StatesGroup):
    # FSM state while admin shares contact to grant admin access.
    waiting_contact = State()


class MenuBroadcastState(StatesGroup):
    # FSM state while admin enters broadcast text.
    waiting_text = State()


class MenuLibraryState(StatesGroup):
    # FSM state for admin library CRUD text input.
    waiting_input = State()


class MenuLibraryBrowseState(StatesGroup):
    # FSM state for user library navigation via reply keyboard.
    waiting_action = State()


def admin_add_contact_request_keyboard() -> ReplyKeyboardMarkup:
    # Reply keyboard that opens native Telegram user/contact sharing.
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="👤 Обрати користувача",
                    request_users=KeyboardButtonRequestUsers(
                        request_id=1001,
                        user_is_bot=False,
                        max_quantity=1,
                        request_name=True,
                        request_username=True,
                    ),
                )
            ],
            [KeyboardButton(text="❌ Скасувати")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Оберіть користувача або натисніть Скасувати",
        selective=True,
    )


def parse_price_to_minor(raw_text: str) -> int | None:
    # Parse UAH text value into integer minor units (kopecks).
    normalized = raw_text.strip().replace(",", ".")
    if not normalized:
        return None
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    if amount <= 0:
        return None
    return int(
        (amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
        .to_integral_value(rounding=ROUND_HALF_UP)
    )


def parse_positive_int(raw_text: str) -> int | None:
    # Parse positive integer value.
    normalized = raw_text.strip()
    if not normalized:
        return None
    if not normalized.isdigit():
        return None
    value = int(normalized)
    if value <= 0:
        return None
    return value


def parse_non_negative_int(raw_text: str) -> int | None:
    # Parse non-negative integer value.
    normalized = raw_text.strip()
    if not normalized:
        return None
    if normalized.startswith("+"):
        normalized = normalized[1:]
    if not normalized.isdigit():
        return None
    value = int(normalized)
    if value < 0:
        return None
    return value


def parse_topic_page(raw_value: str | None, default: int = 0) -> int:
    # Parse topic list page index from callback/fsm payload.
    try:
        if raw_value is None:
            return default
        return max(int(str(raw_value).strip()), 0)
    except (TypeError, ValueError):
        return default


def parse_topic_back_payload(raw_value: str | None) -> tuple[int, int]:
    # Parse "<topic_id>:<topic_page>" payload.
    if not raw_value:
        return 0, 0
    text = str(raw_value)
    if "|" in text:
        parts = text.split("|", maxsplit=1)
    else:
        parts = text.split(":", maxsplit=1)
    if len(parts) != 2:
        return 0, parse_topic_page(parts[0], 0)
    try:
        topic_id = int(parts[0])
    except (TypeError, ValueError):
        topic_id = 0
    return max(topic_id, 0), parse_topic_page(parts[1], 0)


def split_article_payload(raw_text: str) -> tuple[str | None, str | None]:
    # Parse "title + body" from multiline input.
    cleaned = raw_text.strip()
    if not cleaned:
        return None, None
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) < 2:
        return None, None
    title = lines[0]
    body = "\n".join(lines[1:]).strip()
    if not title or not body:
        return None, None
    return title, body


def _short_library_text(value: str | None, limit: int = 3500) -> str:
    # Keep article body inside Telegram message limits.
    text = str(value or "").strip()
    if not text:
        return "Текст статті поки що відсутній."
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n..."


def _library_button_label(*, index: int, title: str) -> str:
    # Build compact numbered label for reply keyboard button.
    cleaned = " ".join(str(title or "").split()).strip() or "Без назви"
    max_len = 36
    trimmed = cleaned if len(cleaned) <= max_len else cleaned[: max_len - 3].rstrip() + "..."
    return f"{index}. {trimmed}"


async def render_library_topics_reply(
    *,
    repo: PostgresRepo,
    state: FSMContext,
    page: int,
) -> tuple[str, ReplyKeyboardMarkup]:
    # Render library topics page for reply-keyboard navigation.
    safe_page = max(page, 0)
    offset = safe_page * LIBRARY_REPLY_PAGE_SIZE
    rows = await repo.list_library_topics(
        limit=LIBRARY_REPLY_PAGE_SIZE + 1,
        offset=offset,
        include_inactive=False,
    )
    has_next = len(rows) > LIBRARY_REPLY_PAGE_SIZE
    page_rows = rows[:LIBRARY_REPLY_PAGE_SIZE]
    has_prev = safe_page > 0

    topic_buttons: dict[str, int] = {}
    topic_lines: list[str] = []
    for idx, topic in enumerate(page_rows, start=1):
        topic_id = int(topic.get("id") or 0)
        label = _library_button_label(index=offset + idx, title=str(topic.get("title") or "Тема"))
        topic_buttons[label] = topic_id
        topic_lines.append(label)

    await state.set_state(MenuLibraryBrowseState.waiting_action)
    await state.update_data(
        library_mode="topics",
        library_topic_page=safe_page,
        library_article_page=0,
        library_topic_id=0,
        library_topic_buttons=topic_buttons,
        library_article_buttons={},
    )

    if topic_lines:
        body = "\n".join(topic_lines)
    else:
        body = "Список тем поки порожній."

    text = (
        "📚 Бібліотека\n\n"
        f"{body}\n\n"
        "Оберіть тему кнопкою нижче."
    )
    keyboard = library_topics_reply_keyboard(
        topic_labels=list(topic_buttons.keys()),
        has_prev=has_prev,
        has_next=has_next,
    )
    return text, keyboard


async def render_library_articles_reply(
    *,
    repo: PostgresRepo,
    state: FSMContext,
    topic_id: int,
    topic_page: int,
    page: int,
) -> tuple[str, ReplyKeyboardMarkup]:
    # Render articles page for selected topic in reply-keyboard mode.
    topic = await repo.get_library_topic(topic_id=topic_id, include_inactive=False)
    if topic is None:
        return await render_library_topics_reply(
            repo=repo,
            state=state,
            page=max(topic_page, 0),
        )

    safe_page = max(page, 0)
    offset = safe_page * LIBRARY_REPLY_PAGE_SIZE
    rows = await repo.list_library_articles(
        topic_id=topic_id,
        limit=LIBRARY_REPLY_PAGE_SIZE + 1,
        offset=offset,
        include_inactive=False,
    )
    has_next = len(rows) > LIBRARY_REPLY_PAGE_SIZE
    page_rows = rows[:LIBRARY_REPLY_PAGE_SIZE]
    has_prev = safe_page > 0

    article_buttons: dict[str, int] = {}
    article_lines: list[str] = []
    for idx, article in enumerate(page_rows, start=1):
        article_id = int(article.get("id") or 0)
        label = _library_button_label(
            index=offset + idx,
            title=str(article.get("title") or "Стаття"),
        )
        article_buttons[label] = article_id
        article_lines.append(label)

    await state.set_state(MenuLibraryBrowseState.waiting_action)
    await state.update_data(
        library_mode="articles",
        library_topic_page=max(topic_page, 0),
        library_article_page=safe_page,
        library_topic_id=int(topic_id),
        library_topic_buttons={},
        library_article_buttons=article_buttons,
    )

    if article_lines:
        body = "\n".join(article_lines)
    else:
        body = "У цій темі ще немає статей."

    text = (
        "📚 Бібліотека\n\n"
        f"Тема: {topic.get('title')}\n\n"
        f"{body}\n\n"
        "Оберіть статтю кнопкою нижче."
    )
    keyboard = library_articles_reply_keyboard(
        article_labels=list(article_buttons.keys()),
        has_prev=has_prev,
        has_next=has_next,
    )
    return text, keyboard


async def show_library_article_reply(
    *,
    message: Message,
    repo: PostgresRepo,
    article_id: int,
) -> bool:
    # Send selected article as regular message in reply-keyboard flow.
    article = await repo.get_library_article(
        article_id=article_id,
        include_inactive=False,
    )
    if article is None:
        await message.answer(
            "⚠️ Статтю не знайдено. Оновіть список кнопками нижче.",
            parse_mode=None,
        )
        return False

    text = (
        "📚 Бібліотека\n\n"
        f"Тема: {article.get('topic_title') or 'Тема'}\n"
        f"Стаття: {article.get('title') or 'Без назви'}\n\n"
        f"{_short_library_text(article.get('content'))}"
    )
    await message.answer(text, parse_mode=None)
    return True


async def finalize_admin_grant_flow(
    *,
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
    actor_user_id: int,
    target_user_id: int,
    target_full_name: str,
    already_admin: bool,
) -> None:
    # Send result message, reset state, remove reply keyboard, and refresh admin menu.
    await state.clear()
    await message.answer(
        (
            f"✅ Користувача {target_full_name} (ID: {target_user_id}) "
            + ("вже має" if already_admin else "додано до")
            + " список адміністраторів."
        ),
        parse_mode=None,
        reply_markup=ReplyKeyboardRemove(),
    )
    try:
        screen = await render_menu_screen(
            repo=repo,
            config=config,
            bot=message.bot,
            tg_user_id=actor_user_id,
            is_admin=True,
            nav=MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_MANAGEMENT),
        )
        await send_menu_message(message=message, screen=screen)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to refresh admin management menu after admin grant. actor_user_id=%s target_user_id=%s",
            actor_user_id,
            target_user_id,
        )
        await message.answer(
            "вљ пёЏ РњРµРЅСЋ РЅРµ РѕРЅРѕРІРёР»РѕСЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РЅРѕ. Р’С–РґРєСЂРёР№С‚Рµ Р№РѕРіРѕ РєРѕРјР°РЅРґРѕСЋ /menu.",
            parse_mode=None,
            reply_markup=ReplyKeyboardRemove(),
        )
    try:
        await message.delete()
    except TelegramAPIError:
        pass


async def resolve_telegram_user_for_admin_grant(
    *,
    message: Message,
    tg_user_id: int,
) -> tuple[bool, str | None, str | None]:
    # Validate Telegram user id via Bot API and return resolved name/username.
    try:
        chat = await message.bot.get_chat(chat_id=tg_user_id)
    except TelegramBadRequest:
        return False, None, None
    except TelegramAPIError:
        return False, None, None

    first_name = getattr(chat, "first_name", None)
    last_name = getattr(chat, "last_name", None)
    username = getattr(chat, "username", None)
    full_name = " ".join(part for part in [first_name, last_name] if part).strip() or None
    return True, full_name, username


def has_active_subscription(panel_data: dict | None) -> bool:
    # Subscription is active only when ACTIVE and not expired.
    if not panel_data:
        return False
    if str(panel_data.get("subscription_status") or "") != "ACTIVE":
        return False
    expires_at = panel_data.get("subscription_expires_at")
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = expires_at.astimezone(timezone.utc)
    return expires_at > datetime.now(timezone.utc)


async def is_user_in_membership_group(
    message: Message,
    config: Config,
    tg_user_id: int,
) -> bool:
    # Check whether user is currently in membership chat.
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


async def apply_admin_price_action(
    *,
    repo: PostgresRepo,
    config: Config,
    callback_data: MenuCallbackData,
    actor_tg_user_id: int,
) -> None:
    # Persist admin subscription price action encoded in callback payload.
    if callback_data.scope != SCOPE_ADMIN:
        return
    if callback_data.view != VIEW_ADMIN_SUBSCRIPTION_PRICE:
        return

    default_minor = int(config.liqpay.amount_minor)
    current_minor = await repo.get_subscription_price_minor(default_minor=default_minor)

    action = str(callback_data.back_view or "").strip().lower()
    raw_value = int(callback_data.target_user_id)

    if action == "delta":
        delta_minor = raw_value * 100
        updated_minor = max(100, current_minor + delta_minor)
        await repo.set_subscription_price_minor(
            amount_minor=updated_minor,
            updated_by_tg_user_id=actor_tg_user_id,
        )
    elif action == "set":
        target_uah = max(raw_value, 1)
        await repo.set_subscription_price_minor(
            amount_minor=target_uah * 100,
            updated_by_tg_user_id=actor_tg_user_id,
        )


async def apply_admin_approve_action(
    *,
    repo: PostgresRepo,
    callback_data: MenuCallbackData,
) -> tuple[str, MenuCallbackData]:
    # Approve latest pending application for target user and route back to detail.
    detail_nav = MenuCallbackData(
        scope=SCOPE_ADMIN,
        view=VIEW_ADMIN_USER_DETAIL,
        page=max(callback_data.page, 0),
        target_user_id=int(callback_data.target_user_id),
        back_view=callback_data.back_view or VIEW_ADMIN_ROOT,
    )

    if callback_data.scope != SCOPE_ADMIN or callback_data.view != VIEW_ADMIN_APPROVE_PENDING:
        return "skip", detail_nav

    updated = await repo.approve_pending_application_for_user(
        tg_user_id=int(callback_data.target_user_id),
    )
    if updated is None:
        return "not_found", detail_nav

    if str(updated.get("status") or "") in {"APPROVED_AWAITING_PAYMENT", "UNLINKED_APPLICATION_APPROVED"}:
        return "approved", detail_nav

    return "not_pending", detail_nav


async def send_menu_message(
    *,
    message: Message,
    screen: MenuScreen,
) -> None:
    # Edit previously tracked menu message or send a new one.
    if message.from_user is None:
        await message.answer(
            screen.text,
            reply_markup=screen.reply_markup,
            parse_mode=None,
        )
        return

    tg_user_id = message.from_user.id
    tracked = get_tracked_message(MENU_STATE_MENU, tg_user_id)
    if tracked is not None:
        try:
            await message.bot.edit_message_text(
                chat_id=tracked.chat_id,
                message_id=tracked.message_id,
                text=screen.text,
                reply_markup=screen.reply_markup,
                parse_mode=None,
            )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in (exc.message or "").lower():
                return
        except TelegramAPIError:
            pass
        forget_tracked_message(MENU_STATE_MENU, tg_user_id)

    await clear_tracked_keyboard(
        bot=message.bot,
        state=MENU_STATE_MENU,
        tg_user_id=tg_user_id,
    )
    sent = await message.answer(
        screen.text,
        reply_markup=screen.reply_markup,
        parse_mode=None,
    )
    remember_tracked_message(
        state=MENU_STATE_MENU,
        tg_user_id=tg_user_id,
        chat_id=sent.chat.id,
        message_id=sent.message_id,
    )


async def edit_or_fallback_menu(
    *,
    query: CallbackQuery,
    screen: MenuScreen,
) -> None:
    # Handle callback-driven menu edit with safe fallback to new message.
    if query.message is None:
        if query.from_user is None:
            return
        await clear_tracked_keyboard(
            bot=query.bot,
            state=MENU_STATE_MENU,
            tg_user_id=query.from_user.id,
        )
        sent = await query.bot.send_message(
            chat_id=query.from_user.id,
            text=screen.text,
            reply_markup=screen.reply_markup,
            parse_mode=None,
        )
        remember_tracked_message(
            state=MENU_STATE_MENU,
            tg_user_id=query.from_user.id,
            chat_id=sent.chat.id,
            message_id=sent.message_id,
        )
        return

    try:
        await query.message.edit_text(
            screen.text,
            reply_markup=screen.reply_markup,
            parse_mode=None,
        )
        if query.from_user is not None:
            remember_tracked_message(
                state=MENU_STATE_MENU,
                tg_user_id=query.from_user.id,
                chat_id=query.message.chat.id,
                message_id=query.message.message_id,
            )
        return
    except TelegramBadRequest as exc:
        if "message is not modified" in (exc.message or "").lower():
            return
        logger.warning("Menu edit failed, fallback to new message. error=%s", exc.message)
    except TelegramForbiddenError:
        return
    except TelegramAPIError:
        logger.exception("Telegram API error during menu edit")

    if query.from_user is None:
        return
    try:
        await clear_tracked_keyboard(
            bot=query.bot,
            state=MENU_STATE_MENU,
            tg_user_id=query.from_user.id,
        )
        sent = await query.bot.send_message(
            chat_id=query.from_user.id,
            text=screen.text,
            reply_markup=screen.reply_markup,
            parse_mode=None,
        )
        remember_tracked_message(
            state=MENU_STATE_MENU,
            tg_user_id=query.from_user.id,
            chat_id=sent.chat.id,
            message_id=sent.message_id,
        )
    except TelegramForbiddenError:
        return
    except TelegramAPIError:
        logger.exception("Failed to send fallback menu message")


@menu_router.message(Command("menu"))
async def open_menu_command(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Open root inline menu for user/admin via /menu shortcut.
    if message.from_user is None:
        await message.answer("⚠️ Не вдалося визначити користувача.")
        return
    await clear_tracked_keyboard(
        bot=message.bot,
        state=MENU_STATE_ENTRY,
        tg_user_id=message.from_user.id,
    )
    await clear_tracked_keyboard(
        bot=message.bot,
        state=MENU_STATE_ACTION,
        tg_user_id=message.from_user.id,
    )
    await state.clear()

    await repo.create_or_update_user(
        tg_user_id=message.from_user.id,
        full_name=message.from_user.full_name or "Невідомо",
        username=message.from_user.username,
        language_code=message.from_user.language_code,
    )
    admin_mode = await is_admin_user(
        repo=repo,
        config=config,
        tg_user_id=message.from_user.id,
    )
    if not admin_mode:
        panel_data = await repo.get_user_panel_data(tg_user_id=message.from_user.id)
        if is_user_blocked(panel_data):
            await message.answer("⛔️ Доступ до бота обмежено адміністратором.")
            return
        in_group = await is_user_in_membership_group(
            message=message,
            config=config,
            tg_user_id=message.from_user.id,
        )
        if in_group:
            await repo.activate_membership_from_group_entry(
                tg_user_id=message.from_user.id,
                full_name=message.from_user.full_name,
                username=message.from_user.username,
                language_code=message.from_user.language_code,
            )
            panel_data = await repo.get_user_panel_data(tg_user_id=message.from_user.id)

        is_payment_exempt = await has_payment_exemption(
            bot=message.bot,
            config=config,
            tg_user_id=message.from_user.id,
            repo=repo,
        )
        if not has_active_subscription(panel_data) and not is_payment_exempt:
            await message.answer(
                "🔒 Меню стане доступним після активації членства."
            )
            return
        if not in_group:
            await message.answer(
                "🔒 Меню стане доступним після вступу до групи.\n"
                "Надішліть /start і натисніть «Отримати доступ до групи»."
            )
            return

    nav = (
        MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_ROOT)
        if admin_mode
        else MenuCallbackData(scope=SCOPE_USER, view=VIEW_USER_ROOT)
    )
    screen = await render_menu_screen(
        repo=repo,
        config=config,
        bot=message.bot,
        tg_user_id=message.from_user.id,
        is_admin=admin_mode,
        nav=nav,
    )
    await send_menu_message(message=message, screen=screen)


@menu_router.message(AdminFilter(), MenuAdminAddState.waiting_contact, Command("menu"))
async def open_menu_command_from_admin_add_state(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Ensure /menu works while add-admin flow FSM is active.
    await state.clear()
    await message.answer(
        "📋 Відкриваю меню.",
        parse_mode=None,
        reply_markup=ReplyKeyboardRemove(),
    )
    await open_menu_command(
        message=message,
        state=state,
        repo=repo,
        config=config,
    )


@menu_router.message(AdminFilter(), MenuBroadcastState.waiting_text, Command("menu"))
async def open_menu_command_from_broadcast_state(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Ensure /menu works while broadcast FSM is active.
    await state.clear()
    await message.answer(
        "📋 Відкриваю меню.",
        parse_mode=None,
    )
    await open_menu_command(
        message=message,
        state=state,
        repo=repo,
        config=config,
    )


@menu_router.message(AdminFilter(), MenuLibraryState.waiting_input, Command("menu"))
async def open_menu_command_from_library_state(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Ensure /menu works while library edit FSM is active.
    await state.clear()
    await message.answer(
        "рџ“‹ Р’С–РґРєСЂРёРІР°СЋ РјРµРЅСЋ.",
        parse_mode=None,
        reply_markup=ReplyKeyboardRemove(),
    )
    await open_menu_command(
        message=message,
        state=state,
        repo=repo,
        config=config,
    )


@menu_router.message(AdminFilter(), MenuBroadcastState.waiting_text)
async def apply_admin_broadcast(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Receive admin broadcast text and send it to all users from DB.
    if message.from_user is None:
        await state.clear()
        return

    text = (message.text or "").strip()
    if text.lower() in {"❌ скасувати", "скасувати", "cancel", "/cancel"}:
        await state.clear()
        nav = MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_BROADCAST)
        screen = await render_menu_screen(
            repo=repo,
            config=config,
            bot=message.bot,
            tg_user_id=message.from_user.id,
            is_admin=True,
            nav=nav,
        )
        await send_menu_message(message=message, screen=screen)
        return

    if not text:
        await message.answer(
            "⚠️ Надішліть текст для розсилки одним повідомленням.",
            parse_mode=None,
        )
        return

    recipients = await repo.list_broadcast_user_ids()
    if not recipients:
        await state.clear()
        await message.answer(
            "ℹ️ Немає користувачів для розсилки.",
            parse_mode=None,
        )
        return

    delivered = await broadcaster.broadcast(
        bot=message.bot,
        users=recipients,
        text=text,
    )
    failed = max(len(recipients) - int(delivered), 0)
    await state.clear()

    await message.answer(
        (
            "✅ Розсилку завершено.\n"
            f"Отримали: {delivered}\n"
            f"Помилки/недоставлено: {failed}"
        ),
        parse_mode=None,
    )

    nav = MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_MANAGEMENT)
    screen = await render_menu_screen(
        repo=repo,
        config=config,
        bot=message.bot,
        tg_user_id=message.from_user.id,
        is_admin=True,
        nav=nav,
    )
    await send_menu_message(message=message, screen=screen)


@menu_router.message(AdminFilter(), MenuPriceState.waiting_price)
async def apply_custom_subscription_price(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Receive new subscription price from admin text input.
    if message.from_user is None:
        await state.clear()
        return

    amount_minor = parse_price_to_minor(message.text or "")
    if amount_minor is not None:
        await repo.set_subscription_price_minor(
            amount_minor=amount_minor,
            updated_by_tg_user_id=message.from_user.id,
        )
        await state.clear()
        nav = MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_SUBSCRIPTION_PRICE,
        )
    else:
        nav = MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_SUBSCRIPTION_PRICE,
            back_view="custom",
        )

    screen = await render_menu_screen(
        repo=repo,
        config=config,
        bot=message.bot,
        tg_user_id=message.from_user.id,
        is_admin=True,
        nav=nav,
    )
    await send_menu_message(message=message, screen=screen)

    try:
        await message.delete()
    except TelegramAPIError:
        pass


@menu_router.message(AdminFilter(), MenuExpiringSettingsState.waiting_days)
async def apply_custom_expiring_window_days(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Receive expiring-members window (days) from admin text input.
    if message.from_user is None:
        await state.clear()
        return

    parsed_days = parse_positive_int(message.text or "")
    if parsed_days is not None:
        await repo.set_expiring_window_days(
            days=parsed_days,
            updated_by_tg_user_id=message.from_user.id,
        )
        await state.clear()
        nav = MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_EXPIRING_SETTINGS,
        )
    else:
        nav = MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_EXPIRING_SETTINGS,
            back_view="custom",
        )

    screen = await render_menu_screen(
        repo=repo,
        config=config,
        bot=message.bot,
        tg_user_id=message.from_user.id,
        is_admin=True,
        nav=nav,
    )
    await send_menu_message(message=message, screen=screen)

    try:
        await message.delete()
    except TelegramAPIError:
        pass


@menu_router.message(AdminFilter(), MenuVotingSettingsState.waiting_target_votes)
async def apply_custom_vote_target(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Receive required target votes for yes/no from admin text input.
    if message.from_user is None:
        await state.clear()
        return

    parsed_target = parse_positive_int(message.text or "")
    if parsed_target is not None:
        await repo.set_vote_min_total(
            target=parsed_target,
            updated_by_tg_user_id=message.from_user.id,
        )
        await state.clear()
        nav = MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_VOTING_SETTINGS,
        )
    else:
        nav = MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_VOTING_SETTINGS,
            back_view="custom_target",
        )

    screen = await render_menu_screen(
        repo=repo,
        config=config,
        bot=message.bot,
        tg_user_id=message.from_user.id,
        is_admin=True,
        nav=nav,
    )
    await send_menu_message(message=message, screen=screen)

    try:
        await message.delete()
    except TelegramAPIError:
        pass


@menu_router.message(AdminFilter(), MenuVotingSettingsState.waiting_duration_seconds)
async def apply_custom_vote_duration_seconds(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Receive vote duration in seconds from admin text input.
    if message.from_user is None:
        await state.clear()
        return

    parsed_duration = parse_non_negative_int(message.text or "")
    if parsed_duration is not None:
        await repo.set_vote_duration_seconds(
            seconds=parsed_duration,
            updated_by_tg_user_id=message.from_user.id,
        )
        await state.clear()
        nav = MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_VOTING_SETTINGS,
        )
    else:
        nav = MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_VOTING_SETTINGS,
            back_view="custom_duration",
        )

    screen = await render_menu_screen(
        repo=repo,
        config=config,
        bot=message.bot,
        tg_user_id=message.from_user.id,
        is_admin=True,
        nav=nav,
    )
    await send_menu_message(message=message, screen=screen)

    try:
        await message.delete()
    except TelegramAPIError:
        pass


@menu_router.message(AdminFilter(), MenuAdminAddState.waiting_contact, F.text.regexp(r"^\d+$"))
async def apply_admin_id_grant(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Allow admin grant by pasted Telegram ID (e.g. value from /chatid).
    if message.from_user is None:
        await state.clear()
        return

    raw_id = (message.text or "").strip()
    if not raw_id.isdigit():
        return

    target_user_id = int(raw_id)
    if target_user_id <= 0:
        await message.answer(
            "⚠️ Невірний Telegram ID. Спробуйте ще раз.",
            parse_mode=None,
        )
        return

    is_valid_user, resolved_full_name, resolved_username = await resolve_telegram_user_for_admin_grant(
        message=message,
        tg_user_id=target_user_id,
    )
    if not is_valid_user:
        await message.answer(
            (
                "⚠️ Користувача з таким Telegram ID не знайдено.\n"
                "Перевірте ID або попросіть користувача спочатку запустити бота і надіслати /chatid."
            ),
            parse_mode=None,
        )
        return

    existing_user = await repo.get_user_by_tg_user_id(target_user_id)
    target_full_name = (
        str(resolved_full_name or "").strip()
        or (
            str(existing_user.get("full_name") or "").strip()
            if existing_user
            else ""
        )
        or f"User {target_user_id}"
    )

    target_username = (
        str(resolved_username or "").strip()
        or (
            str(existing_user.get("username") or "").strip()
            if existing_user
            else ""
        )
        or None
    )

    already_admin = await is_admin_user(
        repo=repo,
        config=config,
        tg_user_id=target_user_id,
    )
    if not already_admin:
        try:
            await repo.grant_bot_admin(
                tg_user_id=target_user_id,
                full_name=target_full_name,
                username=target_username,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to grant admin by id. actor_user_id=%s target_user_id=%s",
                message.from_user.id,
                target_user_id,
            )
            await state.clear()
            await message.answer(
                "вљ пёЏ РќРµ РІРґР°Р»РѕСЃСЏ РґРѕРґР°С‚Рё Р°РґРјС–РЅС–СЃС‚СЂР°С‚РѕСЂР°. РЎРїСЂРѕР±СѓР№С‚Рµ С‰Рµ СЂР°Р·.",
                parse_mode=None,
                reply_markup=ReplyKeyboardRemove(),
            )
            return

    await finalize_admin_grant_flow(
        message=message,
        state=state,
        repo=repo,
        config=config,
        actor_user_id=message.from_user.id,
        target_user_id=target_user_id,
        target_full_name=target_full_name,
        already_admin=already_admin,
    )


@menu_router.message(AdminFilter(), MenuAdminAddState.waiting_contact, F.users_shared)
async def apply_admin_shared_user_grant(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Grant admin from Telegram native "request_users" sharing.
    if message.from_user is None:
        await state.clear()
        return

    users_shared = message.users_shared
    if users_shared is None or not users_shared.users:
        await message.answer(
            "⚠️ Не вдалося отримати користувача. Спробуйте ще раз.",
            parse_mode=None,
        )
        return

    shared_user = users_shared.users[0]
    target_user_id = int(shared_user.user_id)
    existing_user = await repo.get_user_by_tg_user_id(target_user_id)
    shared_full_name = " ".join(
        part for part in [shared_user.first_name, shared_user.last_name] if part
    ).strip()
    target_full_name = (
        shared_full_name
        or (
            str(existing_user.get("full_name") or "").strip()
            if existing_user
            else ""
        )
        or f"User {target_user_id}"
    )

    already_admin = await is_admin_user(
        repo=repo,
        config=config,
        tg_user_id=target_user_id,
    )
    if not already_admin:
        try:
            await repo.grant_bot_admin(
                tg_user_id=target_user_id,
                full_name=target_full_name,
                username=shared_user.username,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to grant admin by shared user. actor_user_id=%s target_user_id=%s",
                message.from_user.id,
                target_user_id,
            )
            await state.clear()
            await message.answer(
                "вљ пёЏ РќРµ РІРґР°Р»РѕСЃСЏ РґРѕРґР°С‚Рё Р°РґРјС–РЅС–СЃС‚СЂР°С‚РѕСЂР°. РЎРїСЂРѕР±СѓР№С‚Рµ С‰Рµ СЂР°Р·.",
                parse_mode=None,
                reply_markup=ReplyKeyboardRemove(),
            )
            return

    await finalize_admin_grant_flow(
        message=message,
        state=state,
        repo=repo,
        config=config,
        actor_user_id=message.from_user.id,
        target_user_id=target_user_id,
        target_full_name=target_full_name,
        already_admin=already_admin,
    )


@menu_router.message(AdminFilter(), MenuAdminAddState.waiting_contact)
async def apply_admin_contact_grant(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Receive contact and grant runtime admin access to that Telegram user.
    if message.from_user is None:
        await state.clear()
        return

    normalized_text = (message.text or "").strip().lower()
    if normalized_text in {"/menu", "menu", "меню"}:
        await state.clear()
        await message.answer(
            "📋 Відкриваю меню.",
            parse_mode=None,
            reply_markup=ReplyKeyboardRemove(),
        )
        await open_menu_command(
            message=message,
            state=state,
            repo=repo,
            config=config,
        )
        return
    if normalized_text in {"❌ скасувати", "скасувати", "cancel", "/cancel"}:
        await state.clear()
        await message.answer(
            "✅ Додавання адміністратора скасовано.",
            parse_mode=None,
            reply_markup=ReplyKeyboardRemove(),
        )
        try:
            screen = await render_menu_screen(
                repo=repo,
                config=config,
                bot=message.bot,
                tg_user_id=message.from_user.id,
                is_admin=True,
                nav=MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_MANAGEMENT),
            )
            await send_menu_message(message=message, screen=screen)
        except Exception:
            logger.exception("Failed to refresh admin menu after cancel admin-add flow")
            await message.answer(
                "Відкрийте меню ще раз кнопкою /menu.",
                parse_mode=None,
                reply_markup=ReplyKeyboardRemove(),
            )
        return

    contact = message.contact
    if contact is None:
        await message.answer(
            (
                "Надішліть контакт користувача або Telegram ID (значення з /chatid).\n"
                "Поверніться назад кнопкою в меню, якщо потрібно скасувати."
            ),
            parse_mode=None,
        )
        return

    contact_user_id = contact.user_id
    if contact_user_id is None:
        await message.answer(
            (
                "⚠️ Для цього контакту не передано Telegram ID.\n"
                "Попросіть користувача відкрити бота й надіслати команду /chatid."
            ),
            parse_mode=None,
        )
        return

    target_full_name = " ".join(
        part for part in [contact.first_name, contact.last_name] if part
    ).strip() or f"User {int(contact_user_id)}"

    already_admin = await is_admin_user(
        repo=repo,
        config=config,
        tg_user_id=int(contact_user_id),
    )
    if not already_admin:
        try:
            await repo.grant_bot_admin(
                tg_user_id=int(contact_user_id),
                full_name=target_full_name,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to grant admin by contact. actor_user_id=%s target_user_id=%s",
                message.from_user.id,
                int(contact_user_id),
            )
            await state.clear()
            await message.answer(
                "вљ пёЏ РќРµ РІРґР°Р»РѕСЃСЏ РґРѕРґР°С‚Рё Р°РґРјС–РЅС–СЃС‚СЂР°С‚РѕСЂР°. РЎРїСЂРѕР±СѓР№С‚Рµ С‰Рµ СЂР°Р·.",
                parse_mode=None,
                reply_markup=ReplyKeyboardRemove(),
            )
            return
    await finalize_admin_grant_flow(
        message=message,
        state=state,
        repo=repo,
        config=config,
        actor_user_id=message.from_user.id,
        target_user_id=int(contact_user_id),
        target_full_name=target_full_name,
        already_admin=already_admin,
    )


@menu_router.message(AdminFilter(), MenuLibraryState.waiting_input)
async def apply_library_input(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Handle admin library CRUD text input.
    if message.from_user is None:
        await state.clear()
        return

    data = await state.get_data()
    action = str(data.get("library_action") or "").strip().lower()
    topic_id = int(data.get("library_topic_id") or 0)
    article_id = int(data.get("library_article_id") or 0)
    topic_page = parse_topic_page(str(data.get("library_topic_page") or "0"), 0)
    article_page = parse_topic_page(str(data.get("library_article_page") or "0"), 0)
    text = (message.text or "").strip()
    lowered = text.lower()

    if lowered in {"вќЊ СЃРєР°СЃСѓРІР°С‚Рё", "СЃРєР°СЃСѓРІР°С‚Рё", "cancel", "/cancel"}:
        await state.clear()
        nav = MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_LIBRARY_TOPICS, page=topic_page)
        screen = await render_menu_screen(
            repo=repo,
            config=config,
            bot=message.bot,
            tg_user_id=message.from_user.id,
            is_admin=True,
            nav=nav,
        )
        await send_menu_message(message=message, screen=screen)
        return

    try:
        if action == "topic_add":
            if not text:
                await message.answer("вљ пёЏ Р’РІРµРґС–С‚СЊ РЅР°Р·РІСѓ С‚РµРјРё.", parse_mode=None)
                return
            await repo.create_library_topic(title=text)
            nav = MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_LIBRARY_TOPICS, page=topic_page)
            success_text = "вњ… РўРµРјСѓ РґРѕРґР°РЅРѕ."
        elif action == "topic_edit":
            if topic_id <= 0:
                await message.answer("вљ пёЏ РўРµРјСѓ РЅРµ Р·РЅР°Р№РґРµРЅРѕ.", parse_mode=None)
                return
            if not text:
                await message.answer("вљ пёЏ Р’РІРµРґС–С‚СЊ РЅРѕРІСѓ РЅР°Р·РІСѓ С‚РµРјРё.", parse_mode=None)
                return
            await repo.update_library_topic(topic_id=topic_id, title=text)
            nav = MenuCallbackData(
                scope=SCOPE_ADMIN,
                view=VIEW_ADMIN_LIBRARY_ARTICLES,
                target_user_id=topic_id,
                page=article_page,
                back_view=str(topic_page),
            )
            success_text = "вњ… РќР°Р·РІСѓ С‚РµРјРё РѕРЅРѕРІР»РµРЅРѕ."
        elif action == "article_add":
            if topic_id <= 0:
                await message.answer("вљ пёЏ РўРµРјСѓ РЅРµ Р·РЅР°Р№РґРµРЅРѕ.", parse_mode=None)
                return
            title, content = split_article_payload(text)
            if title is None or content is None:
                await message.answer(
                    (
                        "вљ пёЏ РќР°РґС–С€Р»С–С‚СЊ СЃС‚Р°С‚С‚СЋ Сѓ С„РѕСЂРјР°С‚С–:\n"
                        "1 СЂСЏРґРѕРє вЂ” Р·Р°РіРѕР»РѕРІРѕРє\n"
                        "Р· 2 СЂСЏРґРєР° вЂ” С‚РµРєСЃС‚ СЃС‚Р°С‚С‚С–"
                    ),
                    parse_mode=None,
                )
                return
            await repo.create_library_article(topic_id=topic_id, title=title, content=content)
            nav = MenuCallbackData(
                scope=SCOPE_ADMIN,
                view=VIEW_ADMIN_LIBRARY_ARTICLES,
                target_user_id=topic_id,
                page=article_page,
                back_view=str(topic_page),
            )
            success_text = "вњ… РЎС‚Р°С‚С‚СЋ РґРѕРґР°РЅРѕ."
        elif action == "article_edit":
            if article_id <= 0:
                await message.answer("вљ пёЏ РЎС‚Р°С‚С‚СЋ РЅРµ Р·РЅР°Р№РґРµРЅРѕ.", parse_mode=None)
                return
            title, content = split_article_payload(text)
            if title is None or content is None:
                await message.answer(
                    (
                        "вљ пёЏ РќР°РґС–С€Р»С–С‚СЊ РѕРЅРѕРІР»РµРЅСѓ СЃС‚Р°С‚С‚СЋ Сѓ С„РѕСЂРјР°С‚С–:\n"
                        "1 СЂСЏРґРѕРє вЂ” Р·Р°РіРѕР»РѕРІРѕРє\n"
                        "Р· 2 СЂСЏРґРєР° вЂ” С‚РµРєСЃС‚ СЃС‚Р°С‚С‚С–"
                    ),
                    parse_mode=None,
                )
                return
            await repo.update_library_article(article_id=article_id, title=title, content=content)
            nav = MenuCallbackData(
                scope=SCOPE_ADMIN,
                view=VIEW_ADMIN_LIBRARY_ARTICLE,
                target_user_id=article_id,
                page=article_page,
                back_view=f"{topic_id}|{topic_page}",
            )
            success_text = "вњ… РЎС‚Р°С‚С‚СЋ РѕРЅРѕРІР»РµРЅРѕ."
        else:
            await state.clear()
            nav = MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_LIBRARY_TOPICS, page=topic_page)
            success_text = ""
    except Exception:
        logger.exception(
            "Library input action failed. action=%s topic_id=%s article_id=%s",
            action,
            topic_id,
            article_id,
        )
        await message.answer("вљ пёЏ РќРµ РІРґР°Р»РѕСЃСЏ Р·Р±РµСЂРµРіС‚Рё Р·РјС–РЅРё. РЎРїСЂРѕР±СѓР№С‚Рµ С‰Рµ СЂР°Р·.", parse_mode=None)
        return

    await state.clear()
    if success_text:
        await message.answer(success_text, parse_mode=None)

    screen = await render_menu_screen(
        repo=repo,
        config=config,
        bot=message.bot,
        tg_user_id=message.from_user.id,
        is_admin=True,
        nav=nav,
    )
    await send_menu_message(message=message, screen=screen)

    try:
        await message.delete()
    except TelegramAPIError:
        pass


@menu_router.message(MenuLibraryBrowseState.waiting_action)
async def navigate_library_reply_menu(
    message: Message,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Handle user library navigation via reply keyboard buttons.
    if message.from_user is None:
        await state.clear()
        return

    text = (message.text or "").strip()
    lowered_text = text.lower()
    if lowered_text in {"/menu", "menu", "меню"}:
        await state.clear()
        await message.answer(
            "📋 Відкриваю меню.",
            parse_mode=None,
            reply_markup=ReplyKeyboardRemove(),
        )
        await open_menu_command(
            message=message,
            state=state,
            repo=repo,
            config=config,
        )
        return

    data = await state.get_data()
    mode = str(data.get("library_mode") or "topics").strip().lower()
    topic_page = parse_topic_page(str(data.get("library_topic_page") or "0"), 0)
    article_page = parse_topic_page(str(data.get("library_article_page") or "0"), 0)
    topic_id = int(data.get("library_topic_id") or 0)
    topic_buttons = {
        str(key): int(value)
        for key, value in (data.get("library_topic_buttons") or {}).items()
    }
    article_buttons = {
        str(key): int(value)
        for key, value in (data.get("library_article_buttons") or {}).items()
    }

    if text == LIBRARY_REPLY_BACK_TO_MENU:
        await state.clear()
        await message.answer(
            "📋 Повертаю до меню.",
            parse_mode=None,
            reply_markup=ReplyKeyboardRemove(),
        )
        admin_mode = await is_admin_user(
            repo=repo,
            config=config,
            tg_user_id=message.from_user.id,
        )
        nav = (
            MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_ROOT)
            if admin_mode
            else MenuCallbackData(scope=SCOPE_USER, view=VIEW_USER_ROOT)
        )
        screen = await render_menu_screen(
            repo=repo,
            config=config,
            bot=message.bot,
            tg_user_id=message.from_user.id,
            is_admin=admin_mode,
            nav=nav,
        )
        await send_menu_message(message=message, screen=screen)
        return

    if mode == "topics":
        if text == LIBRARY_REPLY_PREV_PAGE:
            next_page = max(topic_page - 1, 0)
            screen_text, markup = await render_library_topics_reply(
                repo=repo,
                state=state,
                page=next_page,
            )
            await message.answer(screen_text, parse_mode=None, reply_markup=markup)
            return
        if text == LIBRARY_REPLY_NEXT_PAGE:
            next_page = topic_page + 1
            screen_text, markup = await render_library_topics_reply(
                repo=repo,
                state=state,
                page=next_page,
            )
            await message.answer(screen_text, parse_mode=None, reply_markup=markup)
            return

        selected_topic_id = topic_buttons.get(text)
        if selected_topic_id is None:
            await message.answer(
                "ℹ️ Оберіть тему кнопкою з клавіатури нижче.",
                parse_mode=None,
            )
            return

        screen_text, markup = await render_library_articles_reply(
            repo=repo,
            state=state,
            topic_id=int(selected_topic_id),
            topic_page=topic_page,
            page=0,
        )
        await message.answer(screen_text, parse_mode=None, reply_markup=markup)
        return

    if text == LIBRARY_REPLY_BACK_TO_TOPICS:
        screen_text, markup = await render_library_topics_reply(
            repo=repo,
            state=state,
            page=topic_page,
        )
        await message.answer(screen_text, parse_mode=None, reply_markup=markup)
        return
    if text == LIBRARY_REPLY_PREV_PAGE:
        next_page = max(article_page - 1, 0)
        screen_text, markup = await render_library_articles_reply(
            repo=repo,
            state=state,
            topic_id=topic_id,
            topic_page=topic_page,
            page=next_page,
        )
        await message.answer(screen_text, parse_mode=None, reply_markup=markup)
        return
    if text == LIBRARY_REPLY_NEXT_PAGE:
        next_page = article_page + 1
        screen_text, markup = await render_library_articles_reply(
            repo=repo,
            state=state,
            topic_id=topic_id,
            topic_page=topic_page,
            page=next_page,
        )
        await message.answer(screen_text, parse_mode=None, reply_markup=markup)
        return

    selected_article_id = article_buttons.get(text)
    if selected_article_id is None:
        await message.answer(
            "ℹ️ Оберіть статтю кнопкою з клавіатури нижче.",
            parse_mode=None,
        )
        return

    await show_library_article_reply(
        message=message,
        repo=repo,
        article_id=int(selected_article_id),
    )


@menu_router.callback_query(MenuCallbackData.filter())
async def navigate_menu(
    query: CallbackQuery,
    callback_data: MenuCallbackData,
    state: FSMContext,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Route inline menu callback to renderer and update current menu message.
    if query.from_user is None:
        return

    admin_mode = await is_admin_user(
        repo=repo,
        config=config,
        tg_user_id=query.from_user.id,
    )
    if not admin_mode:
        panel_data = await repo.get_user_panel_data(tg_user_id=query.from_user.id)
        if is_user_blocked(panel_data):
            await query.answer("⛔️ Доступ до бота обмежено адміністратором.", show_alert=True)
            return

    action_result = "skip"
    nav_after_action = callback_data

    is_custom_price_input_request = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_SUBSCRIPTION_PRICE
        and str(callback_data.back_view or "").strip().lower() == "custom"
    )
    is_custom_expiring_days_request = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_EXPIRING_SETTINGS
        and str(callback_data.back_view or "").strip().lower() == "custom"
    )
    is_custom_vote_target_request = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_VOTING_SETTINGS
        and str(callback_data.back_view or "").strip().lower() == "custom_target"
    )
    is_custom_vote_duration_request = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_VOTING_SETTINGS
        and str(callback_data.back_view or "").strip().lower() == "custom_duration"
    )
    admin_add_mode = str(callback_data.back_view or "").strip().lower()
    is_admin_add_mode_request = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_ADD_ADMIN
        and admin_add_mode in {"contact", "id"}
    )
    is_admin_broadcast_compose_request = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_BROADCAST
        and str(callback_data.back_view or "").strip().lower() == "compose"
    )
    is_library_topic_add_request = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_LIBRARY_ADD_TOPIC
    )
    is_library_topic_edit_request = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_LIBRARY_EDIT_TOPIC
    )
    is_library_article_add_request = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_LIBRARY_ADD_ARTICLE
    )
    is_library_article_edit_request = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_LIBRARY_EDIT_ARTICLE
    )
    is_library_topic_delete_action = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_LIBRARY_DELETE_TOPIC
    )
    is_library_article_delete_action = (
        admin_mode
        and callback_data.scope == SCOPE_ADMIN
        and callback_data.view == VIEW_ADMIN_LIBRARY_DELETE_ARTICLE
    )

    if (
        is_custom_price_input_request
        or is_custom_expiring_days_request
        or is_custom_vote_target_request
        or is_custom_vote_duration_request
        or is_admin_add_mode_request
        or is_admin_broadcast_compose_request
        or is_library_topic_add_request
        or is_library_topic_edit_request
        or is_library_article_add_request
        or is_library_article_edit_request
    ):
        if is_custom_price_input_request:
            await state.set_state(MenuPriceState.waiting_price)
        elif is_custom_expiring_days_request:
            await state.set_state(MenuExpiringSettingsState.waiting_days)
        elif is_custom_vote_target_request:
            await state.set_state(MenuVotingSettingsState.waiting_target_votes)
        elif is_custom_vote_duration_request:
            await state.set_state(MenuVotingSettingsState.waiting_duration_seconds)
        elif is_admin_broadcast_compose_request:
            await state.set_state(MenuBroadcastState.waiting_text)
            try:
                await query.bot.send_message(
                    chat_id=query.from_user.id,
                    text="✍️ Надішліть текст повідомлення для розсилки одним повідомленням.",
                    parse_mode=None,
                )
            except TelegramAPIError:
                logger.exception("Failed to send broadcast compose prompt")
        elif is_admin_add_mode_request:
            await state.set_state(MenuAdminAddState.waiting_contact)
            nav_after_action = callback_data
            if admin_add_mode == "contact":
                try:
                    await query.bot.send_message(
                        chat_id=query.from_user.id,
                        text="Натисніть кнопку нижче, щоб поділитися контактом або обрати користувача.",
                        reply_markup=admin_add_contact_request_keyboard(),
                        parse_mode=None,
                    )
                except TelegramAPIError:
                    logger.exception("Failed to send contact request keyboard")
            elif admin_add_mode == "id":
                try:
                    await query.bot.send_message(
                        chat_id=query.from_user.id,
                        text="Надішліть Telegram ID користувача одним повідомленням. Для перегляду ID використайте /chatid.",
                        reply_markup=ReplyKeyboardRemove(),
                        parse_mode=None,
                    )
                except TelegramAPIError:
                    logger.exception("Failed to send admin-id prompt message")
        elif is_library_topic_add_request:
            await state.set_state(MenuLibraryState.waiting_input)
            topic_page = max(callback_data.page, 0)
            await state.update_data(
                library_action="topic_add",
                library_topic_page=topic_page,
                library_article_page=0,
                library_topic_id=0,
                library_article_id=0,
            )
            nav_after_action = MenuCallbackData(
                scope=SCOPE_ADMIN,
                view=VIEW_ADMIN_LIBRARY_TOPICS,
                page=topic_page,
            )
            try:
                await query.bot.send_message(
                    chat_id=query.from_user.id,
                    text="✍️ Надішліть назву нової теми одним повідомленням.",
                    parse_mode=None,
                )
            except TelegramAPIError:
                logger.exception("Failed to send library topic-add prompt")
        elif is_library_topic_edit_request:
            await state.set_state(MenuLibraryState.waiting_input)
            topic_id = int(callback_data.target_user_id)
            topic_page = parse_topic_page(callback_data.back_view, 0)
            article_page = max(callback_data.page, 0)
            await state.update_data(
                library_action="topic_edit",
                library_topic_id=topic_id,
                library_topic_page=topic_page,
                library_article_page=article_page,
                library_article_id=0,
            )
            nav_after_action = MenuCallbackData(
                scope=SCOPE_ADMIN,
                view=VIEW_ADMIN_LIBRARY_ARTICLES,
                target_user_id=topic_id,
                page=article_page,
                back_view=str(topic_page),
            )
            try:
                await query.bot.send_message(
                    chat_id=query.from_user.id,
                    text="✍️ Надішліть нову назву теми одним повідомленням.",
                    parse_mode=None,
                )
            except TelegramAPIError:
                logger.exception("Failed to send library topic-edit prompt")
        elif is_library_article_add_request:
            await state.set_state(MenuLibraryState.waiting_input)
            topic_id = int(callback_data.target_user_id)
            topic_page = parse_topic_page(callback_data.back_view, 0)
            article_page = max(callback_data.page, 0)
            await state.update_data(
                library_action="article_add",
                library_topic_id=topic_id,
                library_topic_page=topic_page,
                library_article_page=article_page,
                library_article_id=0,
            )
            nav_after_action = MenuCallbackData(
                scope=SCOPE_ADMIN,
                view=VIEW_ADMIN_LIBRARY_ARTICLES,
                target_user_id=topic_id,
                page=article_page,
                back_view=str(topic_page),
            )
            try:
                await query.bot.send_message(
                    chat_id=query.from_user.id,
                    text=(
                        "✍️ Надішліть статтю одним повідомленням у форматі:\n"
                        "1 рядок - заголовок\n"
                        "з 2 рядка - текст статті"
                    ),
                    parse_mode=None,
                )
            except TelegramAPIError:
                logger.exception("Failed to send library article-add prompt")
        elif is_library_article_edit_request:
            await state.set_state(MenuLibraryState.waiting_input)
            article_id = int(callback_data.target_user_id)
            topic_id, topic_page = parse_topic_back_payload(callback_data.back_view)
            if topic_id <= 0:
                article = await repo.get_library_article(article_id=article_id, include_inactive=True)
                if article is not None:
                    topic_id = int(article.get("topic_id") or 0)
            article_page = max(callback_data.page, 0)
            await state.update_data(
                library_action="article_edit",
                library_topic_id=topic_id,
                library_topic_page=topic_page,
                library_article_page=article_page,
                library_article_id=article_id,
            )
            nav_after_action = MenuCallbackData(
                scope=SCOPE_ADMIN,
                view=VIEW_ADMIN_LIBRARY_ARTICLE,
                target_user_id=article_id,
                page=article_page,
                back_view=f"{topic_id}|{topic_page}",
            )
            try:
                await query.bot.send_message(
                    chat_id=query.from_user.id,
                    text=(
                        "✍️ Надішліть оновлену статтю одним повідомленням у форматі:\n"
                        "1 рядок - заголовок\n"
                        "з 2 рядка - текст статті"
                    ),
                    parse_mode=None,
                )
            except TelegramAPIError:
                logger.exception("Failed to send library article-edit prompt")
        if query.message is not None:
            await state.update_data(
                menu_chat_id=query.message.chat.id,
                menu_message_id=query.message.message_id,
            )
        await query.answer()
    else:
        await state.clear()
        if admin_mode and callback_data.view == VIEW_ADMIN_APPROVE_PENDING:
            action_result, nav_after_action = await apply_admin_approve_action(
                repo=repo,
                callback_data=callback_data,
            )
            if action_result == "approved":
                await query.answer("✅ Заявку схвалено без голосування.")
            elif action_result == "not_found":
                await query.answer("⚠️ Заявку не знайдено.", show_alert=True)
            elif action_result == "not_pending":
                await query.answer("ℹ️ Для цього статусу ручне схвалення недоступне.")
            else:
                await query.answer()
        elif is_library_topic_delete_action:
            topic_id = int(callback_data.target_user_id)
            topic_page = parse_topic_page(callback_data.back_view, 0)
            deleted = await repo.delete_library_topic(topic_id=topic_id)
            nav_after_action = MenuCallbackData(
                scope=SCOPE_ADMIN,
                view=VIEW_ADMIN_LIBRARY_TOPICS,
                page=topic_page,
            )
            if deleted:
                await query.answer("✅ Тему видалено.")
            else:
                await query.answer("⚠️ Тему не знайдено.", show_alert=True)
        elif is_library_article_delete_action:
            article_id = int(callback_data.target_user_id)
            topic_id, topic_page = parse_topic_back_payload(callback_data.back_view)
            if topic_id <= 0:
                article = await repo.get_library_article(article_id=article_id, include_inactive=True)
                if article is not None:
                    topic_id = int(article.get("topic_id") or 0)
            deleted = await repo.delete_library_article(article_id=article_id)
            if topic_id > 0:
                nav_after_action = MenuCallbackData(
                    scope=SCOPE_ADMIN,
                    view=VIEW_ADMIN_LIBRARY_ARTICLES,
                    target_user_id=topic_id,
                    page=max(callback_data.page, 0),
                    back_view=str(topic_page),
                )
            else:
                nav_after_action = MenuCallbackData(
                    scope=SCOPE_ADMIN,
                    view=VIEW_ADMIN_LIBRARY_TOPICS,
                    page=topic_page,
                )
            if deleted:
                await query.answer("✅ Статтю видалено.")
            else:
                await query.answer("⚠️ Статтю не знайдено.", show_alert=True)
        else:
            await query.answer()

    if admin_mode:
        await apply_admin_price_action(
            repo=repo,
            config=config,
            callback_data=callback_data,
            actor_tg_user_id=query.from_user.id,
        )

    screen = await render_menu_screen(
        repo=repo,
        config=config,
        bot=query.bot,
        tg_user_id=query.from_user.id,
        is_admin=admin_mode,
        nav=nav_after_action,
    )
    await edit_or_fallback_menu(query=query, screen=screen)
