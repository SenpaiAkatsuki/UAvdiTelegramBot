from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from aiogram.types import InlineKeyboardMarkup

from tgbot.callbacks.menu import (
    MenuCallbackData,
    SCOPE_ADMIN,
    VIEW_ADMIN_ACTIVE,
    VIEW_ADMIN_EXPIRED,
    VIEW_ADMIN_EXPIRING,
    VIEW_ADMIN_MANAGEMENT,
    VIEW_ADMIN_ROOT,
    VIEW_ADMIN_SUBSCRIPTION_PRICE,
    VIEW_ADMIN_USER_DETAIL,
    VIEW_PROFILE,
    VIEW_USER_ROOT,
)
from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.menu import (
    admin_denied_keyboard,
    admin_management_keyboard,
    admin_members_list_keyboard,
    admin_root_keyboard,
    admin_subscription_price_keyboard,
    admin_user_detail_keyboard,
    user_profile_keyboard,
    user_root_keyboard,
)

"""
Menu rendering service.

Builds text and inline keyboard for each user/admin menu screen.
"""

PAGE_SIZE = 8
GROUP_ACCESS_ELIGIBLE_STATUSES = {"PAID_AWAITING_JOIN"}


@dataclass
class MenuScreen:
    # UI payload used by menu handlers.
    text: str
    reply_markup: InlineKeyboardMarkup


def _as_utc(value: datetime | None) -> datetime | None:
    # Normalize datetime to UTC-aware value.
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime | None) -> str:
    # Render datetime for menu screens.
    dt = _as_utc(value)
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _format_days_left(days_left: int | None) -> str:
    # Render days-left value with fallback.
    if days_left is None:
        return "-"
    return str(days_left)


def _is_subscription_active(panel_data: dict | None) -> bool:
    # Check whether panel data shows active non-expired subscription.
    if not panel_data:
        return False
    if str(panel_data.get("subscription_status")) != "ACTIVE":
        return False
    expires_at = _as_utc(panel_data.get("subscription_expires_at"))
    if expires_at is None:
        return False
    return expires_at > datetime.now(timezone.utc)


def _is_group_access_eligible(panel_data: dict | None) -> bool:
    # Check whether user can request group access button.
    if not panel_data:
        return False
    app_status = str(panel_data.get("application_status") or "")
    return app_status in GROUP_ACCESS_ELIGIBLE_STATUSES and _is_subscription_active(panel_data)


def _member_title(member: dict) -> str:
    # Build readable member title for admin lists.
    full_name = str(member.get("full_name") or "").strip()
    username = str(member.get("username") or "").strip()
    tg_user_id = member.get("tg_user_id")
    if full_name and username:
        return f"{full_name} (@{username})"
    if full_name:
        return full_name
    if username:
        return f"@{username}"
    return f"user_id={tg_user_id}"


def _member_lines(rows: Sequence[dict], repo: PostgresRepo, start_index: int) -> list[str]:
    # Convert member rows to numbered text lines for screen body.
    lines: list[str] = []
    for idx, row in enumerate(rows, start=start_index):
        days_left = repo.compute_days_left(row.get("subscription_expires_at"))
        lines.append(
            f"{idx}. {_member_title(row)} | дійсна до={_format_datetime(row.get('subscription_expires_at'))} | днів={_format_days_left(days_left)}"
        )
    return lines


async def _render_user_root(
    *,
    repo: PostgresRepo,
    tg_user_id: int,
    is_admin: bool,
) -> MenuScreen:
    # Render user root menu screen.
    panel_data = await repo.get_user_panel_data(tg_user_id=tg_user_id)
    app_status = str(panel_data.get("application_status") or "NEW") if panel_data else "NEW"
    subscription_status = (
        str(panel_data.get("subscription_status") or "NONE") if panel_data else "NONE"
    )
    text = (
        "📋 Меню користувача\n\n"
        f"Поточний статус: {app_status}\n"
        f"Підписка: {subscription_status}\n\n"
        "Відкрийте «Профіль» для деталей і дій."
    )
    return MenuScreen(text=text, reply_markup=user_root_keyboard(is_admin=is_admin))


