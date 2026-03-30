from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
)
from aiogram.types import CallbackQuery, ChatJoinRequest

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.membership import payment_keyboard
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
}
JOIN_APPROVAL_ELIGIBLE_STATUSES = {
    "PAID_AWAITING_JOIN",
    "ACTIVE_MEMBER",
}
GROUP_ACCESS_LINK_TTL_HOURS = 72


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


async def safe_message_user(
    query_or_request: CallbackQuery | ChatJoinRequest,
    user_id: int,
    text: str,
) -> None:
    # Send direct message to user without breaking main flow on Telegram errors.
    try:
        await query_or_request.bot.send_message(chat_id=user_id, text=text)
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
            await query.message.answer("Unable to identify user.")
            await remove_callback_keyboard(query)
        return
    is_admin_user = query.from_user.id in config.tg_bot.admin_ids

    membership_chat_id = config.chat.membership_chat_id
    if not membership_chat_id:
        if query.message is not None:
            await query.message.answer("Membership group is not configured yet.")
            await remove_callback_keyboard(query)
        return

    panel_data = await repo.get_user_panel_data(tg_user_id=query.from_user.id)
    status = str(panel_data.get("application_status") or "") if panel_data else ""
    application_id = (
        int(panel_data["application_id"])
        if panel_data and panel_data.get("application_id") is not None
        else None
    )

    if not status and not is_admin_user:
        await send_tracked_action_message(
            query,
            text="Application not found. Please contact admin.",
        )
        await remove_callback_keyboard(query)
        return

    is_subscription_active = has_active_subscription(panel_data)

    if not is_admin_user and status not in ELIGIBLE_GROUP_ACCESS_STATUSES:
        if status == "ACTIVE_MEMBER":
            await send_tracked_action_message(
                query,
                text="Group access link was already issued for your account.",
            )
        else:
            await send_tracked_action_message(
                query,
                text=f"Group access is not available for your current status: {status}.",
            )
        await remove_callback_keyboard(query)
        return
    if not is_admin_user and not is_subscription_active:
        await send_tracked_action_message(
            query,
            text="Renew required: your subscription is expired.",
            reply_markup=payment_keyboard(pay_button_text="Renew membership"),
        )
        await remove_callback_keyboard(query)
        return

    invite_expire_at = datetime.now(timezone.utc) + timedelta(hours=GROUP_ACCESS_LINK_TTL_HOURS)
    target_chat_id = membership_chat_id
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
                text="Unable to create group invite link right now. Please try again later.",
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
            text="Unable to create group invite link right now. Please try again later.",
        )
        return

    if status == "PAID_AWAITING_JOIN" and application_id is not None:
        await repo.update_application_status(
            application_id=application_id,
            status="ACTIVE_MEMBER",
        )

    if query.message is not None:
        await query.message.answer(
            "Use this one-time link to join the private group:\n"
            f"{invite.invite_link}\n\n"
            f"This link expires in {GROUP_ACCESS_LINK_TTL_HOURS} hours and can be used only once."
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
    is_admin_user = tg_user_id in config.tg_bot.admin_ids

    panel_data = await repo.get_user_panel_data(tg_user_id=tg_user_id)
    status = str(panel_data.get("application_status") or "") if panel_data else ""
    application_id = (
        int(panel_data["application_id"])
        if panel_data and panel_data.get("application_id") is not None
        else None
    )
    is_subscription_active = has_active_subscription(panel_data)

    if is_admin_user or (status in JOIN_APPROVAL_ELIGIBLE_STATUSES and is_subscription_active):
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

        if application_id is not None and status == "PAID_AWAITING_JOIN":
            await repo.update_application_status(
                application_id=application_id,
                status="ACTIVE_MEMBER",
            )

        await safe_message_user(
            join_request,
            tg_user_id,
            "Your join request was approved. Welcome.",
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
        "Your join request was declined. Renew your membership to restore group access.",
    )
