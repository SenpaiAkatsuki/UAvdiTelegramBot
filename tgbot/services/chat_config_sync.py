from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramMigrateToChat

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo

"""
Runtime chat-id sync helpers.

Stores mutable voting/membership chat ids in DB settings and auto-heals migrated ids.
"""

RUNTIME_VOTING_CHAT_ID_KEY = "runtime_voting_chat_id"
RUNTIME_VOTING_TOPIC_ID_KEY = "runtime_voting_topic_id"
RUNTIME_MEMBERSHIP_CHAT_ID_KEY = "runtime_membership_chat_id"
ACTIVE_CHAT_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}


def _parse_chat_id(value: str | None) -> int | None:
    # Parse positive/negative integer chat id from settings value.
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    if parsed == 0:
        return None
    return parsed


def _parse_topic_id(value: str | None) -> int | None:
    # Parse positive topic/thread id from settings value.
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    if parsed <= 0:
        return None
    return parsed


def _is_active_chat_member_status(status: str | None) -> bool:
    # Check whether Telegram chat-member status means active group membership.
    return str(status or "").strip().lower() in ACTIVE_CHAT_MEMBER_STATUSES


async def load_runtime_chat_overrides(
    *,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Load persisted chat-id overrides from DB settings on startup.
    voting_value = await repo.get_setting(RUNTIME_VOTING_CHAT_ID_KEY)
    voting_chat_id = _parse_chat_id(voting_value)
    if voting_chat_id is not None:
        config.voting.chat_id = voting_chat_id
    voting_topic_value = await repo.get_setting(RUNTIME_VOTING_TOPIC_ID_KEY)
    config.voting.thread_id = _parse_topic_id(voting_topic_value)

    membership_value = await repo.get_setting(RUNTIME_MEMBERSHIP_CHAT_ID_KEY)
    membership_chat_id = _parse_chat_id(membership_value)
    if membership_chat_id is not None:
        config.chat.membership_chat_id = membership_chat_id


async def set_voting_chat_id(
    *,
    repo: PostgresRepo,
    config: Config,
    chat_id: int,
    topic_id: int | None = None,
    updated_by_tg_user_id: int | None = None,
) -> int:
    # Persist voting chat id/topic and apply runtime override immediately.
    resolved = int(chat_id)
    config.voting.chat_id = resolved
    config.voting.thread_id = int(topic_id) if topic_id is not None else None
    await repo.set_setting(
        key=RUNTIME_VOTING_CHAT_ID_KEY,
        value_text=str(resolved),
        updated_by_tg_user_id=updated_by_tg_user_id,
    )
    await repo.set_setting(
        key=RUNTIME_VOTING_TOPIC_ID_KEY,
        value_text=str(config.voting.thread_id or ""),
        updated_by_tg_user_id=updated_by_tg_user_id,
    )
    return resolved


async def set_membership_chat_id(
    *,
    repo: PostgresRepo,
    config: Config,
    chat_id: int,
    updated_by_tg_user_id: int | None = None,
) -> int:
    # Persist membership chat id and apply runtime override immediately.
    resolved = int(chat_id)
    config.chat.membership_chat_id = resolved
    await repo.set_setting(
        key=RUNTIME_MEMBERSHIP_CHAT_ID_KEY,
        value_text=str(resolved),
        updated_by_tg_user_id=updated_by_tg_user_id,
    )
    return resolved


async def resolve_voting_chat_id(
    *,
    bot: Bot,
    config: Config,
    repo: PostgresRepo | None = None,
) -> int | None:
    # Validate voting chat id and auto-update when Telegram returns migrated id.
    chat_id = int(config.voting.chat_id) if config.voting.chat_id else 0
    if chat_id == 0:
        return None

    try:
        await bot.get_chat(chat_id=chat_id)
        return chat_id
    except TelegramMigrateToChat as exc:
        migrated_chat_id = int(exc.migrate_to_chat_id)
        config.voting.chat_id = migrated_chat_id
        if repo is not None:
            await repo.set_setting(
                key=RUNTIME_VOTING_CHAT_ID_KEY,
                value_text=str(migrated_chat_id),
            )
        return migrated_chat_id
    except TelegramAPIError:
        return chat_id


async def resolve_membership_chat_id(
    *,
    bot: Bot,
    config: Config,
    repo: PostgresRepo | None = None,
) -> int | None:
    # Validate membership chat id and auto-update when Telegram returns migrated id.
    chat_id = int(config.chat.membership_chat_id) if config.chat.membership_chat_id else 0
    if chat_id == 0:
        return None

    try:
        await bot.get_chat(chat_id=chat_id)
        return chat_id
    except TelegramMigrateToChat as exc:
        migrated_chat_id = int(exc.migrate_to_chat_id)
        config.chat.membership_chat_id = migrated_chat_id
        if repo is not None:
            await repo.set_setting(
                key=RUNTIME_MEMBERSHIP_CHAT_ID_KEY,
                value_text=str(migrated_chat_id),
            )
        return migrated_chat_id
    except TelegramAPIError:
        return chat_id


async def check_runtime_chat_setup_issues(
    *,
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
) -> list[str]:
    # Validate configured runtime chat ids and return human-readable startup issues.
    issues: list[str] = []

    voting_chat_id = await resolve_voting_chat_id(
        bot=bot,
        config=config,
        repo=repo,
    )
    if not voting_chat_id:
        issues.append("Не задано групу голосування (VOTING_CHAT_ID).")
    else:
        try:
            await bot.get_chat(chat_id=int(voting_chat_id))
        except TelegramAPIError:
            issues.append(
                f"Група голосування недоступна для бота: {int(voting_chat_id)}."
            )

    membership_chat_id = await resolve_membership_chat_id(
        bot=bot,
        config=config,
        repo=repo,
    )
    if not membership_chat_id:
        issues.append("Не задано групу членства (CHAT_MEMBERSHIP_CHAT_ID).")
    else:
        try:
            await bot.get_chat(chat_id=int(membership_chat_id))
        except TelegramAPIError:
            issues.append(
                f"Група членства недоступна для бота: {int(membership_chat_id)}."
            )

    return issues


async def sync_voting_members_snapshot(
    *,
    bot: Bot,
    repo: PostgresRepo,
    config: Config,
    max_candidates: int = 5000,
) -> dict[str, int]:
    # Sync voting_members snapshot from known users and current voting-chat admins.
    voting_chat_id = await resolve_voting_chat_id(
        bot=bot,
        config=config,
        repo=repo,
    )
    if not voting_chat_id:
        return {
            "chat_id": 0,
            "known_candidates": 0,
            "admins_bootstrapped": 0,
            "checked": 0,
            "active": 0,
            "left": 0,
            "failed": 0,
        }

    known_ids = set(
        await repo.list_known_tg_user_ids_for_voting_sync(
            limit=max(int(max_candidates), 1)
        )
    )
    now_utc = datetime.now(timezone.utc)
    active_count = 0
    left_count = 0
    failed_count = 0
    checked_count = 0
    bootstrapped_admins = 0
    admin_ids: set[int] = set()

    try:
        admins = await bot.get_chat_administrators(chat_id=int(voting_chat_id))
    except TelegramAPIError:
        admins = []
        failed_count += 1

    for admin in admins:
        user = getattr(admin, "user", None)
        if user is None or getattr(user, "is_bot", False):
            continue
        uid = int(user.id)
        admin_ids.add(uid)
        known_ids.add(uid)
        await repo.upsert_voting_member(
            tg_user_id=uid,
            username=user.username,
            full_name=user.full_name,
            language_code=user.language_code,
            member_status="ACTIVE",
            verified_at=now_utc,
        )
        active_count += 1
        bootstrapped_admins += 1

    for uid in sorted(known_ids):
        if uid in admin_ids:
            continue
        checked_count += 1
        try:
            member = await bot.get_chat_member(
                chat_id=int(voting_chat_id),
                user_id=int(uid),
            )
        except TelegramAPIError:
            failed_count += 1
            continue

        status = str(getattr(member, "status", "")).strip().lower()
        user = getattr(member, "user", None)
        username = getattr(user, "username", None) if user is not None else None
        full_name = (
            getattr(user, "full_name", None) if user is not None else f"User {int(uid)}"
        )
        language_code = (
            getattr(user, "language_code", None) if user is not None else None
        )

        if _is_active_chat_member_status(status):
            await repo.upsert_voting_member(
                tg_user_id=int(uid),
                username=username,
                full_name=full_name,
                language_code=language_code,
                member_status="ACTIVE",
                verified_at=now_utc,
            )
            active_count += 1
            continue

        updated = await repo.set_voting_member_status(
            tg_user_id=int(uid),
            member_status="LEFT",
            clear_admin=True,
        )
        if updated is not None:
            left_count += 1

    return {
        "chat_id": int(voting_chat_id),
        "known_candidates": len(known_ids),
        "admins_bootstrapped": bootstrapped_admins,
        "checked": checked_count,
        "active": active_count,
        "left": left_count,
        "failed": failed_count,
    }
