from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from aiogram import Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from tgbot.callbacks.menu import (
    MenuCallbackData,
    SCOPE_ADMIN,
    SCOPE_USER,
    VIEW_ADMIN_APPROVE_PENDING,
    VIEW_ADMIN_EXPIRING_SETTINGS,
    VIEW_ADMIN_SUBSCRIPTION_PRICE,
    VIEW_ADMIN_USER_DETAIL,
    VIEW_ADMIN_VOTING_SETTINGS,
    VIEW_ADMIN_ROOT,
    VIEW_USER_ROOT,
)
from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.filters.admin import AdminFilter
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


def is_admin(config: Config, tg_user_id: int) -> bool:
    # Check admin access using config admin ids.
    return tg_user_id in config.tg_bot.admin_ids


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
    admin_mode = is_admin(config, message.from_user.id)
    if not admin_mode:
        panel_data = await repo.get_user_panel_data(tg_user_id=message.from_user.id)
        if not has_active_subscription(panel_data):
            await message.answer(
                "🔒 Меню стане доступним після активації членства."
            )
            return
        in_group = await is_user_in_membership_group(
            message=message,
            config=config,
            tg_user_id=message.from_user.id,
        )
        if not in_group:
            await message.answer(
                "🔒 Меню стане доступним після вступу до групи.\n"
                "Надішліть /start і натисніть «Отримати доступ до групи»."
            )
            return

        if (
            panel_data
            and str(panel_data.get("application_status") or "") == "PAID_AWAITING_JOIN"
            and panel_data.get("application_id") is not None
        ):
            await repo.update_application_status(
                application_id=int(panel_data["application_id"]),
                status="ACTIVE_MEMBER",
            )

    nav = (
        MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_ROOT)
        if admin_mode
        else MenuCallbackData(scope=SCOPE_USER, view=VIEW_USER_ROOT)
    )
    screen = await render_menu_screen(
        repo=repo,
        config=config,
        tg_user_id=message.from_user.id,
        is_admin=admin_mode,
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
        tg_user_id=message.from_user.id,
        is_admin=True,
        nav=nav,
    )
    await send_menu_message(message=message, screen=screen)

    try:
        await message.delete()
    except TelegramAPIError:
        pass


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

    admin_mode = is_admin(config, query.from_user.id)
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

    if (
        is_custom_price_input_request
        or is_custom_expiring_days_request
        or is_custom_vote_target_request
        or is_custom_vote_duration_request
    ):
        if is_custom_price_input_request:
            await state.set_state(MenuPriceState.waiting_price)
        elif is_custom_expiring_days_request:
            await state.set_state(MenuExpiringSettingsState.waiting_days)
        elif is_custom_vote_target_request:
            await state.set_state(MenuVotingSettingsState.waiting_target_votes)
        elif is_custom_vote_duration_request:
            await state.set_state(MenuVotingSettingsState.waiting_duration_seconds)
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
        tg_user_id=query.from_user.id,
        is_admin=admin_mode,
        nav=nav_after_action,
    )
    await edit_or_fallback_menu(query=query, screen=screen)
