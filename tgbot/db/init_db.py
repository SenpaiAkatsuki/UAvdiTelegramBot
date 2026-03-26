import asyncio
from pathlib import Path

import asyncpg
from environs import Env

from tgbot.config import DbConfig

"""
Schema application utility.

Applies SQL schema file directly to PostgreSQL.
"""


def _schema_path() -> Path:
    # Resolve schema.sql path next to this module.
    return Path(__file__).with_name("schema.sql")


async def apply_schema(db: DbConfig, schema_path: Path | None = None) -> None:
    # Execute full schema SQL in a single transaction.
    path = schema_path or _schema_path()
    schema_sql = path.read_text(encoding="utf-8")

    conn = await asyncpg.connect(dsn=db.dsn())
    try:
        async with conn.transaction():
            await conn.execute(schema_sql)
    finally:
        await conn.close()


def load_db_config(path: str = ".env") -> DbConfig:
    # Load DB config only for schema utility usage.
    env = Env()
    env.read_env(path)
    return DbConfig.from_env(env)


async def main() -> None:
    # CLI helper to apply schema from local .env.
    await apply_schema(load_db_config(".env"))
    print(f"Schema applied: {_schema_path()}")


if __name__ == "__main__":
    # Run schema utility as standalone script.
    asyncio.run(main())
