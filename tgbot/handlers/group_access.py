from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
)
from aiogram.types import CallbackQuery, ChatJoinRequest, ChatMemberUpdated

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.menu import menu_entry_keyboard
from tgbot.keyboards.membership import payment_keyboard
from tgbot.services.membership_access import has_payment_exemption, is_user_blocked
from tgbot.services.admin_access import is_admin_user
from tgbot.services.chat_config_sync import resolve_membership_chat_id
from tgbot.services.chat_config_sync import set_membership_chat_id
from tgbot.services.menu_state import (
    MENU_STATE_ACTION,
    clear_tracked_keyboard,
    remember_tracked_message,
)

"""
Group access handlers.

Creates one-time invite links and validates join requests by status/subscription.
"""

logger = logging.getLogger(__name__)

group_access_router = Router()

ELIGIBLE_GROUP_ACCESS_STATUSES = {
    "PAID_AWAITING_JOIN",
    "ACTIVE_MEMBER",
}
JOIN_APPROVAL_ELIGIBLE_STATUSES = {
    "PAID_AWAITING_JOIN",
    "ACTIVE_MEMBER",
}
GROUP_ACCESS_LINK_TTL_HOURS = 72
ACTIVE_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}
LEFT_MEMBER_STATUS = ChatMemberStatus.LEFT.value
KICKED_MEMBER_STATUS = ChatMemberStatus.KICKED.value
PRIVATE_WELCOME_TEXT = (
    "✅ Вітаємо! Ви успішно приєдналися до спільноти UAVDI.\n\n"
    "Доступ активовано, тепер ви офіційний учасник асоціації."
)


def has_active_subscription(user_row: dict | None) -> bool:
    # Check ACTIVE status with non-expired subscription date.
    if not user_row:
        return False
    if str(user_row.get("subscription_status")) != "ACTIVE":
        return False
    expires_at = user_row.get("subscription_expires_at")
    if not isinstance(expires_at, datetime):
        return False
    return expires_at > datetime.now(timezone.utc)


def _is_active_member_status(status: str | None) -> bool:
    # Return True for statuses considered current chat membership.
    return str(status or "").strip().lower() in ACTIVE_MEMBER_STATUSES


def _became_active_member(event: ChatMemberUpdated) -> bool:
    # Detect transition from non-member to member/admin/owner/restricted.
    old_status = getattr(event.old_chat_member, "status", None)
    new_status = getattr(event.new_chat_member, "status", None)
    return (not _is_active_member_status(old_status)) and _is_active_member_status(new_status)


def _became_inactive_member(event: ChatMemberUpdated) -> bool:
    # Detect transition from member/admin/owner/restricted to non-member.
    old_status = getattr(event.old_chat_member, "status", None)
    new_status = getattr(event.new_chat_member, "status", None)
    return _is_active_member_status(old_status) and (not _is_active_member_status(new_status))


def _became_left_member(event: ChatMemberUpdated) -> bool:
    # Detect voluntary leave from active membership.
    old_status = getattr(event.old_chat_member, "status", None)
    new_status = str(getattr(event.new_chat_member, "status", "")).strip().lower()
    return _is_active_member_status(old_status) and new_status == LEFT_MEMBER_STATUS


def _became_kicked_member(event: ChatMemberUpdated) -> bool:
    # Detect forced removal (kick/ban) from active membership.
    old_status = getattr(event.old_chat_member, "status", None)
    new_status = str(getattr(event.new_chat_member, "status", "")).strip().lower()
    return _is_active_member_status(old_status) and new_status == KICKED_MEMBER_STATUS


async def safe_message_user(
    query_or_request: CallbackQuery | ChatJoinRequest,
    user_id: int,
    text: str,
    reply_markup=None,
) -> None:
    # Send direct message to user without breaking main flow on Telegram errors.
    try:
        await query_or_request.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
        )
    except TelegramForbiddenError:
        return
    except TelegramAPIError:
        logger.exception("Failed to send user notification. user_id=%s", user_id)


