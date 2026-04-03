from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tgbot.callbacks.menu import (
    MenuCallbackData,
    SCOPE_ADMIN,
    VIEW_ADMIN_ACTIVE,
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
from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.menu import (
    admin_denied_keyboard,
    admin_management_keyboard,
    admin_members_list_keyboard,
    admin_root_keyboard,
    admin_subscription_price_keyboard,
    admin_user_detail_keyboard,
    admin_voting_settings_keyboard,
    user_profile_keyboard,
    user_root_keyboard,
)

"""
Menu rendering service.

Builds text and inline keyboard for each user/admin menu screen.
"""

PAGE_SIZE = 8
GROUP_ACCESS_ELIGIBLE_STATUSES = {"PAID_AWAITING_JOIN"}

APPLICATION_STATUS_LABELS = {
    "NEW": "Початкова реєстрація",
    "APPLICATION_REQUIRED": "Потрібно подати заявку",
    "APPLICATION_PENDING": "Заявка на розгляді",
    "UNLINKED_APPLICATION_PENDING": "Заявка з сайту очікує підтвердження",
    "UNLINKED_APPLICATION_APPROVED": "Контент заявки схвалено, очікується прив'язка",
    "APPROVED_AWAITING_PAYMENT": "Заявку схвалено, очікується оплата",
    "PAID_AWAITING_JOIN": "Оплату підтверджено, можна отримати доступ до групи",
    "ACTIVE_MEMBER": "Членство активне",
    "REJECTED": "Заявку відхилено",
}

SUBSCRIPTION_STATUS_LABELS = {
    "ACTIVE": "Активна",
    "NONE": "Не активована",
}


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


def _format_days_left_human(days_left: int | None) -> str:
    # Render subscription days-left in user-friendly way.
    if days_left is None:
        return "немає активної підписки"
    if days_left < 0:
        return f"прострочено на {abs(days_left)} дн."
    if days_left == 0:
        return "останній день дії"
    return f"{days_left} дн."


def _application_status_label(status: str | None) -> str:
    # Convert internal application status code to readable text.
    normalized = str(status or "NEW").strip().upper()
    return APPLICATION_STATUS_LABELS.get(normalized, "Статус уточнюється")


def _subscription_status_label(status: str | None) -> str:
    # Convert internal subscription status code to readable text.
    normalized = str(status or "NONE").strip().upper()
    return SUBSCRIPTION_STATUS_LABELS.get(normalized, "Статус уточнюється")


def _next_step_hint(
    *,
    app_status: str,
    subscription_status: str,
    show_group_access: bool,
) -> str:
    # Build short "what next" hint for profile screen.
    if show_group_access:
        return "натисніть «Отримати доступ до групи»"
    if app_status == "APPLICATION_PENDING":
        return "очікуйте рішення адміністраторів"
    if app_status == "APPROVED_AWAITING_PAYMENT":
        return "натисніть «Продовжити підписку» та завершіть оплату"
    if app_status == "REJECTED":
        return "зверніться до адміністраторів за деталями"
    if subscription_status == "ACTIVE":
        return "профіль активний, додаткових дій не потрібно"
    return "відкрийте /start для перевірки наступного кроку"


def _admin_next_step_hint(
    *,
    app_status: str,
    subscription_status: str,
    days_left: int | None,
) -> str:
    # Build short admin-facing action hint for member detail.
    if app_status in {"APPLICATION_PENDING", "UNLINKED_APPLICATION_PENDING"}:
        return "очікується рішення адміністраторів"
    if app_status == "UNLINKED_APPLICATION_APPROVED":
        return "очікується прив'язка заявки до Telegram-користувача"
    if app_status == "APPROVED_AWAITING_PAYMENT":
        return "очікується оплата підписки"
    if app_status == "PAID_AWAITING_JOIN":
        return "очікується вхід учасника до групи"
    if app_status == "REJECTED":
        return "заявку завершено зі статусом «Відхилено»"

    if subscription_status == "ACTIVE":
        if days_left is None:
            return "перевірте дату завершення підписки"
        if days_left < 0:
            return "підписка прострочена, потрібне продовження"
        if days_left <= 20:
            return "бажано нагадати учаснику про продовження"
        return "додаткових дій не потрібно"

    return "перевірте стан заявки та оплати"


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
        sub_status = _subscription_status_label(row.get("subscription_status"))
        lines.append(
            f"{idx}. {_member_title(row)} — {sub_status}, до {_format_datetime(row.get('subscription_expires_at'))} ({_format_days_left_human(days_left)})"
        )
    return lines


def _back_only_keyboard(*, view: str) -> InlineKeyboardMarkup:
    # Generic single-back keyboard for input modes.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=MenuCallbackData(
                        scope=SCOPE_ADMIN,
                        view=view,
                    ).pack(),
                )
            ]
        ]
    )


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
    app_label = _application_status_label(app_status)
    subscription_label = _subscription_status_label(subscription_status)

    text = (
        "📋 Ваше меню\n\n"
        f"Статус заявки: {app_label}\n"
        f"Підписка: {subscription_label}\n\n"
        "Відкрийте «Профіль», щоб побачити деталі та наступний крок."
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
        text = "⚠️ Профіль тимчасово недоступний. Спробуйте запустити /start ще раз."
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
    next_step = _next_step_hint(
        app_status=app_status,
        subscription_status=subscription_status,
        show_group_access=show_group_access,
    )

    text = (
        "👤 Ваш профіль\n\n"
        f"Статус заявки: {_application_status_label(app_status)}\n"
        f"Підписка: {_subscription_status_label(subscription_status)}\n"
        f"Учасник з: {_format_datetime(member_since)}\n"
        f"Діє до: {_format_datetime(expires_at)}\n"
        f"Залишилось: {_format_days_left_human(days_left)}\n\n"
        f"Що далі: {next_step}."
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
        "🛠 Адмін-меню\n\n"
        "Оберіть потрібний розділ:\n"
        "- 👤 Профіль\n"
        "- 🧭 Керування"
    )
    return MenuScreen(text=text, reply_markup=admin_root_keyboard())


async def _render_admin_management(
    *,
    repo: PostgresRepo,
    config: Config,
) -> MenuScreen:
    # Render admin management menu.
    vote_target = await repo.get_vote_min_total(
        default_target=(
            int(config.voting.min_total)
            if config.voting.min_total is not None and int(config.voting.min_total) > 0
            else 1
        )
    )
    vote_duration_seconds = await repo.get_vote_duration_seconds(
        default_seconds=int(config.voting.duration_seconds)
    )

    text = (
        "🧭 Панель керування\n\n"
        "Робота з учасниками та підписками:\n"
        "- 🕒 Очікують схвалення\n"
        "- ✅ Активні учасники\n"
        "- ❌ Прострочені\n\n"
        "Голосування:\n"
        f"- Ціль голосів (за/проти): {vote_target}\n"
        f"- Тривалість опиту: {vote_duration_seconds} сек."
    )
    return MenuScreen(
        text=text,
        reply_markup=admin_management_keyboard(),
    )


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
        f"Поточна ціна: {current_uah} грн\n"
        f"Базова (із конфігу): {default_uah} грн\n\n"
        + (
            "✍️ Надішліть нову ціну одним числом у чат."
            if prompt_input
            else "Натисніть «Встановити нову ціну», після цього надішліть суму в чат."
        )
    )
    if prompt_input:
        reply_markup = _back_only_keyboard(view=VIEW_ADMIN_SUBSCRIPTION_PRICE)
    else:
        reply_markup = admin_subscription_price_keyboard()

    return MenuScreen(
        text=text,
        reply_markup=reply_markup,
    )


