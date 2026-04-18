from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tgbot.callbacks.menu import (
    MenuCallbackData,
    SCOPE_ADMIN,
    SCOPE_USER,
    VIEW_ADMIN_ADD_ADMIN,
    VIEW_ADMIN_ACTIVE,
    VIEW_ADMIN_LIBRARY_ADD_ARTICLE,
    VIEW_ADMIN_LIBRARY_ADD_TOPIC,
    VIEW_ADMIN_LIBRARY_ARTICLE,
    VIEW_ADMIN_LIBRARY_ARTICLES,
    VIEW_ADMIN_LIBRARY_DELETE_ARTICLE,
    VIEW_ADMIN_LIBRARY_DELETE_TOPIC,
    VIEW_ADMIN_LIBRARY_EDIT_ARTICLE,
    VIEW_ADMIN_LIBRARY_EDIT_TOPIC,
    VIEW_ADMIN_LIBRARY_TOPICS,
    VIEW_ADMIN_EXPIRED,
    VIEW_ADMIN_MANAGEMENT,
    VIEW_ADMIN_PENDING,
    VIEW_ADMIN_ROOT,
    VIEW_ADMIN_SUBSCRIPTION_PRICE,
    VIEW_ADMIN_USER_DETAIL,
    VIEW_ADMIN_VOTING_SETTINGS,
    VIEW_ADMIN_BROADCAST,
    VIEW_LIBRARY_ARTICLE,
    VIEW_LIBRARY_ARTICLES,
    VIEW_LIBRARY_TOPICS,
    VIEW_PROFILE,
    VIEW_USER_ROOT,
)
from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.menu import (
    admin_add_admin_keyboard,
    admin_denied_keyboard,
    library_article_keyboard,
    library_articles_keyboard,
    library_topics_keyboard,
    admin_management_keyboard,
    admin_members_list_keyboard,
    admin_root_keyboard,
    admin_subscription_price_keyboard,
    admin_user_detail_keyboard,
    admin_voting_settings_keyboard,
    user_profile_keyboard,
    user_root_keyboard,
)
from tgbot.services.chat_config_sync import check_runtime_chat_setup_issues
from tgbot.services.membership_access import has_payment_exemption

"""
Menu rendering service.

Builds text and inline keyboard for each user/admin menu screen.
"""

PAGE_SIZE = 8
LIBRARY_PAGE_SIZE = 8
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
    "BLOCKED": "Обмежена адміністратором",
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
    return dt.strftime("%d.%m.%Y")


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
    days_left: int | None,
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
        if days_left is not None and days_left <= 30:
            return "натисніть «Продовжити підписку», щоб не втратити доступ"
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
        if days_left <= 30:
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


async def _is_board_member(
    *,
    repo: PostgresRepo,
    config: Config,
    tg_user_id: int,
    bot: Bot | None,
) -> bool:
    # Voting-group members are treated as board members with payment exemption.
    if bot is None:
        return await repo.is_active_voting_member(tg_user_id=tg_user_id)
    return await has_payment_exemption(
        bot=bot,
        config=config,
        tg_user_id=tg_user_id,
        repo=repo,
    )


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


def _parse_int(raw: str | None, default: int = 0) -> int:
    # Parse integer from callback payload safely.
    try:
        if raw is None:
            return default
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _parse_topic_back(raw: str | None) -> tuple[int, int]:
    # Parse "<topic_id>:<topic_page>" payload used in article screens.
    if not raw:
        return 0, 0
    text = str(raw)
    if "|" in text:
        parts = text.split("|", maxsplit=1)
    else:
        parts = text.split(":", maxsplit=1)
    if len(parts) != 2:
        return _parse_int(raw, 0), 0
    return _parse_int(parts[0], 0), max(_parse_int(parts[1], 0), 0)


def _short_content(value: str | None, limit: int = 3500) -> str:
    # Keep article text inside Telegram message limits.
    text = str(value or "").strip()
    if len(text) <= limit:
        return text or "Немає тексту."
    return text[:limit].rstrip() + "\n\n..."


