from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.membership import payment_keyboard
from tgbot.keyboards.voting import application_vote_keyboard
from tgbot.services.notify import notify_user

"""
Application voting service.

Starts one-message inline voting for applications and finalizes results after timeout.
"""

logger = logging.getLogger(__name__)

VOTE_STATUS_OPEN = "OPEN"
VOTE_STATUS_PROCESSED = "PROCESSED"
VOTE_CAST_OK = "OK"
VOTE_CAST_NOT_FOUND = "NOT_FOUND"
VOTE_CAST_CLOSED = "CLOSED"
VOTE_CAST_EXPIRED = "EXPIRED"


def utcnow() -> datetime:
    # Current UTC timestamp helper.
    return datetime.now(timezone.utc)


def calc_vote_result(
    yes_count: int,
    no_count: int,
    min_total: int | None,
    require_yes_gt_no: bool,
) -> bool:
    # Calculate approval decision using per-option target votes.
    target_votes = _target_total_votes(min_total)
    yes_reached = yes_count >= target_votes
    no_reached = no_count >= target_votes

    if yes_reached and not no_reached:
        return True
    if no_reached and not yes_reached:
        return False
    if yes_reached and no_reached:
        if require_yes_gt_no:
            return yes_count > no_count
        return yes_count >= no_count

    # Target not reached for either option.
    return False


def _target_total_votes(min_total: int | None) -> int:
    # Resolve configured per-option vote target, defaulting to 1.
    return min_total if (min_total is not None and min_total > 0) else 1


async def _runtime_vote_min_total(
    repo: PostgresRepo,
    config: Config,
) -> int:
    # Load runtime vote target from DB setting (fallback to config/env).
    default_target = (
        int(config.voting.min_total)
        if config.voting.min_total is not None and int(config.voting.min_total) > 0
        else 1
    )
    return await repo.get_vote_min_total(default_target=default_target)


async def _runtime_vote_duration_seconds(
    repo: PostgresRepo,
    config: Config,
) -> int:
    # Load runtime vote duration from DB setting (fallback to config/env).
    return await repo.get_vote_duration_seconds(
        default_seconds=int(config.voting.duration_seconds),
    )


def build_application_vote_text(
    application: dict[str, Any],
    branch: str,
) -> str:
    # Build human-readable vote context text for group.
    def _display_value(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text == "-":
            return None
        return text

    def _document_display(app: dict[str, Any]) -> str:
        doc_url = _display_value(app.get("document_url"))
        if doc_url:
            safe_url = html_escape(doc_url, quote=True)
            return f'<a href="{safe_url}">Відкрити документ</a>'
        return "Не вказано"

    def _text_display(value: Any, *, default: str = "-") -> str:
        text = _display_value(value) or default
        return html_escape(text, quote=False)

    branch_label = {
        "matched": "із бота",
        "unlinked": "із сайту, без Telegram-прив'язки",
    }.get(branch, branch)
    branch_label_safe = html_escape(branch_label, quote=False)
    return (
        f"🗳 Нова заявка на розгляд — {branch_label_safe}\n\n"
        f"📄 Заявка #{_text_display(application.get('id'))}\n"
        f"👤 Ім'я: {_text_display(application.get('applicant_name'))}\n"
        f"📞 Телефон: {_text_display(application.get('contact_phone'))}\n"
        f"✉️ Email: {_text_display(application.get('contact_email'))}\n"
        f"🩺 Спеціалізація: {_text_display(application.get('specialization'))}\n"
        f"📎 Документ: {_document_display(application)}\n\n"
        "Оберіть рішення кнопками нижче."
    )


def _is_vote_expired(vote_closes_at: datetime | None) -> bool:
    # Check whether voting deadline has already passed.
    if not isinstance(vote_closes_at, datetime):
        return False
    closes_at = vote_closes_at
    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=timezone.utc)
    return closes_at <= utcnow()


