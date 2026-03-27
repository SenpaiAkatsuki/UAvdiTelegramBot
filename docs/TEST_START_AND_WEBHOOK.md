# Test Guide: `/start` + Website Application + Weblium Webhook

Use this guide to verify the current end-to-end flow.

## 1) Prepare `.env`

Required minimum values:

```env
BOT_TOKEN=...
ADMINS=123456789
USE_REDIS=false

DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=postgres
DB_NAME=bot

MEMBERSHIP_APPLICATION_URL=https://uavdi.com.ua/#contact-form
MEMBERSHIP_APPLICATION_LINK_BASE_URL=https://uavdi.com.ua/#contact-form
MEMBERSHIP_FALLBACK_MANUAL_SUBMIT_ENABLED=true

WEBHOOK_ENABLED=true
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8080
WEBHOOK_WEBLIUM_PATH=/webhooks/weblium/application
WEBHOOK_WEBLIUM_SECRET=change_me_secret
WEBHOOK_TRUSTED_PROXY_IPS=
```

Notes:
- Leave `WEBHOOK_TRUSTED_PROXY_IPS` empty for local/ngrok tests.
- Test `/start` from a non-admin account if your admin router also has `/start`.

## 2) Start bot (polling)

```powershell
.\.venv\Scripts\python.exe .\bot.py
```

## 3) Start webhook server (separate process)

Open second terminal:

```powershell
.\.venv\Scripts\python.exe -m infrastructure.api.weblium_app
```

## 4) Expose webhook publicly (ngrok)

Open third terminal:

```powershell
ngrok http 8080
```

Take HTTPS URL from ngrok, for example:

`https://abcd-1234.ngrok-free.app`

Set Weblium webhook URL to:

`https://abcd-1234.ngrok-free.app/webhooks/weblium/application`

Pass secret in one supported way (recommended):
- header: `X-Weblium-Secret: change_me_secret`

## 5) Test matched flow (main path)

1. In Telegram, open bot and send `/start`.
2. Bot should send button with tokenized link (`tg_token=...`).
3. Submit website application from that link.
4. Webhook server should accept request and create matched application.

Expected results:
- `application_tokens`: token becomes used (`used_at` filled).
- `applications`: new row with:
  - `source='bot_link'`
  - `status='APPLICATION_PENDING'`
  - `tg_user_id` set
- `webhook_events`: new row with `processing_status='PROCESSED'`
- Telegram:
  - applicant gets "under review" message
  - admins get new application notification with Approve/Reject buttons

## 6) Test unlinked flow (direct site)

1. Open website form directly (without `/start` tokenized link).
2. Submit application.

Expected results:
- `applications`: new row with:
  - `source='site_direct'`
  - `status='UNLINKED_APPLICATION_PENDING'`
  - `tg_user_id IS NULL`
- `bind_tokens`: new token row linked to this application
- `webhook_events`: processed row exists
- Telegram:
  - admin notification only
  - no user notification

## 7) Fast DB checks (run in your SQL console)

```sql
SELECT id, provider, event_key, processing_status, application_id, received_at
FROM webhook_events
ORDER BY id DESC
LIMIT 10;
```

```sql
SELECT id, source, status, tg_user_id, contact_phone, contact_email, created_at
FROM applications
ORDER BY id DESC
LIMIT 10;
```

```sql
SELECT id, token, tg_user_id, used_at, expires_at, created_at
FROM application_tokens
ORDER BY id DESC
LIMIT 10;
```

```sql
SELECT id, token, application_id, used_at, expires_at, created_at
FROM bind_tokens
ORDER BY id DESC
LIMIT 10;
```

## 8) Duplicate delivery check

If the same payload is delivered again, endpoint should still return HTTP 200 but with duplicate message, and must not create duplicate application rows.

