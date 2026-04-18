# Step 19: Docker Compose + Final E2E Test Checklist

## Goal

Make local infra startup reliable with Docker Compose and document a final end-to-end validation checklist.

## Updated files

- `docker-compose.yml`
- `tgbot/db/init.py`
- `docs/TEST_PLAN.md`

## Docker Compose changes

- Added `weblium_app` service (`python -m infrastructure.api.weblium_app`) and exposed `8080:8080`.
- Enabled real `pg_database` service (`postgres:16-alpine`).
- Added Postgres healthcheck using `pg_isready`.
- Added persistent volume `pgdata`.
- `bot` and `weblium_app` now depend on healthy Postgres.
- Runtime services override DB target for compose network:
  - `DB_HOST=pg_database`
  - `DB_PORT=5432`

## Startup wait-for-db logic

Implemented retry logic in DB initialization:

- `init_db(...)` now retries schema apply + pool creation on transient DB startup errors.
- Defaults:
  - `DB_WAIT_MAX_ATTEMPTS=30`
  - `DB_WAIT_DELAY_SECONDS=2`
- Both values are tunable via environment variables.

This prevents bot crash-loop during cold Postgres startup.

## Final test plan

Added `docs/TEST_PLAN.md` with the requested scenarios:

- bot-originated application with `tg_token`
- site-originated application without `tg_token`
- matched and unlinked webhook flows
- admin content approval for unlinked applications
- self-bind by phone/email
- payment gating (bind + approval required)
- payment idempotency
- join request gating
- duplicate webhook delivery
- reused token
- invalid token
- leaked invite link

Also documented that smoke tests stay optional and are gated by `RUN_WEBLIUM_SMOKE_TESTS=1`.
Test execution now uses `pytest`, with smoke checks under `tests/smoke`.