def _safe_library_title(value: object, fallback: str = "Example") -> str:
    # Replace broken placeholder titles like "???" with readable fallback.
    title = " ".join(str(value or "").split()).strip()
    if not title:
        return fallback
    if title.startswith("Example ") and title[len("Example ") :].isdigit():
        return "Example"
    marker = (
        title.replace("?", "")
        .replace("�", "")
        .replace("0", "")
        .replace("1", "")
        .replace("2", "")
        .replace("3", "")
        .replace("4", "")
        .replace("5", "")
        .replace("6", "")
        .replace("7", "")
        .replace("8", "")
        .replace("9", "")
        .replace(".", "")
        .replace("-", "")
        .replace("_", "")
        .strip()
    )
    if not marker:
        return fallback
    return title


async def _render_library_topics(
    *,
    repo: PostgresRepo,
    is_admin: bool,
    page: int,
) -> MenuScreen:
    # Render topic list for user/admin library view.
    safe_page = max(page, 0)
    offset = safe_page * LIBRARY_PAGE_SIZE
    rows = await repo.list_library_topics(
        limit=LIBRARY_PAGE_SIZE + 1,
        offset=offset,
        include_inactive=False,
    )
    has_next = len(rows) > LIBRARY_PAGE_SIZE
    page_rows = rows[:LIBRARY_PAGE_SIZE]
    has_prev = safe_page > 0

    if page_rows:
        lines = [
            f"📘 {offset + idx}. {_safe_library_title(row.get('title'), fallback='Example')}"
            for idx, row in enumerate(page_rows, start=1)
        ]
        body = "\n".join(lines)
    else:
        body = "📭 Список тем поки порожній."

    if is_admin:
        text = (
            "📚 Редагування бібліотеки\n\n"
            f"{body}\n\n"
            "👇 Оберіть тему для перегляду статей або використайте кнопки керування."
        )
    else:
        text = (
            "📚 Бібліотека\n\n"
            f"{body}\n\n"
            "👇 Оберіть тему, щоб відкрити статті."
        )

    return MenuScreen(
        text=text,
        reply_markup=library_topics_keyboard(
            topics=page_rows,
            scope=SCOPE_ADMIN if is_admin else SCOPE_USER,
            page=safe_page,
            has_prev=has_prev,
            has_next=has_next,
            is_admin=is_admin,
        ),
    )


async def _render_library_articles(
    *,
    repo: PostgresRepo,
    is_admin: bool,
    topic_id: int,
    topic_page: int,
    page: int,
) -> MenuScreen:
    # Render articles page for selected topic.
    topic = await repo.get_library_topic(topic_id=topic_id, include_inactive=is_admin)
    if topic is None:
        return await _render_library_topics(repo=repo, is_admin=is_admin, page=topic_page)

    safe_page = max(page, 0)
    offset = safe_page * LIBRARY_PAGE_SIZE
    rows = await repo.list_library_articles(
        topic_id=topic_id,
        limit=LIBRARY_PAGE_SIZE + 1,
        offset=offset,
        include_inactive=is_admin,
    )
    has_next = len(rows) > LIBRARY_PAGE_SIZE
    page_rows = rows[:LIBRARY_PAGE_SIZE]
    has_prev = safe_page > 0

    if page_rows:
        lines = [
            f"📄 {offset + idx}. {_safe_library_title(row.get('title'), fallback='Example')}"
            for idx, row in enumerate(page_rows, start=1)
        ]
        body = "\n".join(lines)
    else:
        body = "📭 У цій темі ще немає статей."

    prefix = "📚 Редагування бібліотеки" if is_admin else "📚 Бібліотека"
    text = (
        f"{prefix}\n\n"
        f"🧩 Тема: {_safe_library_title(topic.get('title'), fallback='Example')}\n\n"
        f"{body}"
    )

    return MenuScreen(
        text=text,
        reply_markup=library_articles_keyboard(
            articles=page_rows,
            scope=SCOPE_ADMIN if is_admin else SCOPE_USER,
            topic_id=int(topic_id),
            topic_page=max(topic_page, 0),
            page=safe_page,
            has_prev=has_prev,
            has_next=has_next,
            is_admin=is_admin,
        ),
    )


