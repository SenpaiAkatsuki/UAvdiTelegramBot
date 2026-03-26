from __future__ import annotations

import asyncio
import logging
import os

from asyncpg import Pool

from tgbot.config import Config
from tgbot.db.init_db import apply_schema
from tgbot.db.pool import close_db_pool, create_db_pool

"""
DB bootstrap helpers.

Creates pool with retry/wait strategy and applies schema on startup.
"""

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    # Read positive int from env with fallback.
    raw_value = os.getenv(name, str(default)).strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _env_float(name: str, default: float) -> float:
    # Read positive float from env with fallback.
    raw_value = os.getenv(name, str(default)).strip()
    try:
        parsed = float(raw_value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _is_transient_db_error(exc: Exception) -> bool:
    # Detect temporary DB errors that should be retried.
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return True

    exc_module = exc.__class__.__module__
    if not exc_module.startswith("asyncpg"):
        return False

    transient_error_names = {
        "CannotConnectNowError",
        "PostgresConnectionError",
        "ConnectionDoesNotExistError",
        "TooManyConnectionsError",
    }
    if exc.__class__.__name__ in transient_error_names:
        return True

    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "connection refused",
            "could not connect",
            "the database system is starting up",
            "connection is closed",
            "timeout",
        )
    )


async def init_db(config: Config) -> Pool:
    # Apply schema and create DB pool with retry loop.
    max_attempts = _env_int("DB_WAIT_MAX_ATTEMPTS", 30)
    delay_seconds = _env_float("DB_WAIT_DELAY_SECONDS", 2.0)
    attempt = 1

    while True:
        try:
            await apply_schema(config.db)
            return await create_db_pool(config.db)
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_attempts or not _is_transient_db_error(exc):
                raise

            logger.warning(
                "Database is not ready yet (attempt %s/%s): %s. Retrying in %.1f seconds.",
                attempt,
                max_attempts,
                exc,
                delay_seconds,
            )
            await asyncio.sleep(delay_seconds)
            attempt += 1


async def shutdown_db(pool: Pool | None) -> None:
    # Close DB pool gracefully.
    await close_db_pool(pool)
