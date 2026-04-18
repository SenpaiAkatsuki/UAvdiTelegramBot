# Step 4: asyncpg Repository Layer For Final Application Flow

## Added file

- `tgbot/db/repo.py`

## Repository class

- `PostgresRepo(pool: asyncpg.Pool)`

All queries are asyncpg-only and parameterized (`$1`, `$2`, ...).

## Standard methods

Users:

- `create_or_update_user`
- `get_user_by_tg_user_id`

Applications:

- `get_application_by_id`
- `update_application_status`

Payments:

- `create_payment`
- `get_payment_by_id`
- `update_payment_status`

## Application token methods

- `create_application_token`
- `get_active_application_token`
- `get_token_record`
- `mark_application_token_used`

## Bind token methods

- `create_bind_token`
- `get_bind_token`
- `mark_bind_token_used`

## Webhook event methods

- `webhook_event_exists`
- `create_webhook_event`
- `mark_webhook_event_processed`

## Application ingestion methods

- `create_matched_application_from_webhook`
- `create_unlinked_application_from_webhook`
- `get_unlinked_application_candidates_by_phone`
- `get_unlinked_application_candidates_by_email`
- `bind_application_to_tg_user`

## Transaction guarantees

Implemented transaction scopes for:

- token consume + application creation (`create_matched_application_from_webhook`)
- bind flow + status transition (`bind_application_to_tg_user`)
- payment state updates + related application status changes (`update_payment_status`)
