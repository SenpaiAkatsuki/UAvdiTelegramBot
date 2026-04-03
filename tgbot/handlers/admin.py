from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from tgbot.filters.admin import AdminFilter
from tgbot.keyboards.menu import menu_entry_keyboard
from tgbot.services.menu_state import (
    MENU_STATE_ACTION,
    MENU_STATE_ENTRY,
    MENU_STATE_MENU,
    clear_tracked_keyboard,
    remember_tracked_message,
)

"""
Admin utility handlers.

Includes admin start entry and quick commands to read chat/topic ids.
"""

admin_router = Router()


@admin_router.message(AdminFilter(), CommandStart())
async def admin_start(message: Message) -> None:
    # Open admin entry message and clear stale tracked keyboards.
    if message.from_user is not None:
        await clear_tracked_keyboard(
            bot=message.bot,
            state=MENU_STATE_ENTRY,
            tg_user_id=message.from_user.id,
        )
        await clear_tracked_keyboard(
            bot=message.bot,
            state=MENU_STATE_ACTION,
            tg_user_id=message.from_user.id,
        )
        await clear_tracked_keyboard(
            bot=message.bot,
            state=MENU_STATE_MENU,
            tg_user_id=message.from_user.id,
        )
    sent = await message.reply(
        "Admin mode. You can now use the menu.",
        reply_markup=menu_entry_keyboard(is_admin=True),
    )
    if message.from_user is not None:
        remember_tracked_message(
            state=MENU_STATE_ENTRY,
            tg_user_id=message.from_user.id,
            chat_id=sent.chat.id,
            message_id=sent.message_id,
        )


@admin_router.message(AdminFilter(), Command("chatid"))
async def get_chat_id(message: Message) -> None:
    # Return group/supergroup chat id for env setup.
    if message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await message.reply(f"Chat ID: {message.chat.id}")


@admin_router.message(AdminFilter(), Command("topicid"))
async def get_topic_id(message: Message) -> None:
    # Return current topic id in forum chats.
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is None:
        await message.reply("No topic id in this message context.")
        return
    await message.reply(f"Topic id: {thread_id}")
