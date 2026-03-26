from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message

"""
Config injection middleware.

Adds loaded app config object into handler context data.
"""


class ConfigMiddleware(BaseMiddleware):
    def __init__(self, config) -> None:
        # Keep resolved config for all updates.
        self.config = config

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        # Inject config into middleware/handler pipeline data.
        data["config"] = self.config
        return await handler(event, data)
