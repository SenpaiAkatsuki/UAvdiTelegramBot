# Step 3: PostgreSQL Schema For Two-Branch Application Flow

## Scope

Designed and added an idempotent PostgreSQL schema for:

- Branch A: bot user goes to site with `tg_token` and can be auto-matched.
- Branch B: site-direct submission without `tg_token` and later binding/manual handling.

## Added files

- `tgbot/db/schema.sql`
- `tgbot/db/init_db.py`
- `tgbot/db/__init__.py`

## Tables

Core:

- `users`
- `applications`
- `payments`

Correlation / binding:

- `application_tokens` (bot -> site correlation)
- `bind_tokens` (site-direct app -> bot bind token)

Webhook audit / dedup:

- `webhook_events` (provider + event_key uniqueness)

## Applications fields

`applications` includes:

- `source` (`bot_link`, `site_direct`, `manual`)
- nullable `tg_user_id`
- `contact_phone`
- `contact_email`
- `applicant_name`
- `specialization`
- `document_url`
- `document_file_name`

## Status model

Enforced with SQL CHECK constraint:

- `NEW`
- `APPLICATION_REQUIRED`
- `APPLICATION_PENDING`
- `UNLINKED_APPLICATION_PENDING`
- `UNLINKED_APPLICATION_APPROVED`
- `APPROVED_AWAITING_PAYMENT`
- `PAID_AWAITING_JOIN`
- `ACTIVE_MEMBER`
- `REJECTED`

## Idempotency

Schema is plain SQL and idempotent:

- `CREATE TABLE IF NOT EXISTS`
- `CREATE INDEX IF NOT EXISTS`

## Minimal DB init

Use:

```bash
python -m tgbot.db.init_db
```

This applies `tgbot/db/schema.sql` using asyncpg and DB settings from `.env`.
