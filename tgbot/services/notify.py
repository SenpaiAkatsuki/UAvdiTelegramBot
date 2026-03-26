import json
import logging
from typing import Any, Mapping, Sequence

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from tgbot.services import broadcaster

"""
Notification service wrappers.

Adds structured logging and event-specific helpers over broadcaster utilities.
"""

logger = logging.getLogger(__name__)


def _to_json(data: Mapping[str, Any] | None = None) -> str:
    # Convert context payload to stable JSON log string.
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True, default=str)


def _log(event: str, **fields: Any) -> None:
    # Structured log helper for notification events.
    logger.info("%s %s", event, _to_json(fields))


def _build_admin_message(title: str, payload: Mapping[str, Any] | None = None) -> str:
    # Build compact admin notification body.
    body = _to_json(payload)
    return f"{title}\n{body}"


async def notify_user(
    bot: Bot,
    user_id: int | str,
    text: str,
    disable_notification: bool = False,
    reply_markup: InlineKeyboardMarkup | None = None,
    context: Mapping[str, Any] | None = None,
) -> bool:
    # Send message to a single user with start/done logs.
    _log("notify_user.start", user_id=user_id, context=context or {})
    ok = await broadcaster.send_message(
        bot=bot,
        user_id=user_id,
        text=text,
        disable_notification=disable_notification,
        reply_markup=reply_markup,
    )
    _log("notify_user.done", user_id=user_id, ok=ok, context=context or {})
    return ok


async def notify_admins(
    bot: Bot,
    admin_ids: Sequence[int | str],
    text: str,
    disable_notification: bool = False,
    reply_markup: InlineKeyboardMarkup | None = None,
    context: Mapping[str, Any] | None = None,
) -> int:
    # Broadcast message to all configured admins with logging.
    admin_list = list(admin_ids)
    _log("notify_admins.start", admins=admin_list, context=context or {})
    sent_count = await broadcaster.broadcast(
        bot=bot,
        users=admin_list,
        text=text,
        disable_notification=disable_notification,
        reply_markup=reply_markup,
    )
    _log(
        "notify_admins.done",
        admins=admin_list,
        sent_count=sent_count,
        context=context or {},
    )
    return sent_count


async def notify_admins_matched_application(
    bot: Bot,
    admin_ids: Sequence[int | str],
    application: Mapping[str, Any],
) -> int:
    # Notify admins about matched application webhook.
    return await notify_admins(
        bot=bot,
        admin_ids=admin_ids,
        text=_build_admin_message("Matched application received", application),
        context={"event": "matched_application"},
    )


async def notify_admins_unlinked_application(
    bot: Bot,
    admin_ids: Sequence[int | str],
    application: Mapping[str, Any],
) -> int:
    # Notify admins about unlinked application webhook.
    return await notify_admins(
        bot=bot,
        admin_ids=admin_ids,
        text=_build_admin_message("Unlinked application received", application),
        context={"event": "unlinked_application"},
    )


async def notify_admins_bind_confirmation_request(
    bot: Bot,
    admin_ids: Sequence[int | str],
    bind_request: Mapping[str, Any],
) -> int:
    # Notify admins when user requests bind confirmation.
    return await notify_admins(
        bot=bot,
        admin_ids=admin_ids,
        text=_build_admin_message("Bind confirmation request", bind_request),
        context={"event": "bind_confirmation_request"},
    )


async def notify_admins_approval(
    bot: Bot,
    admin_ids: Sequence[int | str],
    application: Mapping[str, Any],
) -> int:
    # Notify admins that application has been approved.
    return await notify_admins(
        bot=bot,
        admin_ids=admin_ids,
        text=_build_admin_message("Application approved", application),
        context={"event": "application_approved"},
    )


async def notify_admins_rejection(
    bot: Bot,
    admin_ids: Sequence[int | str],
    application: Mapping[str, Any],
) -> int:
    # Notify admins that application has been rejected.
    return await notify_admins(
        bot=bot,
        admin_ids=admin_ids,
        text=_build_admin_message("Application rejected", application),
        context={"event": "application_rejected"},
    )


async def notify_admins_payment_ready(
    bot: Bot,
    admin_ids: Sequence[int | str],
    application: Mapping[str, Any],
) -> int:
    # Notify admins that application is ready for payment.
    return await notify_admins(
        bot=bot,
        admin_ids=admin_ids,
        text=_build_admin_message("Payment-ready notification", application),
        context={"event": "payment_ready"},
    )
