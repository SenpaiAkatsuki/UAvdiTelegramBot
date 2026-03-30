# UAvdi Telegram Bot

Telegram bot for closed membership management:
application intake, admin moderation, payment, subscription lifecycle, and private group access.

## Product Overview

This project is built for organizations that need a controlled member onboarding flow in Telegram.

Core idea:
1. A person submits an application (from site or bot entry link).
2. Admins review and vote on that application in Telegram.
3. Approved person pays yearly subscription through LiqPay.
4. Bot confirms callback, activates subscription, and gives group access.
5. Bot sends renewal reminders and can enforce expired-access rules.

## Who Uses It

- Applicant/User: applies, pays, renews, gets group access.
- Admin: reviews and votes applications, sees member lists, edits subscription price.
- Operator/Dev: runs bot + webhook + database, configures env and deployment.

## What Users and Admins See

- User side:
  - `/start` entry flow
  - one-message inline menu
  - profile with subscription status and days left
  - pay/renew actions and group access action

- Admin side:
  - inline admin panel (active/expiring/expired lists)
  - inline vote buttons for applications
  - runtime subscription price update from menu

## Main Functional Blocks

- Weblium webhook intake (linked and unlinked applications)
- Bind/self-bind flow by contact data
- Admin voting flow with idempotent state transitions
- LiqPay checkout page + callback signature verification
- 365-day subscriptions with 20/10/5-day reminders
- Group join gating by subscription state
- Optional expired-user removal job
- Anti-spam throttling middleware

## Architecture (High Level)

- Bot runtime: `bot.py` (`aiogram` polling)
- Webhook runtime: `python -m infrastructure.api.weblium_app` (`aiohttp`)
- Data layer: `asyncpg` + PostgreSQL (`tgbot/db/repo.py`, no ORM)
- Schema: `tgbot/db/schema.sql` (auto-applied on startup)

## Technical Setup

### Requirements

- Python 3.11+
- PostgreSQL 14+
- Telegram bot token
- LiqPay keys (if payments enabled)
- Public webhook base URL (for site + LiqPay callbacks)

### Environment

1. Rename `.env.dist` to `.env`.
2. Fill all required values.
3. Minimum critical variables to verify:
   - `BOT_TOKEN`
   - `ADMINS`
   - DB settings: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
   - `CHAT_MEMBERSHIP_CHAT_ID` (member group)
   - `VOTING_CHAT_ID` (admin voting group)
   - `WEBHOOK_ENABLED`, `WEBHOOK_WEBLIUM_SECRET`
   - `PAYMENTS_ENABLED` and `LIQPAY_*` values (if payment flow enabled)

Note:
- Recommended mode is separate chats for voting and membership.
- One-chat legacy mode is possible only with `VOTING_ALLOW_SHARED_CHAT=true`.

### Run Locally (without Docker)

1. Install deps:
   - `pip install -r requirements.txt`
2. Ensure PostgreSQL is running and DB exists.
3. Start webhook app:
   - `python -m infrastructure.api.weblium_app`
4. Start bot:
   - `python bot.py`

### Run with Docker Compose

Compose services:
- `bot`
- `weblium_app`
- `pg_database`
- `pg_backup` (automatic compressed Postgres dumps)
- `adminer` (`127.0.0.1:8081`)

Start:
- `docker compose up -d --build`

### Automatic DB Backups (Compose)

- Backups run automatically in `pg_backup` service.
- Dump format: compressed custom dump (`pg_dump -Fc -Z6`).
- Controlled by env:
  - `BACKUP_INTERVAL_SECONDS` (default `86400`)
  - `BACKUP_RETENTION_DAYS` (default `14`)

Check backup logs:
- `docker compose logs -f pg_backup`

List backup files inside backup volume:
- `docker compose exec pg_backup sh -lc "ls -lah /backups"`

Restore from a dump file:
- `docker compose exec -T pg_database sh -lc "pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --clean --if-exists /backups/<dump_file>.dump"`

## Testing and CI

- Install test deps:
  - `pip install -r requirements.txt -r requirements-dev.txt`
- Run tests:
  - `pytest`
- Optional smoke tests:
  - PowerShell: `$env:RUN_WEBLIUM_SMOKE_TESTS=1; pytest`
  - Bash: `RUN_WEBLIUM_SMOKE_TESTS=1 pytest`
- CI workflow:
  - `.github/workflows/ci.yml` runs compile + tests on push/PR

## Implementation Notes

- No SQLAlchemy, no Alembic.
- DB is source of truth for statuses/panels.
- Payments are LiqPay checkout + verified server callback.
- Weblium webhook supports optional signature validation:
  `WEBHOOK_SIGNATURE_SECRET`, `WEBHOOK_SIGNATURE_HEADER`.
