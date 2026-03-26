# UAvdi Telegram Bot

Telegram bot project with:

- `aiogram v3` (polling mode),
- `asyncpg` + PostgreSQL (no ORM),
- `aiohttp` webhook app for Weblium and LiqPay callbacks.

## What This Bot Does

This bot automates a closed membership flow:

1. A person submits an application from site or bot link.
2. Admins review and vote in Telegram.
3. Approved users pay yearly subscription through LiqPay.
4. After successful callback verification, subscription is activated.
5. User gets access to the private Telegram group.

It also includes:

- renewal reminders before expiry,
- admin inline panel for member monitoring,
- runtime subscription price control from admin menu.

## Stack

- Bot runtime: `bot.py`
- Webhook runtime: `python -m infrastructure.api.weblium_app`
- Database schema: `tgbot/db/schema.sql` (applied automatically on startup)

## Requirements

- Python 3.11+
- PostgreSQL 14+
- Telegram bot token and admin user IDs

## Local run (without Docker)

1. Rename `.env.dist` to `.env` and fill required values.
2. Configure DB connection in `.env`:
   - `DB_HOST`
   - `DB_PORT`
   - `DB_USER`
   - `DB_PASSWORD`
   - `DB_NAME`
3. Make sure PostgreSQL is running and the database from `DB_NAME` exists.
4. Install dependencies:
   - `pip install -r requirements.txt`
5. Start webhook app:
   - `python -m infrastructure.api.weblium_app`
6. Start bot:
   - `python bot.py`

## Docker Compose run

Current compose stack includes:

- `bot`
- `weblium_app`
- `pg_database`
- `adminer` (bound to `127.0.0.1:8081`)

Run:

- `docker compose up --build`

## Notes

- Project does not use SQLAlchemy or Alembic.
- Payments are handled by LiqPay checkout + callback verification.
- Subscription and admin menu logic are in `tgbot/handlers` and `tgbot/services`.
