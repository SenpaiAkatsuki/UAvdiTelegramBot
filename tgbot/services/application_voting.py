from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
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
    branch_label = {
        "matched": "from bot",
        "unlinked": "from site (no Telegram link)",
    }.get(branch, branch)
    return (
        f"🗳 New application for review ({branch_label})\n\n"
        f"📄 Application ID #{application.get('id')}\n"
        f"👤 Name: {application.get('applicant_name') or '-'}\n"
        f"📞 Phone: {application.get('contact_phone') or '-'}\n"
        f"✉️ Email: {application.get('contact_email') or '-'}\n"
        f"🩺 Specialization: {application.get('specialization') or '-'}\n"
        f"📎 Document: {application.get('document_file_name') or '-'}\n\n"
        "Choose a decision using buttons below."
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

    vote_message = await bot.send_message(
        chat_id=config.voting.chat_id,
        text=application_text,
        reply_markup=application_vote_keyboard(
            application_id=application_id,
            yes_count=0,
            no_count=0,
        ),
        parse_mode=None,
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

    try:
        await bot.edit_message_reply_markup(
            chat_id=int(chat_id),
            message_id=int(message_id),
            reply_markup=application_vote_keyboard(
                application_id=int(application_id),
                yes_count=yes_count,
                no_count=no_count,
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
                    "✅ Your application has been approved by community vote.\n"
                    "You can now proceed to payment."
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
            text="✅ Your application content is approved. Please wait for the next step.",
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
            "❌ Your application was rejected by community vote.\n"
            "Contact admins for details."
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

    chat_id = updated_row.get("vote_chat_id")
    message_id = updated_row.get("vote_message_id")
    if chat_id is not None and message_id is not None:
        try:
            await bot.edit_message_reply_markup(
                chat_id=int(chat_id),
                message_id=int(message_id),
                reply_markup=None,
            )
        except TelegramBadRequest as exc:
            message = (exc.message or "").lower()
            if "message is not modified" not in message and "message to edit not found" not in message:
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
