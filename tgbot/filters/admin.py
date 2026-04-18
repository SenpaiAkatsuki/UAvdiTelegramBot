from aiogram.filters import BaseFilter
from aiogram.types import Message

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.services.admin_access import is_admin_user, is_static_admin

"""
Admin access filter.

Checks whether message sender is in configured admin ids list.
"""


class AdminFilter(BaseFilter):
    # Toggle for direct admin / not-admin matching.
    is_admin: bool = True

    async def __call__(
        self,
        obj: Message,
        config: Config,
        repo: PostgresRepo | None = None,
    ) -> bool:
        # Resolve sender against effective admin ids (env + DB).
        if obj.from_user is None:
            return False

        if repo is None:
            resolved = is_static_admin(config, obj.from_user.id)
        else:
            resolved = await is_admin_user(
                repo=repo,
                config=config,
                tg_user_id=obj.from_user.id,
            )
        return resolved == self.is_admin
