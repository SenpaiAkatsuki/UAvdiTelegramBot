from aiogram.filters import BaseFilter
from aiogram.types import Message

from tgbot.config import Config

"""
Admin access filter.

Checks whether message sender is in configured admin ids list.
"""


class AdminFilter(BaseFilter):
    # Toggle for direct admin / not-admin matching.
    is_admin: bool = True

    async def __call__(self, obj: Message, config: Config) -> bool:
        # Resolve sender against configured admin ids.
        return (obj.from_user.id in config.tg_bot.admin_ids) == self.is_admin