async def _render_library_article(
    *,
    repo: PostgresRepo,
    is_admin: bool,
    article_id: int,
    article_page: int,
    back_view: str,
) -> MenuScreen:
    # Render single article details.
    back_topic_id, back_topic_page = _parse_topic_back(back_view)
    article = await repo.get_library_article(
        article_id=article_id,
        include_inactive=is_admin,
    )
    if article is None:
        if back_topic_id > 0:
            return await _render_library_articles(
                repo=repo,
                is_admin=is_admin,
                topic_id=back_topic_id,
                topic_page=back_topic_page,
                page=max(article_page, 0),
            )
        return await _render_library_topics(repo=repo, is_admin=is_admin, page=back_topic_page)

    topic_id = int(article.get("topic_id") or back_topic_id or 0)
    topic_title = _safe_library_title(article.get("topic_title"), fallback="Example")
    title = _safe_library_title(article.get("title"), fallback="Example")
    content = _short_content(article.get("content"))
    prefix = "📚 Редагування бібліотеки" if is_admin else "📚 Бібліотека"
    text = (
        f"{prefix}\n\n"
        f"🧩 Тема: {topic_title}\n"
        f"📄 Стаття: {title}\n\n"
        f"{content}"
    )
    return MenuScreen(
        text=text,
        reply_markup=library_article_keyboard(
            scope=SCOPE_ADMIN if is_admin else SCOPE_USER,
            topic_id=topic_id,
            topic_page=max(back_topic_page, 0),
            article_id=int(article_id),
            article_page=max(article_page, 0),
            is_admin=is_admin,
        ),
    )


async def _render_user_root(
    *,
    repo: PostgresRepo,
    config: Config,
    bot: Bot | None,
    tg_user_id: int,
    is_admin: bool,
) -> MenuScreen:
    # Render user root menu screen.
    panel_data = await repo.get_user_panel_data(tg_user_id=tg_user_id)
    is_board_member = await _is_board_member(
        repo=repo,
        config=config,
        tg_user_id=tg_user_id,
        bot=bot,
    )
    app_status = str(panel_data.get("application_status") or "NEW") if panel_data else "NEW"
    subscription_status = (
        str(panel_data.get("subscription_status") or "NONE") if panel_data else "NONE"
    )
    if is_board_member:
        app_label = "Член правління"
        subscription_label = "Безстрокова"
    else:
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
    config: Config,
    bot: Bot | None,
    tg_user_id: int,
    is_admin: bool,
) -> MenuScreen:
    # Render user profile details and context actions.
    panel_data = await repo.get_user_panel_data(tg_user_id=tg_user_id)
    is_board_member = await _is_board_member(
        repo=repo,
        config=config,
        tg_user_id=tg_user_id,
        bot=bot,
    )

    if panel_data is None and not is_board_member:
        text = "⚠️ Профіль тимчасово недоступний. Спробуйте запустити /start ще раз."
        return MenuScreen(
            text=text,
            reply_markup=user_profile_keyboard(
                show_renew=False,
                show_group_access=False,
                back_to_admin=is_admin,
            ),
        )

    member_since = panel_data.get("member_since") if panel_data else None
    expires_at = panel_data.get("subscription_expires_at") if panel_data else None
    days_left = repo.compute_days_left(expires_at)
    app_status = str(panel_data.get("application_status") or "NEW") if panel_data else "NEW"
    subscription_status = (
        str(panel_data.get("subscription_status") or "NONE") if panel_data else "NONE"
    )

    show_renew = (days_left is not None and days_left <= 30) and not is_board_member
    show_group_access = _is_group_access_eligible(panel_data)

    if is_board_member:
        app_label = "Член правління"
        subscription_label = "Безстрокова"
        expires_label = "Без обмеження"
        days_label = "Без обмеження"
        next_step = "профіль активний, додаткових дій не потрібно"
    else:
        app_label = _application_status_label(app_status)
        subscription_label = _subscription_status_label(subscription_status)
        expires_label = _format_datetime(expires_at)
        days_label = _format_days_left_human(days_left)
        next_step = _next_step_hint(
            app_status=app_status,
            subscription_status=subscription_status,
            days_left=days_left,
            show_group_access=show_group_access,
        )

    text = (
        "👤 Ваш профіль\n\n"
        f"🧾 Статус заявки: {app_label}\n"
        f"💳 Підписка: {subscription_label}\n"
        f"📅 Учасник з: {_format_datetime(member_since)}\n"
        f"⏳ Діє до: {expires_label}\n"
        f"🕒 Залишилось: {days_label}\n\n"
        f"➡️ Що далі: {next_step}."
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
        "- 🧭 Керування\n"
        "- 📚 Редагування бібліотеки"
    )
    return MenuScreen(text=text, reply_markup=admin_root_keyboard())