async def start_vote(
    application_id: int,
    application_text: str,
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
) -> dict[str, Any]:
    # Create one-message inline vote and persist vote metadata.
    if not config.voting.chat_id:
        raise RuntimeError("VOTING_CHAT_ID is not configured.")

    app = await repo.get_application_by_id(application_id)
    if not app:
        raise ValueError(f"application {application_id} not found")

    if app.get("vote_status") == VOTE_STATUS_OPEN:
        return app

    runtime_duration_seconds = await _runtime_vote_duration_seconds(repo, config)
    vote_closes_at = (
        utcnow() + timedelta(seconds=runtime_duration_seconds)
        if runtime_duration_seconds > 0
        else None
    )
    send_kwargs: dict[str, Any] = {}
    if config.voting.thread_id is not None:
        send_kwargs["message_thread_id"] = config.voting.thread_id

    contact_url = await _resolve_manual_contact_url(
        repo=repo,
        application_row=app,
    )
    vote_message = await bot.send_message(
        chat_id=config.voting.chat_id,
        text=application_text,
        reply_markup=application_vote_keyboard(
            application_id=application_id,
            yes_count=0,
            no_count=0,
            contact_url=contact_url,
        ),
        parse_mode="HTML",
        **send_kwargs,
    )

    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM application_votes WHERE application_id = $1;",
                application_id,
            )
            row = await conn.fetchrow(
                """
                UPDATE applications
                SET
                    vote_chat_id = $2,
                    vote_message_id = $3,
                    vote_poll_id = NULL,
                    vote_status = $4,
                    vote_closes_at = $5,
                    vote_yes_count = 0,
                    vote_no_count = 0,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING *;
                """,
                application_id,
                int(vote_message.chat.id),
                int(vote_message.message_id),
                VOTE_STATUS_OPEN,
                vote_closes_at,
            )

    if row is None:
        raise ValueError(f"application {application_id} not found during vote start")
    return dict(row)