async def remove_callback_keyboard(query: CallbackQuery) -> None:
    # Remove inline keyboard from source message after action handling.
    if query.message is None:
        return
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        return


async def send_tracked_action_message(
    query: CallbackQuery,
    *,
    text: str,
    reply_markup=None,
) -> None:
    # Send action message and keep it tracked for future cleanup.
    if query.from_user is None:
        return
    await clear_tracked_keyboard(
        bot=query.bot,
        state=MENU_STATE_ACTION,
        tg_user_id=query.from_user.id,
    )
    if query.message is None:
        sent = await query.bot.send_message(
            chat_id=query.from_user.id,
            text=text,
            reply_markup=reply_markup,
        )
    else:
        sent = await query.message.answer(text, reply_markup=reply_markup)
    if reply_markup is not None:
        remember_tracked_message(
            state=MENU_STATE_ACTION,
            tg_user_id=query.from_user.id,
            chat_id=sent.chat.id,
            message_id=sent.message_id,
        )


@group_access_router.callback_query(F.data == "membership_get_group_access")
async def membership_get_group_access(
    query: CallbackQuery,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Validate user eligibility and create single-use invite link.
    await query.answer()

    if query.from_user is None:
        if query.message is not None:
            await query.message.answer("⚠️ Не вдалося визначити користувача.")
            await remove_callback_keyboard(query)
        return
    is_admin_user_flag = await is_admin_user(
        repo=repo,
        config=config,
        tg_user_id=query.from_user.id,
    )
    is_payment_exempt = await has_payment_exemption(
        bot=query.bot,
        config=config,
        tg_user_id=query.from_user.id,
        repo=repo,
    )

    membership_chat_id = await resolve_membership_chat_id(
        bot=query.bot,
        config=config,
        repo=repo,
    )
    if not membership_chat_id:
        if query.message is not None:
            await query.message.answer("⚠️ Групу членства ще не налаштовано.")
            await remove_callback_keyboard(query)
        return

    # Hard protection: kicked members cannot request a new invite link.
    try:
        member = await query.bot.get_chat_member(
            chat_id=int(membership_chat_id),
            user_id=int(query.from_user.id),
        )
        membership_status = str(getattr(member, "status", "")).strip().lower()
    except TelegramAPIError:
        membership_status = ""
    force_new_invite_after_leave = membership_status == LEFT_MEMBER_STATUS

    if membership_status == KICKED_MEMBER_STATUS and not is_admin_user_flag:
        await repo.block_user_access_from_membership_removal(
            tg_user_id=int(query.from_user.id),
            full_name=query.from_user.full_name,
            username=query.from_user.username,
            language_code=query.from_user.language_code,
        )
        await send_tracked_action_message(
            query,
            text="⛔️ Доступ до спільноти обмежено адміністратором.",
        )
        await remove_callback_keyboard(query)
        return

    panel_data = await repo.get_user_panel_data(tg_user_id=query.from_user.id)
    status = str(panel_data.get("application_status") or "") if panel_data else ""
    if not is_admin_user_flag and is_user_blocked(panel_data):
        await send_tracked_action_message(
            query,
            text="⛔️ Доступ до бота обмежено адміністратором.",
        )
        await remove_callback_keyboard(query)
        return

    is_subscription_active = has_active_subscription(panel_data) or is_payment_exempt
    is_legacy_active_member = (
        is_subscription_active and status in {"", "NEW", "APPLICATION_REQUIRED"}
    )

    if (
        not status
        and not is_admin_user_flag
        and not is_payment_exempt
        and not is_subscription_active
    ):
        await send_tracked_action_message(
            query,
            text="⚠️ Заявку не знайдено. Зверніться до адміністратора.",
        )
        await remove_callback_keyboard(query)
        return

    is_exempt_approved_without_payment = (
        is_payment_exempt and status == "APPROVED_AWAITING_PAYMENT"
    )

    if (
        not is_admin_user_flag
        and not is_payment_exempt
        and not is_legacy_active_member
        and status not in ELIGIBLE_GROUP_ACCESS_STATUSES
        and not is_exempt_approved_without_payment
    ):
        if status == "ACTIVE_MEMBER":
            await send_tracked_action_message(
                query,
                text="ℹ️ Для вашого акаунта посилання доступу вже було видано.",
            )
        else:
            await send_tracked_action_message(
                query,
                text=f"⚠️ Доступ до групи недоступний для поточного статусу: {status}.",
            )
        await remove_callback_keyboard(query)
        return
    if not is_admin_user_flag and not is_subscription_active:
        await send_tracked_action_message(
            query,
            text="⏳ Потрібно продовжити підписку: термін дії завершився.",
            reply_markup=payment_keyboard(pay_button_text="💳 Продовжити підписку"),
        )
        await remove_callback_keyboard(query)
        return

    cached_invite = await repo.get_user_membership_invite(query.from_user.id)
    cached_link = ""
    cached_expires_at = None
    if cached_invite:
        raw_link = cached_invite.get("last_membership_invite_link")
        raw_expires_at = cached_invite.get("last_membership_invite_expires_at")
        if isinstance(raw_link, str):
            cached_link = raw_link.strip()
        if isinstance(raw_expires_at, datetime):
            cached_expires_at = raw_expires_at
            if cached_expires_at.tzinfo is None:
                cached_expires_at = cached_expires_at.replace(tzinfo=timezone.utc)
            else:
                cached_expires_at = cached_expires_at.astimezone(timezone.utc)

    now_utc = datetime.now(timezone.utc)
    if (
        cached_link
        and (cached_expires_at is None or cached_expires_at > now_utc)
        and not force_new_invite_after_leave
    ):
        if query.message is not None:
            await query.message.answer(
                "🔐 Використайте це одноразове посилання, щоб приєднатися до приватної групи:\n"
                f"{cached_link}\n\n"
                f"⏱ Посилання дійсне {GROUP_ACCESS_LINK_TTL_HOURS} годин і працює лише один раз."
            )
        await clear_tracked_keyboard(
            bot=query.bot,
            state=MENU_STATE_ACTION,
            tg_user_id=query.from_user.id,
        )
        await remove_callback_keyboard(query)
        return

    invite_expire_at = datetime.now(timezone.utc) + timedelta(hours=GROUP_ACCESS_LINK_TTL_HOURS)
    target_chat_id = membership_chat_id
    if cached_link:
        try:
            await query.bot.revoke_chat_invite_link(
                chat_id=target_chat_id,
                invite_link=cached_link,
            )
        except TelegramBadRequest:
            pass
        except TelegramAPIError:
            logger.warning(
                "Failed to revoke previous membership invite link. user_id=%s chat_id=%s",
                query.from_user.id,
                target_chat_id,
            )
    try:
        invite = await query.bot.create_chat_invite_link(
            chat_id=target_chat_id,
            name=f"membership_join_{query.from_user.id}",
            creates_join_request=False,
            member_limit=1,
            expire_date=invite_expire_at,
        )
    except TelegramMigrateToChat as exc:
        target_chat_id = int(exc.migrate_to_chat_id)
        logger.warning(
            "Membership chat migrated: configured=%s migrated_to=%s. "
            "Updating runtime chat id, please update CHAT_MEMBERSHIP_CHAT_ID in .env.",
            membership_chat_id,
            target_chat_id,
        )
        config.chat.membership_chat_id = target_chat_id
        await set_membership_chat_id(
            repo=repo,
            config=config,
            chat_id=target_chat_id,
        )
        try:
            invite = await query.bot.create_chat_invite_link(
                chat_id=target_chat_id,
                name=f"membership_join_{query.from_user.id}",
                creates_join_request=False,
                member_limit=1,
                expire_date=invite_expire_at,
            )
        except TelegramAPIError:
            logger.exception(
                "Failed to create join-request invite link after migration. "
                "user_id=%s old_chat_id=%s new_chat_id=%s",
                query.from_user.id,
                membership_chat_id,
                target_chat_id,
            )
            await send_tracked_action_message(
                query,
                text="⚠️ Зараз не вдається створити посилання для входу в групу. Спробуйте пізніше.",
            )
            return
    except TelegramAPIError:
        logger.exception(
            "Failed to create join-request invite link. user_id=%s chat_id=%s",
            query.from_user.id,
            target_chat_id,
        )
        await send_tracked_action_message(
            query,
            text="⚠️ Зараз не вдається створити посилання для входу в групу. Спробуйте пізніше.",
        )
        return

    await repo.set_user_membership_invite(
        tg_user_id=query.from_user.id,
        invite_link=invite.invite_link,
        invite_expires_at=invite_expire_at,
    )
    if query.message is not None:
        await query.message.answer(
            "🔐 Використайте це одноразове посилання, щоб приєднатися до приватної групи:\n"
            f"{invite.invite_link}\n\n"
            f"⏱ Посилання дійсне {GROUP_ACCESS_LINK_TTL_HOURS} годин і працює лише один раз."
        )
    await clear_tracked_keyboard(
        bot=query.bot,
        state=MENU_STATE_ACTION,
        tg_user_id=query.from_user.id,
    )
    await remove_callback_keyboard(query)


@group_access_router.chat_join_request()
async def handle_membership_join_request(
    join_request: ChatJoinRequest,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Approve/decline join request based on membership status and subscription.
    membership_chat_id = config.chat.membership_chat_id
    if not membership_chat_id or join_request.chat.id != membership_chat_id:
        return

    tg_user_id = join_request.from_user.id
    is_admin_user_flag = await is_admin_user(
        repo=repo,
        config=config,
        tg_user_id=tg_user_id,
    )
    is_payment_exempt = await has_payment_exemption(
        bot=join_request.bot,
        config=config,
        tg_user_id=tg_user_id,
        repo=repo,
    )

    panel_data = await repo.get_user_panel_data(tg_user_id=tg_user_id)
    status = str(panel_data.get("application_status") or "") if panel_data else ""
    is_blocked_user = is_user_blocked(panel_data)
    is_subscription_active = has_active_subscription(panel_data) or is_payment_exempt
    is_join_status_eligible = (
        status in JOIN_APPROVAL_ELIGIBLE_STATUSES
        or is_payment_exempt
    )

    if is_blocked_user and not is_admin_user_flag:
        try:
            await join_request.bot.decline_chat_join_request(
                chat_id=join_request.chat.id,
                user_id=tg_user_id,
            )
        except TelegramBadRequest:
            return
        except TelegramAPIError:
            logger.exception("Telegram error while declining blocked join request. user_id=%s", tg_user_id)
            return
        await safe_message_user(
            join_request,
            tg_user_id,
            "⛔️ Доступ до спільноти обмежено адміністратором.",
        )
        return

    if is_admin_user_flag or (is_join_status_eligible and is_subscription_active and not is_blocked_user):
        try:
            await join_request.bot.approve_chat_join_request(
                chat_id=join_request.chat.id,
                user_id=tg_user_id,
            )
        except TelegramBadRequest as exc:
            message = (exc.message or "").lower()
            if "already" not in message:
                logger.warning(
                    "Failed to approve join request. user_id=%s error=%s",
                    tg_user_id,
                    exc.message,
                )
                return
        except TelegramAPIError:
            logger.exception("Telegram error while approving join request. user_id=%s", tg_user_id)
            return

        await repo.activate_membership_from_group_entry(
            tg_user_id=tg_user_id,
            full_name=join_request.from_user.full_name,
            username=join_request.from_user.username,
            language_code=join_request.from_user.language_code,
        )

        return

    try:
        await join_request.bot.decline_chat_join_request(
            chat_id=join_request.chat.id,
            user_id=tg_user_id,
        )
    except TelegramBadRequest:
        return
    except TelegramAPIError:
        logger.exception("Telegram error while declining join request. user_id=%s", tg_user_id)
        return

    await safe_message_user(
        join_request,
        tg_user_id,
        "❌ Ваш запит на вступ відхилено. Продовжіть підписку, щоб відновити доступ до групи.",
    )

@group_access_router.chat_member()
async def handle_membership_chat_member_update(
    event: ChatMemberUpdated,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Sync DB if member was added to membership group manually.
    membership_chat_id = await resolve_membership_chat_id(
        bot=event.bot,
        config=config,
        repo=repo,
    )
    if not membership_chat_id or int(event.chat.id) != int(membership_chat_id):
        return
    if event.new_chat_member.user.is_bot:
        return

    user = event.new_chat_member.user
    if _became_active_member(event):
        try:
            await repo.activate_membership_from_group_entry(
                tg_user_id=int(user.id),
                full_name=user.full_name,
                username=user.username,
                language_code=user.language_code,
            )
            is_admin_flag = await is_admin_user(
                repo=repo,
                config=config,
                tg_user_id=int(user.id),
            )
            await event.bot.send_message(
                chat_id=int(user.id),
                text=PRIVATE_WELCOME_TEXT,
                reply_markup=menu_entry_keyboard(is_admin=is_admin_flag),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to sync manual group entry. user_id=%s chat_id=%s",
                user.id,
                event.chat.id,
            )
        return

    if _became_kicked_member(event):
        try:
            await repo.block_user_access_from_membership_removal(
                tg_user_id=int(user.id),
                full_name=user.full_name,
                username=user.username,
                language_code=user.language_code,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to block removed member. user_id=%s chat_id=%s",
                user.id,
                event.chat.id,
            )
        return

    if _became_left_member(event):
        try:
            # Voluntary leave: keep account active, only drop stale cached invite.
            await repo.clear_user_membership_invite(
                tg_user_id=int(user.id),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to clear invite after voluntary leave. user_id=%s chat_id=%s",
                user.id,
                event.chat.id,
            )


@group_access_router.chat_member()
async def handle_voting_chat_member_update(
    event: ChatMemberUpdated,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Sync dedicated voting_members table from voting-group join/leave updates.
    voting_chat_id = config.voting.chat_id
    if not voting_chat_id or int(event.chat.id) != int(voting_chat_id):
        return
    if event.new_chat_member.user.is_bot:
        return

    user = event.new_chat_member.user
    now_utc = datetime.now(timezone.utc)

    if _became_active_member(event):
        try:
            await repo.upsert_voting_member(
                tg_user_id=int(user.id),
                username=user.username,
                full_name=user.full_name,
                language_code=user.language_code,
                member_status="ACTIVE",
                verified_at=now_utc,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to mark active voting member. user_id=%s chat_id=%s",
                user.id,
                event.chat.id,
            )
        return

    if _became_inactive_member(event):
        try:
            updated = await repo.set_voting_member_status(
                tg_user_id=int(user.id),
                member_status="LEFT",
                clear_admin=True,
            )
            if updated is None:
                await repo.upsert_voting_member(
                    tg_user_id=int(user.id),
                    username=user.username,
                    full_name=user.full_name,
                    language_code=user.language_code,
                    member_status="LEFT",
                    is_bot_admin=False,
                    verified_at=now_utc,
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to mark inactive voting member. user_id=%s chat_id=%s",
                user.id,
                event.chat.id,
            )
        return

    # Keep profile data fresh for active members on role/metadata updates.
    if _is_active_member_status(getattr(event.new_chat_member, "status", None)):
        try:
            await repo.upsert_voting_member(
                tg_user_id=int(user.id),
                username=user.username,
                full_name=user.full_name,
                language_code=user.language_code,
                member_status="ACTIVE",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to refresh voting member profile. user_id=%s chat_id=%s",
                user.id,
                event.chat.id,
            )
