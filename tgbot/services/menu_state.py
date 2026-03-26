from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

"""
Menu message tracking storage.

Keeps last sent menu/action messages per user to clear stale inline keyboards.
"""

MENU_STATE_ENTRY = "entry"
MENU_STATE_MENU = "menu"
MENU_STATE_ACTION = "action"


@dataclass(frozen=True)
class TrackedMessage:
    # Telegram message coordinates for later edit/cleanup.
    chat_id: int
    message_id: int


_tracked_messages: Dict[str, Dict[int, TrackedMessage]] = {
    MENU_STATE_ENTRY: {},
    MENU_STATE_MENU: {},
    MENU_STATE_ACTION: {},
}


def get_tracked_message(state: str, tg_user_id: int) -> TrackedMessage | None:
    # Return tracked message for state/user pair.
    return _tracked_messages.get(state, {}).get(tg_user_id)


def remember_tracked_message(
    *,
    state: str,
    tg_user_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    # Save tracked message for state/user pair.
    bucket = _tracked_messages.setdefault(state, {})
    bucket[tg_user_id] = TrackedMessage(chat_id=chat_id, message_id=message_id)


def forget_tracked_message(state: str, tg_user_id: int) -> None:
    # Remove tracked message reference if present.
    bucket = _tracked_messages.get(state)
    if bucket is None:
        return
    bucket.pop(tg_user_id, None)


async def clear_tracked_keyboard(
    *,
    bot: Bot,
    state: str,
    tg_user_id: int,
) -> None:
    # Clear inline keyboard on tracked message and forget reference.
    tracked = get_tracked_message(state, tg_user_id)
    if tracked is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=tracked.chat_id,
            message_id=tracked.message_id,
            reply_markup=None,
        )
    except TelegramAPIError:
        # Ignore missing/deleted/unchanged messages.
        pass
    forget_tracked_message(state, tg_user_id)