async def _render_profile(
    *,
    repo: PostgresRepo,
    tg_user_id: int,
    is_admin: bool,
) -> MenuScreen:
    # Render user profile details and context actions.
    panel_data = await repo.get_user_panel_data(tg_user_id=tg_user_id)
    if panel_data is None:
        text = "⚠️ Профіль поки недоступний. Спочатку запустіть онбординг через /start."
        return MenuScreen(
            text=text,
            reply_markup=user_profile_keyboard(
                show_renew=False,
                show_group_access=False,
                back_to_admin=is_admin,
            ),
        )

    member_since = panel_data.get("member_since")
    expires_at = panel_data.get("subscription_expires_at")
    days_left = repo.compute_days_left(expires_at)
    app_status = str(panel_data.get("application_status") or "NEW")
    subscription_status = str(panel_data.get("subscription_status") or "NONE")

    show_renew = days_left is not None and days_left <= 20
    show_group_access = _is_group_access_eligible(panel_data)

    text = (
        "👤 Профіль\n\n"
        f"Поточний статус: {app_status}\n"
        f"Статус підписки: {subscription_status}\n"
        f"Учасник з: {_format_datetime(member_since)}\n"
        f"Підписка дійсна до: {_format_datetime(expires_at)}\n"
        f"Залишилось днів: {_format_days_left(days_left)}"
    )
    return MenuScreen(
        text=text,
        reply_markup=user_profile_keyboard(
            show_renew=show_renew,
            show_group_access=show_group_access,
            back_to_admin=is_admin,
        ),
    )


async def _render_admin_root() -> MenuScreen:
    # Render admin root menu.
    text = (
        "🛠 Меню адміністратора\n\n"
        "Оберіть дію:\n"
        "- 👤 Профіль\n"
        "- 🧭 Керування"
    )
    return MenuScreen(text=text, reply_markup=admin_root_keyboard())


async def _render_admin_management() -> MenuScreen:
    # Render admin management menu.
    text = (
        "🧭 Панель керування\n\n"
        "Оберіть розділ:\n"
        "- ✅ Активні учасники\n"
        "- ⏳ Закінчуються (<= 30 днів)\n"
        "- ❌ Прострочені\n"
        "- 💰 Ціна підписки (грн)"
    )
    return MenuScreen(text=text, reply_markup=admin_management_keyboard())