async def cast_admin_vote(
    repo: PostgresRepo,
    *,
    application_id: int,
    tg_user_id: int,
    approve: bool,
) -> tuple[str, dict[str, Any] | None]:
    # Insert or update admin vote and refresh aggregate counters.
    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            locked = await conn.fetchrow(
                "SELECT * FROM applications WHERE id = $1 FOR UPDATE;",
                application_id,
            )
            if locked is None:
                return VOTE_CAST_NOT_FOUND, None

            if locked.get("vote_status") != VOTE_STATUS_OPEN:
                return VOTE_CAST_CLOSED, dict(locked)

            if _is_vote_expired(locked.get("vote_closes_at")):
                return VOTE_CAST_EXPIRED, dict(locked)

            await conn.execute(
                """
                INSERT INTO application_votes (application_id, tg_user_id, vote, created_at, updated_at)
                VALUES ($1, $2, $3, NOW(), NOW())
                ON CONFLICT (application_id, tg_user_id) DO UPDATE
                SET
                    vote = EXCLUDED.vote,
                    updated_at = NOW();
                """,
                application_id,
                tg_user_id,
                approve,
            )

            counts = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN vote IS TRUE THEN 1 ELSE 0 END), 0)::INT AS yes_count,
                    COALESCE(SUM(CASE WHEN vote IS FALSE THEN 1 ELSE 0 END), 0)::INT AS no_count
                FROM application_votes
                WHERE application_id = $1;
                """,
                application_id,
            )
            yes_count = int(counts["yes_count"]) if counts is not None else 0
            no_count = int(counts["no_count"]) if counts is not None else 0

            updated = await conn.fetchrow(
                """
                UPDATE applications
                SET
                    vote_yes_count = $2,
                    vote_no_count = $3,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING *;
                """,
                application_id,
                yes_count,
                no_count,
            )
            return VOTE_CAST_OK, dict(updated) if updated is not None else None


async def refresh_vote_message_markup(
    bot: Bot,
    *,
    repo: PostgresRepo,
    application_row: dict[str, Any],
) -> None:
    # Refresh inline vote keyboard with current counters.
    chat_id = application_row.get("vote_chat_id")
    message_id = application_row.get("vote_message_id")
    application_id = application_row.get("id")
    if chat_id is None or message_id is None or application_id is None:
        return

    yes_count = int(application_row.get("vote_yes_count") or 0)
    no_count = int(application_row.get("vote_no_count") or 0)
    contact_url = await _resolve_manual_contact_url(
        repo=repo,
        application_row=application_row,
    )

    try:
        await bot.edit_message_reply_markup(
            chat_id=int(chat_id),
            message_id=int(message_id),
            reply_markup=application_vote_keyboard(
                application_id=int(application_id),
                yes_count=yes_count,
                no_count=no_count,
                contact_url=contact_url,
            ),
        )
    except TelegramBadRequest as exc:
        message = (exc.message or "").lower()
        if "message is not modified" in message:
            return
        if "message to edit not found" in message:
            return
        logger.warning(
            "Failed to refresh vote keyboard. application_id=%s error=%s",
            application_id,
            exc.message,
        )
    except TelegramAPIError:
        logger.exception(
            "Telegram API error while refreshing vote keyboard. application_id=%s",
            application_id,
        )


async def notify_vote_result_user(
    bot: Bot,
    application_row: dict[str, Any],
    approved: bool,
) -> None:
    # Notify application owner about vote result.
    tg_user_id = application_row.get("tg_user_id")
    if tg_user_id is None:
        return

    application_id = application_row.get("id")
    if approved:
        if application_row.get("status") == "APPROVED_AWAITING_PAYMENT":
            await notify_user(
                bot=bot,
                user_id=tg_user_id,
                text=(
                    "✅ Вашу заявку схвалено голосуванням спільноти.\n"
                    "Тепер можете перейти до оплати."
                ),
                reply_markup=payment_keyboard(),
                context={
                    "event": "vote_application_approved",
                    "application_id": application_id,
                },
            )
            return
        await notify_user(
            bot=bot,
            user_id=tg_user_id,
            text="✅ Контент заявки схвалено. Очікуйте на наступний крок.",
            context={
                "event": "vote_application_content_approved",
                "application_id": application_id,
            },
        )
        return

    await notify_user(
        bot=bot,
        user_id=tg_user_id,
        text=(
            "❌ Вашу заявку відхилено голосуванням спільноти.\n"
            "Зверніться до адміністраторів для деталей."
        ),
        context={
            "event": "vote_application_rejected",
            "application_id": application_id,
        },
    )


def _resolve_final_status(current_status: str, approved: bool) -> str:
    # Resolve final application status from current status and vote decision.
    if approved:
        if current_status == "APPLICATION_PENDING":
            return "APPROVED_AWAITING_PAYMENT"
        if current_status == "UNLINKED_APPLICATION_PENDING":
            return "UNLINKED_APPLICATION_APPROVED"
        return current_status

    if current_status in {"APPLICATION_PENDING", "UNLINKED_APPLICATION_PENDING"}:
        return "REJECTED"
    return current_status


def _resolve_vote_branch(source: str | None) -> str:
    # Map application source to vote text branch label.
    if source == "site_direct":
        return "unlinked"
    return "matched"


def _build_final_vote_line(
    approved: bool,
    yes_count: int,
    no_count: int,
) -> str:
    # Build concise final decision line for the vote message.
    decision = "СХВАЛЕНО" if approved else "ВІДХИЛЕНО"
    return f"Підсумок голосування: {decision} (За: {yes_count}, Проти: {no_count})"


def _build_final_manual_note(
    application_row: dict[str, Any],
    approved: bool,
) -> str | None:
    # Show manual action hint only for approved site-origin applications.
    if not approved:
        return None
    if str(application_row.get("source") or "") != "site_direct":
        return None
    return (
        "⚠️ Заявку подано із сайту. Після схвалення потрібен ручний контакт із заявником."
    )


async def _resolve_manual_contact_url(
    repo: PostgresRepo,
    application_row: dict[str, Any],
) -> str | None:
    # Resolve Telegram contact URL for manual-contact button.
    tg_user_id = application_row.get("tg_user_id")
    if tg_user_id is None:
        tg_user_id = await repo.find_linked_tg_user_id_by_contacts(
            contact_phone=application_row.get("contact_phone"),
            contact_email=application_row.get("contact_email"),
        )
    if tg_user_id is None:
        return None

    user_row = await repo.get_user_by_tg_user_id(int(tg_user_id))
    username = (user_row or {}).get("username")
    if isinstance(username, str):
        cleaned = username.strip().lstrip("@")
        if cleaned:
            return f"https://t.me/{cleaned}"

    return f"tg://user?id={int(tg_user_id)}"


async def _update_final_vote_message(
    bot: Bot,
    *,
    repo: PostgresRepo,
    application_row: dict[str, Any],
    approved: bool,
) -> None:
    # Edit vote message text on close and remove inline keyboard.
    chat_id = application_row.get("vote_chat_id")
    message_id = application_row.get("vote_message_id")
    application_id = application_row.get("id")
    if chat_id is None or message_id is None or application_id is None:
        return

    yes_count = int(application_row.get("vote_yes_count") or 0)
    no_count = int(application_row.get("vote_no_count") or 0)
    branch = _resolve_vote_branch(str(application_row.get("source") or ""))
    base_text = build_application_vote_text(application_row, branch=branch)
    final_line = _build_final_vote_line(
        approved=approved,
        yes_count=yes_count,
        no_count=no_count,
    )
    manual_note = _build_final_manual_note(
        application_row=application_row,
        approved=approved,
    )
    contact_url = await _resolve_manual_contact_url(
        repo=repo,
        application_row=application_row,
    )
    final_text = f"{base_text}\n\n{final_line}"
    if manual_note:
        final_text = f"{final_text}\n{manual_note}"
    final_reply_markup = (
        application_vote_keyboard(
            application_id=int(application_id),
            yes_count=yes_count,
            no_count=no_count,
            include_vote_buttons=False,
            contact_url=contact_url,
        )
        if approved
        else None
    )

    try:
        await bot.edit_message_text(
            chat_id=int(chat_id),
            message_id=int(message_id),
            text=final_text,
            reply_markup=final_reply_markup,
            parse_mode="HTML",
        )
        return
    except TelegramBadRequest as exc:
        message = (exc.message or "").lower()
        if "message is not modified" in message:
            return
        if "message to edit not found" in message or "message can't be edited" in message:
            return
        logger.warning(
            "Failed to edit finalized vote message. application_id=%s error=%s",
            application_id,
            exc.message,
        )
    except TelegramAPIError:
        logger.exception(
            "Telegram API error while editing finalized vote message. application_id=%s",
            application_id,
        )

    try:
        await bot.edit_message_reply_markup(
            chat_id=int(chat_id),
            message_id=int(message_id),
            reply_markup=final_reply_markup,
        )
    except TelegramBadRequest as exc:
        message = (exc.message or "").lower()
        if "message is not modified" in message or "message to edit not found" in message:
            return
        logger.warning(
            "Failed to remove closed vote keyboard. application_id=%s error=%s",
            application_id,
            exc.message,
        )
    except TelegramAPIError:
        logger.exception(
            "Telegram API error while removing closed vote keyboard. application_id=%s",
            application_id,
        )


async def _finalize_vote_application(
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
    *,
    application_id: int,
) -> bool:
    # Finalize one open application vote and notify applicant.
    approved: bool
    updated_row: dict[str, Any] | None = None
    runtime_min_total = await _runtime_vote_min_total(repo, config)

    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            locked = await conn.fetchrow(
                "SELECT * FROM applications WHERE id = $1 FOR UPDATE;",
                application_id,
            )
            if locked is None or locked["vote_status"] != VOTE_STATUS_OPEN:
                return False

            current_status = str(locked["status"])
            yes_count = int(locked.get("vote_yes_count") or 0)
            no_count = int(locked.get("vote_no_count") or 0)
            approved = calc_vote_result(
                yes_count=yes_count,
                no_count=no_count,
                min_total=runtime_min_total,
                require_yes_gt_no=config.voting.require_yes_gt_no,
            )
            new_status = _resolve_final_status(current_status=current_status, approved=approved)

            updated = await conn.fetchrow(
                """
                UPDATE applications
                SET
                    status = $2,
                    approved_at = CASE
                        WHEN $2 IN ('APPROVED_AWAITING_PAYMENT', 'UNLINKED_APPLICATION_APPROVED')
                            THEN COALESCE(approved_at, NOW())
                        ELSE approved_at
                    END,
                    rejected_at = CASE
                        WHEN $2 = 'REJECTED'
                            THEN COALESCE(rejected_at, NOW())
                        ELSE rejected_at
                    END,
                    vote_status = $3,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING *;
                """,
                application_id,
                new_status,
                VOTE_STATUS_PROCESSED,
            )
            updated_row = dict(updated) if updated is not None else None

    if updated_row is None:
        return False

    await _update_final_vote_message(
        bot=bot,
        repo=repo,
        application_row=updated_row,
        approved=approved,
    )

    await notify_vote_result_user(
        bot=bot,
        application_row=updated_row,
        approved=approved,
    )
    return True


async def finalize_vote_if_target_reached(
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
    *,
    application_row: dict[str, Any],
) -> bool:
    # Finalize vote immediately once yes/no reaches target votes.
    if not application_row:
        return False
    if application_row.get("vote_status") != VOTE_STATUS_OPEN:
        return False

    yes_count = int(application_row.get("vote_yes_count") or 0)
    no_count = int(application_row.get("vote_no_count") or 0)
    target_votes = _target_total_votes(await _runtime_vote_min_total(repo, config))
    if yes_count < target_votes and no_count < target_votes:
        return False

    application_id = application_row.get("id")
    if application_id is None:
        return False
    return await _finalize_vote_application(
        bot=bot,
        config=config,
        repo=repo,
        application_id=int(application_id),
    )


async def close_due_votes(
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
) -> int:
    # Finalize all due inline votes, remove keyboards, and notify users.
    async with repo.pool.acquire() as conn:
        due_rows = await conn.fetch(
            """
            SELECT id
            FROM applications
            WHERE vote_status = 'OPEN'
              AND vote_closes_at IS NOT NULL
              AND vote_closes_at <= NOW()
              AND vote_chat_id IS NOT NULL
              AND vote_message_id IS NOT NULL
            ORDER BY vote_closes_at ASC
            LIMIT 100;
            """
        )

    processed = 0
    for row in due_rows:
        app_id = int(row["id"])
        if await _finalize_vote_application(
            bot=bot,
            config=config,
            repo=repo,
            application_id=app_id,
        ):
            processed += 1

    return processed
