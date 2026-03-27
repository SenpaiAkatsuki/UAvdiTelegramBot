import asyncio
import logging
from contextlib import suppress

import betterlogging as bl
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from aiogram.types import ErrorEvent

from tgbot.config import Config, load_config
from tgbot.db.init import init_db, shutdown_db
from tgbot.db.repo import PostgresRepo
from tgbot.handlers import routers_list
from tgbot.middlewares.config import ConfigMiddleware
from tgbot.middlewares.db import DbMiddleware
from tgbot.middlewares.throttling import ThrottlingMiddleware
from tgbot.services import broadcaster
from tgbot.services.application_voting import close_due_votes
from tgbot.services.subscription_reminders import subscription_reminder_loop

"""
Bot entrypoint.

Initializes config, DB, middlewares, routers, and background loops for polling mode.
"""

VOTE_CLOSER_INTERVAL_SECONDS = 45


async def on_startup(bot: Bot, admin_ids: list[int]):
    # Send startup notification to admins.
    await broadcaster.broadcast(bot, admin_ids, "✅ Бота запущено.")


async def on_error(event: ErrorEvent) -> bool:
    # Catch unhandled update errors and respond with safe fallback text.
    logger = logging.getLogger(__name__)
    update_id = getattr(event.update, "update_id", None)
    logger.exception(
        "Unhandled bot update error. update_id=%s",
        update_id,
        exc_info=event.exception,
    )

    callback_query = getattr(event.update, "callback_query", None)
    if callback_query is not None:
        try:
            await callback_query.answer(
                "⚠️ Тимчасова помилка бота. Спробуйте ще раз.",
                show_alert=True,
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "Failed to answer callback after unhandled exception",
                exc_info=True,
            )
        return True

    message = getattr(event.update, "message", None)
    if message is not None:
        try:
            await message.answer(
                "⚠️ Тимчасова помилка бота. Спробуйте ще раз.",
                parse_mode=None,
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "Failed to notify user about unhandled message exception",
                exc_info=True,
            )

    return True


def register_global_middlewares(dp: Dispatcher, config: Config, db_pool=None):
    # Register shared middlewares for messages, callbacks, and join requests.
    middleware_types = [
        ConfigMiddleware(config),
        ThrottlingMiddleware(config.throttling),
        DbMiddleware(db_pool) if db_pool else None,
    ]

    observers = [
        dp.message,
        dp.callback_query,
        dp.chat_join_request,
    ]

    for middleware_type in middleware_types:
        if middleware_type is None:
            continue
        for observer in observers:
            observer.outer_middleware(middleware_type)


def setup_logging():
    # Configure readable colored logging for runtime diagnostics.
    log_level = logging.INFO
    bl.basic_colorized_config(level=log_level)

    logging.basicConfig(
        level=logging.INFO,
        format="%(filename)s:%(lineno)d #%(levelname)-8s [%(asctime)s] - %(name)s - %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting bot")


def get_storage(config):
    # Select FSM storage backend by config.
    if config.tg_bot.use_redis:
        return RedisStorage.from_url(
            config.redis.dsn(),
            key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True),
        )
    else:
        return MemoryStorage()


async def vote_closer_loop(
    bot: Bot,
    config: Config,
    repo: PostgresRepo,
    stop_event: asyncio.Event,
) -> None:
    # Periodically close due voting polls.
    while not stop_event.is_set():
        try:
            await close_due_votes(bot=bot, config=config, repo=repo)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("Vote closer loop iteration failed")

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=VOTE_CLOSER_INTERVAL_SECONDS,
            )
        except TimeoutError:
            continue


async def main():
    # Main application flow for polling mode.
    setup_logging()

    config = load_config(".env")
    db_pool = await init_db(config)

    storage = get_storage(config)

    bot = Bot(
        token=config.tg_bot.token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=storage)
    dp.errors.register(on_error)

    dp.include_routers(*routers_list)

    register_global_middlewares(dp, config, db_pool=db_pool)
    repo = PostgresRepo(db_pool)
    vote_loop_stop = asyncio.Event()
    reminder_loop_stop = asyncio.Event()
    vote_loop_task = asyncio.create_task(
        vote_closer_loop(
            bot=bot,
            config=config,
            repo=repo,
            stop_event=vote_loop_stop,
        )
    )
    reminder_loop_task = asyncio.create_task(
        subscription_reminder_loop(
            bot=bot,
            config=config,
            repo=repo,
            stop_event=reminder_loop_stop,
        )
    )

    try:
        await on_startup(bot, config.tg_bot.admin_ids)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        vote_loop_stop.set()
        reminder_loop_stop.set()
        vote_loop_task.cancel()
        reminder_loop_task.cancel()
        with suppress(asyncio.CancelledError):
            await vote_loop_task
        with suppress(asyncio.CancelledError):
            await reminder_loop_task
        await shutdown_db(db_pool)


if __name__ == "__main__":
    # Run async bot process and handle graceful stop.
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.error("⛔ Бота зупинено.")
