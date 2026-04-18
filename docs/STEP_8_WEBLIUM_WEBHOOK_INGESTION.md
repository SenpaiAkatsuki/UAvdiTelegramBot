# Step 8: Weblium Webhook Ingestion (Primary Application Path)

## Updated files

- `infrastructure/api/weblium_app.py`
- `tgbot/db/repo.py`

## What was implemented

- Added production `aiohttp` webhook server flow for Weblium applications.
- Main endpoint: `POST /webhooks/weblium/application`.
- Server startup now initializes DB/pool and Bot client for notifications.

## Request validation

- Enforces JSON content type.
- Parses JSON body safely and rejects invalid payloads.
- Validates webhook secret using header/query/body strategy.
- Validates source IP against `WEBHOOK_TRUSTED_PROXY_IPS` when configured.

## Payload normalization

Normalized fields extracted from Weblium payload:

- `form_name`
- `referer`
- `time`
- `applicant_name`
- `phone`
- `email`
- `specialization`
- `document_url`
- `document_file_name`
- optional `tg_token`

## Idempotency and deduplication

- Computes `request_hash` (`sha256(path + raw_body)`).
- Uses `webhook_events.event_key` for dedupe.
- Duplicate deliveries return success with `"duplicate webhook ignored"`.
- Added `is_new` marker in repo `create_webhook_event` result for race-safe dedupe.

## Branch logic

### Matched flow (`tg_token` valid)

- Resolve `tg_user_id` from token.
- Create matched application (`APPLICATION_PENDING`).
- Mark token as used (transactional in repo).
- Notify user that application is under review.
- Notify admins with Approve/Reject buttons.

### Unlinked flow (`tg_token` missing/invalid)

- Create unlinked application (`UNLINKED_APPLICATION_PENDING`).
- Create bind token for later safe Telegram binding.
- Notify admins with:
  - Contact manually
  - Mark approved content
  - Reject
- No user notification (no trusted `tg_user_id` yet).

## Runtime

Run separately from bot polling:

```powershell
.\.venv\Scripts\python.exe -m infrastructure.api.weblium_app
```

Requirements:

- `WEBHOOK_ENABLED=true`
- valid webhook env (`WEBHOOK_HOST`, `WEBHOOK_PORT`, `WEBHOOK_WEBLIUM_PATH`, `WEBHOOK_WEBLIUM_SECRET`)
