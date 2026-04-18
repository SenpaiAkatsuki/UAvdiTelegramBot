from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.services.chat_config_sync import resolve_voting_chat_id

"""
Membership access helpers.

Central checks for voting-group membership and payment-exempt access rules.
"""

ACTIVE_CHAT_MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR.value,
    ChatMemberStatus.ADMINISTRATOR.value,
    ChatMemberStatus.MEMBER.value,
    ChatMemberStatus.RESTRICTED.value,
}
VOTING_MEMBER_VERIFY_TTL_HOURS = 12


def _is_active_chat_member_status(status: str | ChatMemberStatus | None) -> bool:
    # Return True for statuses treated as current membership.
    if isinstance(status, ChatMemberStatus):
        value = status.value
    elif status is None:
        value = ""
    else:
        value = str(status).strip().lower()
    return value in ACTIVE_CHAT_MEMBER_STATUSES


async def is_voting_group_member(
    *,
    bot: Bot,
    config: Config,
    tg_user_id: int,
) -> bool:
    # Check if user currently belongs to configured voting group.
    voting_chat_id = await resolve_voting_chat_id(
        bot=bot,
        config=config,
    )
    if not voting_chat_id:
        return False

    try:
        member = await bot.get_chat_member(
            chat_id=int(voting_chat_id),
            user_id=int(tg_user_id),
        )
    except TelegramAPIError:
        return False

    return _is_active_chat_member_status(getattr(member, "status", None))


async def get_voting_group_membership_state(
    *,
    bot: Bot,
    config: Config,
    tg_user_id: int,
    repo: PostgresRepo | None = None,
) -> bool | None:
    # Tri-state voting-group check: True/False, None when Telegram API is unavailable.
    voting_chat_id = await resolve_voting_chat_id(
        bot=bot,
        config=config,
        repo=repo,
    )
    if not voting_chat_id:
        return False

    try:
        member = await bot.get_chat_member(
            chat_id=int(voting_chat_id),
            user_id=int(tg_user_id),
        )
    except TelegramAPIError:
        return None

    return _is_active_chat_member_status(getattr(member, "status", None))


async def has_payment_exemption(
    *,
    bot: Bot,
    config: Config,
    tg_user_id: int,
    repo: PostgresRepo | None = None,
) -> bool:
    # Payment exemption only for active voting-group members.
    if repo is None:
        state = await get_voting_group_membership_state(
            bot=bot,
            config=config,
            tg_user_id=int(tg_user_id),
            repo=None,
        )
        return state is True

    uid = int(tg_user_id)
    member_row = await repo.get_voting_member(uid)
    now_utc = datetime.now(timezone.utc)
    stale_cutoff = now_utc - timedelta(hours=VOTING_MEMBER_VERIFY_TTL_HOURS)

    live_state = await get_voting_group_membership_state(
        bot=bot,
        config=config,
        tg_user_id=uid,
        repo=repo,
    )

    if live_state is True:
        await repo.upsert_voting_member(
            tg_user_id=uid,
            member_status="ACTIVE",
            verified_at=now_utc,
        )
        return True

    if live_state is False:
        if member_row and str(member_row.get("member_status") or "").strip().upper() != "LEFT":
            await repo.set_voting_member_status(
                tg_user_id=uid,
                member_status="LEFT",
                clear_admin=False,
            )
        return False

    # Telegram API unavailable: fallback only to fresh verified cache.
    if member_row:
        cached_status = str(member_row.get("member_status") or "").strip().upper()
        cached_verified_at = member_row.get("last_verified_at")
        if isinstance(cached_verified_at, datetime):
            if cached_verified_at.tzinfo is None:
                cached_verified_at = cached_verified_at.replace(tzinfo=timezone.utc)
            else:
                cached_verified_at = cached_verified_at.astimezone(timezone.utc)
        else:
            cached_verified_at = None
        if (
            cached_status == "ACTIVE"
            and cached_verified_at is not None
            and cached_verified_at >= stale_cutoff
        ):
            return True

    # Fail closed: no exemption when live membership cannot be confirmed.
    if member_row and str(member_row.get("member_status") or "").strip().upper() == "ACTIVE":
        await repo.set_voting_member_status(
            tg_user_id=uid,
            member_status="LEFT",
            clear_admin=False,
        )
        return False
    return False


def is_user_blocked(user_row: dict | None) -> bool:
    # Blocked users are manually restricted regardless of payment/subscription.
    if not user_row:
        return False
    return str(user_row.get("subscription_status") or "").strip().upper() == "BLOCKED"
