from __future__ import annotations

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo

"""
Admin access helpers.

Combines static admins from env with runtime admins stored in database.
"""


def is_static_admin(config: Config, tg_user_id: int) -> bool:
    # Check immutable admin list from config env.
    return int(tg_user_id) in config.tg_bot.admin_ids


async def is_admin_user(
    *,
    repo: PostgresRepo,
    config: Config,
    tg_user_id: int,
) -> bool:
    # Check effective admin access: env list or DB flag.
    if is_static_admin(config, tg_user_id):
        return True
    return await repo.is_bot_admin(int(tg_user_id))


async def get_effective_admin_ids(
    *,
    repo: PostgresRepo,
    config: Config,
) -> list[int]:
    # Return union of env admins and DB admins.
    static_ids = {int(uid) for uid in config.tg_bot.admin_ids}
    dynamic_ids = set(await repo.list_bot_admin_ids())
    return sorted(static_ids | dynamic_ids)