async def _render_admin_subscription_price(
    *,
    repo: PostgresRepo,
    config: Config,
    prompt_input: bool = False,
) -> MenuScreen:
    # Render subscription price screen with input prompt mode.
    current_minor = await repo.get_subscription_price_minor(
        default_minor=int(config.liqpay.amount_minor)
    )
    default_minor = int(config.liqpay.amount_minor)
    current_uah = max(current_minor // 100, 1)
    default_uah = max(default_minor // 100, 1)
    text = (
        "💰 Ціна підписки\n\n"
        f"Поточна: {current_uah} грн\n"
        f"Значення з env: {default_uah} грн\n\n"
        + (
            "✍️ Напишіть нову ціну в чат зараз."
            if prompt_input
            else "Натисніть «Встановити нову ціну», а потім надішліть суму в чат."
        )
    )
    return MenuScreen(
        text=text,
        reply_markup=admin_subscription_price_keyboard(),
    )


async def _render_admin_list(
    *,
    repo: PostgresRepo,
    view: str,
    page: int,
) -> MenuScreen:
    # Render paginated admin member list for selected view.
    safe_page = max(page, 0)
    offset = safe_page * PAGE_SIZE
    limit = PAGE_SIZE + 1

    if view == VIEW_ADMIN_ACTIVE:
        title = "✅ Активні учасники"
        rows = await repo.list_active_members(limit=limit, offset=offset)
    elif view == VIEW_ADMIN_EXPIRING:
        title = "⏳ Закінчуються (<= 30 днів)"
        rows = await repo.list_expiring_members(max_days=30, limit=limit, offset=offset)
    else:
        title = "❌ Прострочені"
        rows = await repo.list_expired_members(limit=limit, offset=offset)

    has_next = len(rows) > PAGE_SIZE
    page_rows = rows[:PAGE_SIZE]
    has_prev = safe_page > 0

    if page_rows:
        lines = _member_lines(page_rows, repo=repo, start_index=offset + 1)
        text = f"{title}\n\n" + "\n".join(lines)
    else:
        text = f"{title}\n\nℹ️ На цій сторінці немає записів."

    keyboard = admin_members_list_keyboard(
        members=page_rows,
        list_view=view,
        page=safe_page,
        has_prev=has_prev,
        has_next=has_next,
    )
    return MenuScreen(text=text, reply_markup=keyboard)


async def _render_admin_user_detail(
    *,
    repo: PostgresRepo,
    target_user_id: int,
    back_view: str,
    page: int,
) -> MenuScreen:
    # Render single member detail card for admin.
    detail = await repo.get_member_detail(tg_user_id=target_user_id)
    if detail is None:
        text = f"⚠️ Дані учасника для user_id={target_user_id} не знайдено."
    else:
        days_left = repo.compute_days_left(detail.get("subscription_expires_at"))
        text = (
            "👤 Деталі учасника\n\n"
            f"Користувач: {_member_title(detail)}\n"
            f"tg_user_id: {detail.get('tg_user_id')}\n"
            f"Статус заявки: {detail.get('application_status') or '-'}\n"
            f"Статус підписки: {detail.get('subscription_status') or '-'}\n"
            f"Учасник з: {_format_datetime(detail.get('member_since'))}\n"
            f"Підписка дійсна до: {_format_datetime(detail.get('subscription_expires_at'))}\n"
            f"Залишилось днів: {_format_days_left(days_left)}"
        )

    keyboard = admin_user_detail_keyboard(back_view=back_view, page=page)
    return MenuScreen(text=text, reply_markup=keyboard)


async def render_menu_screen(
    *,
    repo: PostgresRepo,
    config: Config,
    tg_user_id: int,
    is_admin: bool,
    nav: MenuCallbackData,
) -> MenuScreen:
    # Route menu callback payload to concrete screen renderer.
    if is_admin and nav.scope != SCOPE_ADMIN:
        nav = MenuCallbackData(scope=SCOPE_ADMIN, view=VIEW_ADMIN_ROOT)

    if nav.scope == SCOPE_ADMIN:
        if not is_admin:
            return MenuScreen(
                text="⛔ Адмін-панель доступна лише адміністраторам.",
                reply_markup=admin_denied_keyboard(),
            )

        if nav.view == VIEW_ADMIN_ROOT:
            return await _render_admin_root()
        if nav.view == VIEW_ADMIN_MANAGEMENT:
            return await _render_admin_management()
        if nav.view == VIEW_ADMIN_SUBSCRIPTION_PRICE:
            return await _render_admin_subscription_price(
                repo=repo,
                config=config,
                prompt_input=str(nav.back_view or "").strip().lower() == "custom",
            )
        if nav.view == VIEW_PROFILE:
            return await _render_profile(repo=repo, tg_user_id=tg_user_id, is_admin=True)
        if nav.view in {VIEW_ADMIN_ACTIVE, VIEW_ADMIN_EXPIRING, VIEW_ADMIN_EXPIRED}:
            return await _render_admin_list(repo=repo, view=nav.view, page=nav.page)
        if nav.view == VIEW_ADMIN_USER_DETAIL:
            return await _render_admin_user_detail(
                repo=repo,
                target_user_id=nav.target_user_id,
                back_view=nav.back_view,
                page=nav.page,
            )
        return await _render_admin_root()

    if nav.view == VIEW_PROFILE:
        return await _render_profile(repo=repo, tg_user_id=tg_user_id, is_admin=is_admin)
    return await _render_user_root(repo=repo, tg_user_id=tg_user_id, is_admin=is_admin)
