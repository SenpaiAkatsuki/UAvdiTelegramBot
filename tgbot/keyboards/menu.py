from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tgbot.callbacks.menu import (
    MenuCallbackData,
    SCOPE_ADMIN,
    SCOPE_USER,
    VIEW_ADMIN_ACTIVE,
    VIEW_ADMIN_APPROVE_PENDING,
    VIEW_ADMIN_EXPIRED,
    VIEW_ADMIN_MANAGEMENT,
    VIEW_ADMIN_PENDING,
    VIEW_ADMIN_ROOT,
    VIEW_ADMIN_SUBSCRIPTION_PRICE,
    VIEW_ADMIN_USER_DETAIL,
    VIEW_ADMIN_VOTING_SETTINGS,
    VIEW_PROFILE,
    VIEW_USER_ROOT,
)

"""
Inline menu keyboards.

Builds user/admin menu screens, member list pagination, and back-navigation buttons.
"""


def safe_member_label(member: dict) -> str:
    # Format compact member title for admin list button.
    full_name = str(member.get("full_name") or "").strip()
    username = str(member.get("username") or "").strip()
    if full_name:
        base = full_name
    elif username:
        base = f"@{username}"
    else:
        base = str(member.get("tg_user_id") or "member")
    return (base[:24] + "...") if len(base) > 27 else base


def menu_entry_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    # Single entry button to open user/admin root menu.
    keyboard = InlineKeyboardBuilder()
    target_scope = SCOPE_ADMIN if is_admin else SCOPE_USER
    target_view = VIEW_ADMIN_ROOT if is_admin else VIEW_USER_ROOT
    keyboard.button(
        text="📋 Меню",
        callback_data=MenuCallbackData(scope=target_scope, view=target_view),
    )
    keyboard.adjust(1)
    return keyboard.as_markup()


def user_root_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    # User root menu with optional admin panel entry.
    keyboard = InlineKeyboardBuilder()
    keyboard.button(
        text="👤 Профіль",
        callback_data=MenuCallbackData(scope=SCOPE_USER, view=VIEW_PROFILE),
    )
    if is_admin:
        keyboard.button(
            text="🛠 Адмін-панель",
            callback_data=MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_ROOT),
        )
    keyboard.adjust(1)
    return keyboard.as_markup()


def user_profile_keyboard(
    *,
    show_renew: bool,
    show_group_access: bool,
    back_to_admin: bool = False,
) -> InlineKeyboardMarkup:
    # Profile screen actions: renew, get group access, and back.
    keyboard = InlineKeyboardBuilder()
    if show_renew:
        keyboard.button(text="💳 Продовжити підписку", callback_data="membership_pay")
    if show_group_access:
        keyboard.button(
            text="🔐 Отримати доступ до групи",
            callback_data="membership_get_group_access",
        )
    keyboard.button(
        text="⬅️ Назад",
        callback_data=MenuCallbackData(
            scope=SCOPE_ADMIN if back_to_admin else SCOPE_USER,
            view=VIEW_ADMIN_ROOT if back_to_admin else VIEW_USER_ROOT,
        ),
    )
    keyboard.adjust(1)
    return keyboard.as_markup()


def admin_root_keyboard() -> InlineKeyboardMarkup:
    # Admin root split into profile and management sections.
    keyboard = InlineKeyboardBuilder()
    keyboard.button(
        text="👤 Профіль",
        callback_data=MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_PROFILE),
    )
    keyboard.button(
        text="🧭 Керування",
        callback_data=MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_MANAGEMENT),
    )
    keyboard.adjust(2)
    return keyboard.as_markup()


def admin_management_keyboard() -> InlineKeyboardMarkup:
    # Admin management screens navigation.
    keyboard = InlineKeyboardBuilder()
    keyboard.button(
        text="🕒 Очікують схвалення",
        callback_data=MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_PENDING, page=0),
    )
    keyboard.button(
        text="✅ Активні учасники",
        callback_data=MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_ACTIVE, page=0),
    )
    keyboard.button(
        text="❌ Прострочені",
        callback_data=MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_EXPIRED, page=0),
    )
    keyboard.button(
        text="💰 Ціна підписки (грн)",
        callback_data=MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_SUBSCRIPTION_PRICE,
        ),
    )
    keyboard.button(
        text="🗳 Налаштування голосування",
        callback_data=MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_VOTING_SETTINGS,
        ),
    )
    keyboard.button(
        text="⬅️ Назад",
        callback_data=MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_ROOT),
    )
    keyboard.adjust(1, 2, 2)
    return keyboard.as_markup()


