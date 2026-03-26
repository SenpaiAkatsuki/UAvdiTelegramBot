from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.membership import payment_keyboard
from tgbot.services.notify import notify_user

"""
Subscription reminder service.

Sends renewal reminders and optionally enforces removal of expired members.
"""

logger = logging.getLogger(__name__)

REMINDER_DAYS = (20, 10, 5)
REMINDER_LOOP_INTERVAL_SECONDS = 6 * 60 * 60
REMOVABLE_CHAT_MEMBER_STATUSES = {
    ChatMemberStatus.MEMBER.value,
    ChatMemberStatus.RESTRICTED.value,
}


def _format_expiry(expires_at: datetime | None) -> str:
    # Render expiry date in UTC yyyy-mm-dd format.
    if not isinstance(expires_at, datetime):
        return "-"
    expires = (
        expires_at.astimezone(timezone.utc)
        if expires_at.tzinfo is not None
        else expires_at.replace(tzinfo=timezone.utc)
    )
    return expires.strftime("%Y-%m-%d")


def _build_reminder_text(days_left: int, expires_at: datetime | None) -> str:
    # Build reminder text for user notification.
    return (
        f"Your membership expires in {days_left} day(s).\n"
        f"Expires on: {_format_expiry(expires_at)}\n\n"
        "Renew now to keep uninterrupted group access."
    )


def _is_removable_chat_member(status: str | ChatMemberStatus | None) -> bool:
    # Check chat member status eligible for temporary remove/unban flow.
    if isinstance(status, ChatMemberStatus):
        value = status.value
    elif status is None:
        value = ""
    else:
        value = str(status)
    return value in REMOVABLE_CHAT_MEMBER_STATUSES


def _is_admin_user(config: Config, tg_user_id: int | None) -> bool:
    # Skip enforcement/reminders for admin users.
    if tg_user_id is None:
        return False
    tg_bot = getattr(config, "tg_bot", None)
    admin_ids = getattr(tg_bot, "admin_ids", [])
    return int(tg_user_id) in admin_ids


async def send_due_renewal_reminders(
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
) -> int:
    # Send reminders for users with 20/10/5 days left.
    if not config.payments.enabled:
        return 0

    sent_count = 0
    for days_left in REMINDER_DAYS:
        candidates = await repo.get_users_with_subscription_expiring(days_left=days_left)
        for user in candidates:
            tg_user_id = user.get("tg_user_id")
            expires_at = user.get("subscription_expires_at")
            if tg_user_id is None or not isinstance(expires_at, datetime):
                continue
            if _is_admin_user(config, int(tg_user_id)):
                continue

            delivered = await notify_user(
                bot=bot,
                user_id=int(tg_user_id),
                text=_build_reminder_text(days_left=days_left, expires_at=expires_at),
                reply_markup=payment_keyboard(pay_button_text="Renew membership"),
                context={
                    "event": "subscription_renewal_reminder",
                    "days_left": days_left,
                    "tg_user_id": int(tg_user_id),
                },
            )
            if not delivered:
                continue

            inserted = await repo.mark_renewal_notified(
                tg_user_id=int(tg_user_id),
                subscription_expires_at=expires_at,
                days_left=days_left,
            )
            if inserted:
                sent_count += 1

    return sent_count


async def enforce_expired_removal(
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
) -> int:
    # Remove expired users from group (ban+unban) with safety limits.
    if not config.subscription.enforce_expired_removal:
        return 0

    membership_chat_id = config.chat.membership_chat_id
    if not membership_chat_id:
        logger.warning("Expired removal is enabled but CHAT_MEMBERSHIP_CHAT_ID is not set")
        return 0

    max_per_run = config.subscription.enforce_expired_removal_max_per_run
    fetch_limit = max_per_run + 1
    removed_count = 0
    expired_users = await repo.get_users_with_expired_subscription(limit=fetch_limit)
    if not expired_users:
        return 0

    over_limit = len(expired_users) > max_per_run
    users_to_process = expired_users[:max_per_run]
    if over_limit:
        logger.error(
            "Expired removal candidates exceed safety cap. processing_only=%s total_candidates>=%s",
            max_per_run,
            fetch_limit,
        )

    if config.subscription.enforce_expired_removal_dry_run:
        sample_ids = [
            int(user["tg_user_id"])
            for user in users_to_process
            if user.get("tg_user_id") is not None
        ][:5]
        logger.warning(
            "Expired removal dry-run: would remove %s users from chat %s. sample_user_ids=%s",
            len(users_to_process),
            membership_chat_id,
            sample_ids,
        )
        return 0

    for user in users_to_process:
        tg_user_id = user.get("tg_user_id")
        if tg_user_id is None:
            continue
        uid = int(tg_user_id)
        if _is_admin_user(config, uid):
            continue

        try:
            member = await bot.get_chat_member(chat_id=membership_chat_id, user_id=uid)
            if not _is_removable_chat_member(getattr(member, "status", None)):
                continue

            await bot.ban_chat_member(chat_id=membership_chat_id, user_id=uid)
            await bot.unban_chat_member(
                chat_id=membership_chat_id,
                user_id=uid,
                only_if_banned=True,
            )
            removed_count += 1
        except TelegramBadRequest as exc:
            logger.warning(
                "Failed to remove expired user. user_id=%s chat_id=%s error=%s",
                uid,
                membership_chat_id,
                exc.message,
            )
        except TelegramAPIError:
            logger.exception(
                "Telegram API error while removing expired user. user_id=%s chat_id=%s",
                uid,
                membership_chat_id,
            )

    return removed_count


async def subscription_reminder_loop(
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
    stop_event: asyncio.Event,
    interval_seconds: int = REMINDER_LOOP_INTERVAL_SECONDS,
) -> None:
    # Run periodic reminder/enforcement loop until stop event is set.
    last_enforcement_date: date | None = None

    while not stop_event.is_set():
        try:
            reminded = await send_due_renewal_reminders(bot=bot, config=config, repo=repo)
            if reminded:
                logger.info("Sent subscription renewal reminders: %s", reminded)
        except Exception:  # noqa: BLE001
            logger.exception("Subscription reminder iteration failed")

        try:
            today = datetime.now(timezone.utc).date()
            if (
                config.subscription.enforce_expired_removal
                and last_enforcement_date != today
            ):
                removed = await enforce_expired_removal(bot=bot, config=config, repo=repo)
                if removed:
                    logger.info("Expired users removed from group: %s", removed)
                last_enforcement_date = today
        except Exception:  # noqa: BLE001
            logger.exception("Expired membership enforcement iteration failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
