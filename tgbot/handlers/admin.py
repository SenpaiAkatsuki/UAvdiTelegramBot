from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.filters.admin import AdminFilter
from tgbot.keyboards.menu import menu_entry_keyboard
from tgbot.services.chat_config_sync import (
    set_membership_chat_id,
    set_voting_chat_id,
    sync_voting_members_snapshot,
)
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


def _parse_command_chat_id(message: Message) -> int | None:
    # Parse optional numeric chat id from command argument.
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    raw = parts[1].strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _resolve_target_chat_id(message: Message) -> tuple[int | None, bool]:
    # Resolve target chat id from argument or current group chat.
    arg_chat_id = _parse_command_chat_id(message)
    if arg_chat_id is not None:
        return arg_chat_id, True
    if message.chat.type in {"group", "supergroup"}:
        return int(message.chat.id), True
    return None, False


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
        "🛠 Режим адміністратора увімкнено.\nВідкрийте меню для керування.",
        reply_markup=menu_entry_keyboard(is_admin=True),
    )
    if message.from_user is not None:
        remember_tracked_message(
            state=MENU_STATE_ENTRY,
            tg_user_id=message.from_user.id,
            chat_id=sent.chat.id,
            message_id=sent.message_id,
        )


@admin_router.message(AdminFilter(), Command("topicid"))
async def get_topic_id(message: Message) -> None:
    # Return current topic id in forum chats.
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is None:
        await message.reply("⚠️ У цьому повідомленні немає topic id.")
        return
    await message.reply(f"🧵 Topic id: {thread_id}")


@admin_router.message(AdminFilter(), Command("set_voting_chat"))
async def admin_set_voting_chat(
    message: Message,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Set runtime voting chat id and topic id (if command is sent inside a forum topic).
    chat_id, ok = _resolve_target_chat_id(message)
    if not ok or chat_id is None:
        await message.reply(
            "⚠️ Використання: /set_voting_chat у потрібній групі\n"
            "або /set_voting_chat -1001234567890 у приватному чаті."
        )
        return

    updated_by = message.from_user.id if message.from_user is not None else None
    topic_id = getattr(message, "message_thread_id", None)
    resolved = await set_voting_chat_id(
        repo=repo,
        config=config,
        chat_id=chat_id,
        topic_id=int(topic_id) if topic_id is not None else None,
        updated_by_tg_user_id=updated_by,
    )
    sync_result = await sync_voting_members_snapshot(
        bot=message.bot,
        repo=repo,
        config=config,
    )
    topic_line = (
        f"🧵 Topic id: {int(topic_id)}"
        if topic_id is not None
        else "🧵 Topic id: не встановлено (публікація в основний чат)"
    )
    await message.reply(
        "✅ Група голосування встановлена: "
        f"{resolved}\n"
        f"{topic_line}\n\n"
        "🔄 Синхронізацію voting-members запущено:\n"
        f"• Відомих ID для перевірки: {sync_result.get('known_candidates', 0)}\n"
        f"• Активних учасників: {sync_result.get('active', 0)}\n"
        f"• Позначено як LEFT: {sync_result.get('left', 0)}\n"
        f"• Помилок перевірки: {sync_result.get('failed', 0)}\n\n"
        "ℹ️ Telegram API не дозволяє отримати повний список учасників групи.\n"
        "Синхронізація бере адмінів групи + тих, хто вже є у voting_members/голосуваннях.\n"
        "Решта учасників досинхронізується автоматично під час /start або join/leave подій."
    )


@admin_router.message(AdminFilter(), Command("set_membership_chat"))
async def admin_set_membership_chat(
    message: Message,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Set runtime membership chat id from current group chat or explicit argument.
    chat_id, ok = _resolve_target_chat_id(message)
    if not ok or chat_id is None:
        await message.reply(
            "⚠️ Використання: /set_membership_chat у потрібній групі\n"
            "або /set_membership_chat -1001234567890 у приватному чаті."
        )
        return

    updated_by = message.from_user.id if message.from_user is not None else None
    resolved = await set_membership_chat_id(
        repo=repo,
        config=config,
        chat_id=chat_id,
        updated_by_tg_user_id=updated_by,
    )
    await message.reply(f"✅ Група членства встановлена: {resolved}")
