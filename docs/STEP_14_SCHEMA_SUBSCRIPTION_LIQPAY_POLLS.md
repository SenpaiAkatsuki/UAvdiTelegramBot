# Step 14: Schema Update for Subscription, LiqPay, and Group Voting

## Updated files

- `tgbot/db/schema.sql`
- `tgbot/db/init_db.py`

## What was added

### Users table

Added fields:

- `member_since TIMESTAMPTZ NULL`
- `subscription_expires_at TIMESTAMPTZ NULL`
- `subscription_status TEXT NOT NULL DEFAULT 'NONE'`

### Applications table

Added fields:

- `vote_chat_id BIGINT NULL`
- `vote_message_id INTEGER NULL`
- `vote_poll_id TEXT NULL`
- `vote_status TEXT NULL`
- `vote_closes_at TIMESTAMPTZ NULL`
- `vote_yes_count INTEGER NULL`
- `vote_no_count INTEGER NULL`

### Payments table (LiqPay-ready)

Kept existing Telegram-compatible fields and added:

- `provider_order_id TEXT NULL`
- `provider_status TEXT NULL`
- `callback_data TEXT NULL`
- `callback_signature TEXT NULL`
- `signature_valid BOOLEAN NULL`

### New table

`renewal_notifications`:

- `tg_user_id BIGINT REFERENCES users(tg_user_id)`
- `subscription_expires_at TIMESTAMPTZ NOT NULL`
- `days_left INTEGER NOT NULL`
- `sent_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- `UNIQUE(tg_user_id, subscription_expires_at, days_left)`

## Indexes added

- `idx_users_subscription_expires_at` on `users(subscription_expires_at)`
- `idx_applications_vote_status_closes_at` on `applications(vote_status, vote_closes_at)`
- `uq_payments_provider_order_id_not_null` unique partial index:
  - `(provider, provider_order_id)` where `provider_order_id IS NOT NULL`

## Idempotent migration behavior

- `schema.sql` now includes `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` blocks.
- Existing rows are normalized where needed (`provider`, `subscription_status`).
- `apply_schema()` executes the full schema inside a transaction on each startup.

No ORM and no Alembic were introduced.
