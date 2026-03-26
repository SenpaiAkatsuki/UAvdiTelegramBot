from __future__ import annotations

import logging

import asyncpg
from asyncpg import Pool

from tgbot.config import DbConfig

"""
DB pool helpers.

Creates and closes asyncpg pool with startup/shutdown logging.
"""

logger = logging.getLogger(__name__)


async def create_db_pool(db: DbConfig) -> Pool:
    # Open asyncpg pool with configured size bounds.
    pool = await asyncpg.create_pool(
        dsn=db.dsn(),
        min_size=db.min_pool_size,
        max_size=db.max_pool_size,
    )
    logger.info(
        "DB pool created (host=%s port=%s db=%s min=%s max=%s)",
        db.host,
        db.port,
        db.database,
        db.min_pool_size,
        db.max_pool_size,
    )
    return pool


async def close_db_pool(pool: Pool | None) -> None:
    # Close asyncpg pool if initialized.
    if pool is None:
        return
    await pool.close()
    logger.info("DB pool closed")