async def _render_admin_management(
    *,
    repo: PostgresRepo,
    config: Config,
    bot: Bot | None = None,
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
    setup_issues: list[str] = []
    if bot is not None:
        setup_issues = await check_runtime_chat_setup_issues(
            bot=bot,
            config=config,
            repo=repo,
        )
    else:
        if not int(config.voting.chat_id or 0):
            setup_issues.append("Не задано групу голосування (VOTING_CHAT_ID).")
        if not int(config.chat.membership_chat_id or 0):
            setup_issues.append("Не задано групу членства (CHAT_MEMBERSHIP_CHAT_ID).")

    warning_block = ""
    if setup_issues:
        warning_block = (
            "\n\n"
            "⚠️ Увага: групи для бота не налаштовано або недоступні.\n"
            + "\n".join(f"- {issue}" for issue in setup_issues)
            + "\n"
            + "Використайте: /set_voting_chat та /set_membership_chat."
        )

    text = (
        "🧭 Панель керування\n\n"
        "Робота з учасниками та підписками:\n"
        "- 🕒 Очікують схвалення\n"
        "- ✅ Активні учасники\n"
        "- ❌ Прострочені\n\n"
        "Сповіщення:\n"
        "- 📣 Розсилка учасникам\n\n"
        "Голосування:\n"
        f"- Ціль голосів (за/проти): {vote_target}\n"
        f"- Тривалість опиту: {vote_duration_seconds} сек."
        f"{warning_block}"
    )
    return MenuScreen(
        text=text,
        reply_markup=admin_management_keyboard(),
    )


async def _render_admin_add_admin(*, input_mode: str = "") -> MenuScreen:
    # Render inline-only admin grant instructions (contact or /chatid value).
    if input_mode == "contact":
        hint = "Натисніть кнопку запиту контакту внизу чату та надішліть контакт."
    elif input_mode == "id":
        hint = (
            "Надішліть Telegram ID користувача одним повідомленням.\n"
            "Можна отримати ID через команду /chatid."
        )
    else:
        hint = "Оберіть спосіб нижче, потім надішліть дані в чат."
    text = (
        "➕ Додати адміністратора\n\n"
        "Варіанти:\n"
        "- Натисніть «📇 Надіслати контакт».\n"
        "- Або натисніть «🆔 Додати за ID».\n\n"
        f"{hint}"
    )
    return MenuScreen(
        text=text,
        reply_markup=admin_add_admin_keyboard(input_mode=input_mode),
    )


async def _render_admin_broadcast(*, prompt_input: bool = False) -> MenuScreen:
    # Render admin broadcast screen and optional input mode.
    text = (
        "📣 Розсилка учасникам\n\n"
        + (
            "✍️ Надішліть текст повідомлення одним повідомленням у чат.\n"
            "Бот розішле його всім користувачам із таблиці users."
            if prompt_input
            else "Натисніть «Почати розсилку», потім надішліть текст повідомлення в чат."
        )
    )
    if prompt_input:
        reply_markup = _back_only_keyboard(view=VIEW_ADMIN_BROADCAST)
    else:
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✍️ Почати розсилку",
                        callback_data=MenuCallbackData(
                            scope=SCOPE_ADMIN,
                            view=VIEW_ADMIN_BROADCAST,
                            back_view="compose",
                        ).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=MenuCallbackData(
                            scope=SCOPE_ADMIN,
                            view=VIEW_ADMIN_MANAGEMENT,
                        ).pack(),
                    )
                ],
            ]
        )
    return MenuScreen(text=text, reply_markup=reply_markup)


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
            f"👤 Користувач: {_member_title(detail)}\n"
            f"🆔 ID: {detail.get('tg_user_id')}\n"
            f"🧾 Статус заявки: {_application_status_label(app_status)}\n"
            f"💳 Підписка: {_subscription_status_label(subscription_status)}\n"
            f"📅 Учасник з: {_format_datetime(detail.get('member_since'))}\n"
            f"⏳ Діє до: {_format_datetime(detail.get('subscription_expires_at'))}\n"
            f"🕒 Залишок: {_format_days_left_human(days_left)}\n\n"
            f"➡️ Що далі: {next_step}."
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
    bot: Bot | None = None,
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
            return await _render_admin_management(
                repo=repo,
                config=config,
                bot=bot,
            )
        if nav.view == VIEW_ADMIN_ADD_ADMIN:
            mode_raw = str(nav.back_view or "").strip().lower()
            mode = mode_raw if mode_raw in {"contact", "id"} else ""
            return await _render_admin_add_admin(input_mode=mode)
        if nav.view == VIEW_ADMIN_BROADCAST:
            return await _render_admin_broadcast(
                prompt_input=str(nav.back_view or "").strip().lower() == "compose",
            )
        if nav.view == VIEW_ADMIN_LIBRARY_TOPICS:
            return await _render_library_topics(
                repo=repo,
                is_admin=True,
                page=nav.page,
            )
        if nav.view == VIEW_ADMIN_LIBRARY_ARTICLES:
            return await _render_library_articles(
                repo=repo,
                is_admin=True,
                topic_id=int(nav.target_user_id),
                topic_page=max(_parse_int(nav.back_view, 0), 0),
                page=nav.page,
            )
        if nav.view == VIEW_ADMIN_LIBRARY_ARTICLE:
            return await _render_library_article(
                repo=repo,
                is_admin=True,
                article_id=int(nav.target_user_id),
                article_page=nav.page,
                back_view=nav.back_view,
            )
        if nav.view in {
            VIEW_ADMIN_LIBRARY_ADD_TOPIC,
            VIEW_ADMIN_LIBRARY_EDIT_TOPIC,
            VIEW_ADMIN_LIBRARY_DELETE_TOPIC,
        }:
            return await _render_library_topics(
                repo=repo,
                is_admin=True,
                page=max(_parse_int(nav.back_view, nav.page), 0),
            )
        if nav.view == VIEW_ADMIN_LIBRARY_ADD_ARTICLE:
            topic_id = int(nav.target_user_id)
            topic_page = max(_parse_int(nav.back_view, 0), 0)
            return await _render_library_articles(
                repo=repo,
                is_admin=True,
                topic_id=topic_id,
                topic_page=max(topic_page, 0),
                page=max(nav.page, 0),
            )
        if nav.view in {
            VIEW_ADMIN_LIBRARY_EDIT_ARTICLE,
            VIEW_ADMIN_LIBRARY_DELETE_ARTICLE,
        }:
            topic_id, topic_page = _parse_topic_back(nav.back_view)
            if topic_id <= 0:
                article = await repo.get_library_article(
                    article_id=int(nav.target_user_id),
                    include_inactive=True,
                )
                if article is not None:
                    topic_id = int(article.get("topic_id") or 0)
            return await _render_library_articles(
                repo=repo,
                is_admin=True,
                topic_id=topic_id,
                topic_page=max(topic_page, 0),
                page=max(nav.page, 0),
            )
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
            return await _render_profile(
                repo=repo,
                config=config,
                bot=bot,
                tg_user_id=tg_user_id,
                is_admin=True,
            )
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
        return await _render_profile(
            repo=repo,
            config=config,
            bot=bot,
            tg_user_id=tg_user_id,
            is_admin=is_admin,
        )
    if nav.view == VIEW_LIBRARY_TOPICS:
        return await _render_library_topics(
            repo=repo,
            is_admin=False,
            page=nav.page,
        )
    if nav.view == VIEW_LIBRARY_ARTICLES:
        return await _render_library_articles(
            repo=repo,
            is_admin=False,
            topic_id=int(nav.target_user_id),
            topic_page=max(_parse_int(nav.back_view, 0), 0),
            page=nav.page,
        )
    if nav.view == VIEW_LIBRARY_ARTICLE:
        return await _render_library_article(
            repo=repo,
            is_admin=False,
            article_id=int(nav.target_user_id),
            article_page=nav.page,
            back_view=nav.back_view,
        )
    return await _render_user_root(
        repo=repo,
        config=config,
        bot=bot,
        tg_user_id=tg_user_id,
        is_admin=is_admin,
    )
