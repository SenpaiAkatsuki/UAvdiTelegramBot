from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.membership import payment_keyboard
from tgbot.services.notify import notify_user

"""
Application voting service.

Starts group polls for applications and finalizes results after vote timeout.
"""

logger = logging.getLogger(__name__)

VOTE_STATUS_OPEN = "OPEN"
VOTE_STATUS_PROCESSED = "PROCESSED"


def utcnow() -> datetime:
    # Current UTC timestamp helper.
    return datetime.now(timezone.utc)


def calc_vote_result(
    yes_count: int,
    no_count: int,
    min_total: int | None,
    require_yes_gt_no: bool,
) -> bool:
    # Calculate approval decision from poll counts and thresholds.
    total = yes_count + no_count
    if min_total is not None and total < min_total:
        return False
    if require_yes_gt_no:
        return yes_count > no_count
    return yes_count >= no_count


def build_application_vote_text(
    application: dict[str, Any],
    branch: str,
) -> str:
    # Build human-readable poll context text for group.
    return (
        f"Application review request ({branch})\n"
        f"application_id={application.get('id')}\n"
        f"tg_user_id={application.get('tg_user_id') or '-'}\n"
        f"name={application.get('applicant_name') or '-'}\n"
        f"phone={application.get('contact_phone') or '-'}\n"
        f"email={application.get('contact_email') or '-'}\n"
        f"specialization={application.get('specialization') or '-'}\n"
        f"document={application.get('document_file_name') or '-'}\n"
        "Vote in the poll below."
    )


async def start_vote(
    application_id: int,
    application_text: str,
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
) -> dict[str, Any]:
    # Create poll for application and save vote metadata in DB.
    if not config.voting.chat_id:
        raise RuntimeError("VOTING_CHAT_ID is not configured.")

    app = await repo.get_application_by_id(application_id)
    if not app:
        raise ValueError(f"application {application_id} not found")

    if app.get("vote_status") == VOTE_STATUS_OPEN:
        return app

    vote_closes_at = utcnow() + timedelta(seconds=config.voting.duration_seconds)
    send_kwargs: dict[str, Any] = {}
    if config.voting.thread_id is not None:
        send_kwargs["message_thread_id"] = config.voting.thread_id

    summary = await bot.send_message(
        chat_id=config.voting.chat_id,
        text=application_text,
        **send_kwargs,
    )
    poll_message = await bot.send_poll(
        chat_id=config.voting.chat_id,
        question=f"Approve application #{application_id}?",
        options=["Approve", "Reject"],
        is_anonymous=False,
        allows_multiple_answers=False,
        reply_to_message_id=summary.message_id,
        **send_kwargs,
    )

    async with repo.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE applications
            SET
                vote_chat_id = $2,
                vote_message_id = $3,
                vote_poll_id = $4,
                vote_status = $5,
                vote_closes_at = $6,
                vote_yes_count = NULL,
                vote_no_count = NULL,
                updated_at = NOW()
            WHERE id = $1
            RETURNING *;
            """,
            application_id,
            int(poll_message.chat.id),
            int(poll_message.message_id),
            poll_message.poll.id if poll_message.poll else None,
            VOTE_STATUS_OPEN,
            vote_closes_at,
        )
    if row is None:
        raise ValueError(f"application {application_id} not found during vote start")
    return dict(row)


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
                    "Your application was approved by the community vote.\n"
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
            text="Your application content is approved and still pending next steps.",
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
            "Your application was rejected by the community vote.\n"
            "You can contact admins for details."
        ),
        context={
            "event": "vote_application_rejected",
            "application_id": application_id,
        },
    )


async def close_due_votes(
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
) -> int:
    # Stop due polls, update application status, and notify users.
    async with repo.pool.acquire() as conn:
        due_rows = await conn.fetch(
            """
            SELECT *
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
        app = dict(row)
        app_id = int(app["id"])
        chat_id = int(app["vote_chat_id"])
        message_id = int(app["vote_message_id"])

        try:
            poll = await bot.stop_poll(chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest as exc:
            message = (exc.message or "").lower()
            if "poll has already been closed" in message:
                logger.info(
                    "Vote poll already closed by Telegram/admin. application_id=%s",
                    app_id,
                )
                continue
            if "message to edit not found" in message:
                logger.warning(
                    "Vote poll message missing. application_id=%s chat_id=%s message_id=%s",
                    app_id,
                    chat_id,
                    message_id,
                )
                continue
            logger.warning("Failed to stop poll for application_id=%s: %s", app_id, exc.message)
            continue
        except TelegramAPIError:
            logger.exception("Telegram API error while stopping poll. application_id=%s", app_id)
            continue

        options = poll.options if poll else []
        yes_count = int(options[0].voter_count) if len(options) > 0 else 0
        no_count = int(options[1].voter_count) if len(options) > 1 else 0
        approved = calc_vote_result(
            yes_count=yes_count,
            no_count=no_count,
            min_total=config.voting.min_total,
            require_yes_gt_no=config.voting.require_yes_gt_no,
        )

        async with repo.pool.acquire() as conn:
            async with conn.transaction():
                locked = await conn.fetchrow(
                    "SELECT * FROM applications WHERE id = $1 FOR UPDATE;",
                    app_id,
                )
                if locked is None or locked["vote_status"] != VOTE_STATUS_OPEN:
                    continue

                current_status = str(locked["status"])
                if approved:
                    if current_status == "APPLICATION_PENDING":
                        new_status = "APPROVED_AWAITING_PAYMENT"
                    elif current_status == "UNLINKED_APPLICATION_PENDING":
                        new_status = "UNLINKED_APPLICATION_APPROVED"
                    else:
                        new_status = current_status
                else:
                    if current_status in {"APPLICATION_PENDING", "UNLINKED_APPLICATION_PENDING"}:
                        new_status = "REJECTED"
                    else:
                        new_status = current_status

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
                        vote_yes_count = $4,
                        vote_no_count = $5,
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING *;
                    """,
                    app_id,
                    new_status,
                    VOTE_STATUS_PROCESSED,
                    yes_count,
                    no_count,
                )

        if updated is not None:
            processed += 1
            await notify_vote_result_user(
                bot=bot,
                application_row=dict(updated),
                approved=approved,
            )

    return processed