def admin_subscription_price_keyboard() -> InlineKeyboardMarkup:
    # Price management action keyboard.
    keyboard = InlineKeyboardBuilder()
    keyboard.button(
        text="✍️ Встановити нову ціну",
        callback_data=MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_SUBSCRIPTION_PRICE,
            back_view="custom",
        ),
    )
    keyboard.button(
        text="⬅️ Назад",
        callback_data=MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_MANAGEMENT,
        ),
    )
    keyboard.adjust(1, 1)
    return keyboard.as_markup()


def admin_voting_settings_keyboard() -> InlineKeyboardMarkup:
    # Voting-setup keyboard.
    keyboard = InlineKeyboardBuilder()
    keyboard.button(
        text="✍️ Ціль голосів",
        callback_data=MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_VOTING_SETTINGS,
            back_view="custom_target",
        ),
    )
    keyboard.button(
        text="✍️ Тривалість опиту (сек.)",
        callback_data=MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_VOTING_SETTINGS,
            back_view="custom_duration",
        ),
    )
    keyboard.button(
        text="⬅️ Назад",
        callback_data=MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=VIEW_ADMIN_MANAGEMENT,
        ),
    )
    keyboard.adjust(1, 1, 1)
    return keyboard.as_markup()


def admin_members_list_keyboard(
    *,
    members: Sequence[dict],
    list_view: str,
    page: int,
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    # Paginated member list keyboard with detail and prev/next buttons.
    rows: list[list[InlineKeyboardButton]] = []
    for member in members:
        tg_user_id = int(member["tg_user_id"])
        rows.append(
            [
                InlineKeyboardButton(
                    text=safe_member_label(member),
                    callback_data=MenuCallbackData(
                        scope=SCOPE_ADMIN,
                        view=VIEW_ADMIN_USER_DETAIL,
                        page=page,
                        target_user_id=tg_user_id,
                        back_view=list_view,
                    ).pack(),
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if has_prev:
        nav_row.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=MenuCallbackData(
                    scope=SCOPE_ADMIN,
                    view=list_view,
                    page=max(page - 1, 0),
                ).pack(),
            )
        )
    if has_next:
        nav_row.append(
            InlineKeyboardButton(
                text="Далі ▶️",
                callback_data=MenuCallbackData(
                    scope=SCOPE_ADMIN,
                    view=list_view,
                    page=page + 1,
                ).pack(),
            )
        )
    if nav_row:
        rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=MenuCallbackData(
                    scope=SCOPE_ADMIN,
                    view=VIEW_ADMIN_MANAGEMENT,
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_detail_keyboard(
    *,
    back_view: str,
    page: int,
    target_user_id: int,
    show_approve: bool,
) -> InlineKeyboardMarkup:
    # Back button from member detail to source list page.
    keyboard = InlineKeyboardBuilder()
    if show_approve:
        keyboard.button(
            text="✅ Схвалити без голосування",
            callback_data=MenuCallbackData(
                scope=SCOPE_ADMIN,
                view=VIEW_ADMIN_APPROVE_PENDING,
                page=max(page, 0),
                target_user_id=target_user_id,
                back_view=back_view,
            ),
        )
    keyboard.button(
        text="⬅️ Назад",
        callback_data=MenuCallbackData(
            scope=SCOPE_ADMIN,
            view=back_view,
            page=max(page, 0),
        ),
    )
    keyboard.adjust(1, 1)
    return keyboard.as_markup()


def admin_denied_keyboard() -> InlineKeyboardMarkup:
    # Fallback keyboard when non-admin opens admin scope.
    keyboard = InlineKeyboardBuilder()
    keyboard.button(
        text="⬅️ Назад",
        callback_data=MenuCallbackData(scope=SCOPE_USER, view=VIEW_USER_ROOT),
    )
    keyboard.adjust(1)
    return keyboard.as_markup()
