# Step 5: asyncpg Pool Lifecycle And Repo Middleware Injection

## Added files

- `tgbot/db/pool.py`
- `tgbot/db/init.py`
- `tgbot/middlewares/db.py`

## Updated file

- `bot.py`

## What was implemented

### Pool lifecycle

- `create_db_pool(db: DbConfig)` in `tgbot/db/pool.py`
- `close_db_pool(pool)` in `tgbot/db/pool.py`
- `init_db(config: Config)` in `tgbot/db/init.py`:
  - applies schema (`apply_schema`)
  - creates asyncpg pool
- `shutdown_db(pool)` in `tgbot/db/init.py`

### Middleware injection

- Added `DbMiddleware` in `tgbot/middlewares/db.py`
- Injects into aiogram `data`:
  - `db_pool`
  - `repo` (`PostgresRepo`)

### Middleware registration

`bot.py` registers global middlewares for:

- messages
- callback queries
- chat join requests
- pre-checkout queries

### Polling/webhook boundary

- Bot remains polling-only (`dp.start_polling(...)`).
- No Telegram webhook mode added.
- aiohttp Weblium server remains separate from bot startup.
