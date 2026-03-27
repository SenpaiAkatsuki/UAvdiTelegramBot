# Step 1: Dependencies Baseline

## Applied changes

- Pinned `aiogram` to stable 3.x: `aiogram==3.26.0`
- Added `asyncpg`
- Added `aiohttp`
- Kept existing useful packages:
  - `environs~=9.0`
  - `redis`
  - `betterlogging`

## Guardrails for this step

- Telegram bot entrypoint remains polling-based (`bot.py` unchanged).
- Weblium webhook server remains a separate process/entrypoint (aiohttp server modules).
- No SQLAlchemy added.
- No Alembic added.
- Python 3.11 compatibility preserved.