async def _render_admin_voting_settings(
    *,
    repo: PostgresRepo,
    config: Config,
    prompt_mode: str = "",
) -> MenuScreen:
    # Render voting setup screen (target votes and vote duration).
    vote_target = await repo.get_vote_min_total(
        default_target=(
            int(config.voting.min_total)
            if config.voting.min_total is not None and int(config.voting.min_total) > 0
            else 1
        )
    )
    vote_duration_seconds = await repo.get_vote_duration_seconds(
        default_seconds=int(config.voting.duration_seconds)
    )

    prompt_hint = "Налаштуйте значення кнопками нижче."
    if prompt_mode == "target":
        prompt_hint = "✍️ Надішліть нову ціль голосів одним числом у чат."
    elif prompt_mode == "duration":
        prompt_hint = "✍️ Надішліть нову тривалість опиту в секундах (0 = без таймера)."

    text = (
        "🗳 Налаштування голосування\n\n"
        f"Поточна ціль голосів (за/проти): {vote_target}\n"
        f"Поточна тривалість опиту: {vote_duration_seconds} сек.\n\n"
        f"{prompt_hint}"
    )
    if prompt_mode:
        reply_markup = _back_only_keyboard(view=VIEW_ADMIN_VOTING_SETTINGS)
    else:
        reply_markup = admin_voting_settings_keyboard()
    return MenuScreen(text=text, reply_markup=reply_markup)


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
    elif view == VIEW_ADMIN_PENDING:
        title = "🕒 Очікують схвалення"
        rows = await repo.list_pending_approval_members(limit=limit, offset=offset)
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
        text = f"⚠️ Дані учасника з ID {target_user_id} не знайдено."
    else:
        days_left = repo.compute_days_left(detail.get("subscription_expires_at"))
        app_status = str(detail.get("application_status") or "NEW")
        subscription_status = str(detail.get("subscription_status") or "NONE")
        next_step = _admin_next_step_hint(
            app_status=app_status,
            subscription_status=subscription_status,
            days_left=days_left,
        )
        text = (
            "👤 Профіль учасника\n\n"
            f"Користувач: {_member_title(detail)}\n"
            f"ID: {detail.get('tg_user_id')}\n"
            f"Статус заявки: {_application_status_label(app_status)}\n"
            f"Підписка: {_subscription_status_label(subscription_status)}\n"
            f"Учасник з: {_format_datetime(detail.get('member_since'))}\n"
            f"Діє до: {_format_datetime(detail.get('subscription_expires_at'))}\n"
            f"Залишок: {_format_days_left_human(days_left)}\n\n"
            f"Що далі: {next_step}."
        )

    show_approve = False
    if detail is not None:
        show_approve = str(detail.get("application_status") or "") in {
            "APPLICATION_PENDING",
            "UNLINKED_APPLICATION_PENDING",
        }

    keyboard = admin_user_detail_keyboard(
        back_view=back_view,
        page=page,
        target_user_id=target_user_id,
        show_approve=show_approve,
    )
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
            return await _render_admin_management(repo=repo, config=config)
        if nav.view == VIEW_ADMIN_SUBSCRIPTION_PRICE:
            return await _render_admin_subscription_price(
                repo=repo,
                config=config,
                prompt_input=str(nav.back_view or "").strip().lower() == "custom",
            )
        if nav.view == VIEW_ADMIN_VOTING_SETTINGS:
            mode_raw = str(nav.back_view or "").strip().lower()
            prompt_mode = ""
            if mode_raw == "custom_target":
                prompt_mode = "target"
            elif mode_raw == "custom_duration":
                prompt_mode = "duration"
            return await _render_admin_voting_settings(
                repo=repo,
                config=config,
                prompt_mode=prompt_mode,
            )
        if nav.view == VIEW_PROFILE:
            return await _render_profile(repo=repo, tg_user_id=tg_user_id, is_admin=True)
        if nav.view in {VIEW_ADMIN_PENDING, VIEW_ADMIN_ACTIVE, VIEW_ADMIN_EXPIRED}:
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
