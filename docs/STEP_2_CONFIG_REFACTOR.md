# Step 2: Config Refactor (asyncpg + aiohttp Weblium + membership flow)

## What changed

- Removed SQLAlchemy-specific config helper usage from `DbConfig`.
- Kept `environs`-based environment parsing.
- Added/updated config dataclasses:
  - `DbConfig`
  - `ChatConfig`
  - `PaymentsConfig`
  - `MembershipConfig`
  - `WebhookConfig`
- Updated root `Config` to include:
  - `db`, `chat`, `payments`, `membership`, `webhook`
  - plus existing `tg_bot` and optional `redis`.
- Updated `.env.dist` with required variables for the new config model.

## Webhook fail-fast behavior

When `WEBHOOK_ENABLED=true`, startup now raises `ValueError` if webhook config is invalid:

- empty `WEBHOOK_HOST`
- invalid `WEBHOOK_PORT`
- empty or non-absolute `WEBHOOK_WEBLIUM_PATH`
- empty `WEBHOOK_WEBLIUM_SECRET`
- invalid IP/CIDR entries in `WEBHOOK_TRUSTED_PROXY_IPS`

## Scope guardrail

- Bot polling entrypoint remains unchanged.
- This step only refactors configuration and env model.
