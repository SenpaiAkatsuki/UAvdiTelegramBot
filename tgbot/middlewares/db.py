from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from asyncpg import Pool

from tgbot.db.repo import PostgresRepo

"""
DB repository middleware.

Injects asyncpg pool and repository object into update context.
"""


class DbMiddleware(BaseMiddleware):
    def __init__(self, db_pool: Pool) -> None:
        # Build reusable repository instance for handlers.
        self.db_pool = db_pool
        self.repo = PostgresRepo(db_pool)

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        # Expose DB pool and repo to downstream handlers.
        data["db_pool"] = self.db_pool
        data["repo"] = self.repo
        return await handler(event, data)
