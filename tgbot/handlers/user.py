from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

"""
Basic user utility handlers.

Keeps a lightweight /start response for diagnostics.
"""

user_router = Router()


@user_router.message(CommandStart())
async def user_start(message: Message):
    # Minimal runtime check message with chat/user ids.
    await message.reply(
        "✅ Бот працює.\n"
        f"chat_id={message.chat.id}\n"
        f"user_id={message.from_user.id}"
    )
