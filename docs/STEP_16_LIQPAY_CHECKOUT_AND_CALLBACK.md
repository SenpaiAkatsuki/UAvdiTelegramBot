# Step 16: Replace Telegram Payments with LiqPay Checkout + Callback Verification

## Updated files

- `tgbot/config.py`
- `tgbot/handlers/payments.py`
- `tgbot/db/repo.py`
- `tgbot/keyboards/membership.py`
- `infrastructure/api/weblium_app.py`
- `bot.py`

## Config added

- `LIQPAY_PUBLIC_KEY`
- `LIQPAY_PRIVATE_KEY`
- `LIQPAY_CURRENCY` (default `UAH`)
- `LIQPAY_AMOUNT` (major units, e.g. `100.00`)
- `PUBLIC_WEBHOOK_BASE_URL` (legacy `PUBLIC_BASE_URL` is also supported)
- `LIQPAY_CALLBACK_PATH` (default `/webhooks/liqpay/callback`)
- `LIQPAY_PAY_PATH` (default `/pay/liqpay/{payment_id}`)

## Bot-side payment flow changes

- Telegram invoice flow (`send_invoice`, `pre_checkout_query`, `successful_payment`) is removed.
- On `membership_pay`:
  - validates payments are enabled
  - validates latest application is payment-eligible (`APPROVED_AWAITING_PAYMENT` + vote already processed)
  - creates/reuses `payments` row with:
    - `provider='liqpay'`
    - unique `provider_order_id`
    - configured amount/currency
    - `status='PENDING'`
  - sends user payment link:
    - `{PUBLIC_WEBHOOK_BASE_URL}{LIQPAY_PAY_PATH}` with `{payment_id}` substitution
- `I paid, check status` button remains as DB-state recheck only (no external calls).

## Web-side LiqPay endpoints

### GET `/pay/liqpay/{payment_id}`

- Loads `payments` row by id and provider `liqpay`.
- Builds LiqPay checkout payload:
  - `version=3`
  - `public_key`
  - `action='pay'`
  - `amount`
  - `currency`
  - `description`
  - `order_id=provider_order_id`
  - `server_url={PUBLIC_WEBHOOK_BASE_URL}{LIQPAY_CALLBACK_PATH}`
- Encodes payload to `data` (base64 JSON).
- Builds `signature = base64(sha1(private_key + data + private_key))`.
- Returns minimal auto-submit HTML form to:
  - `https://www.liqpay.ua/api/3/checkout`

### POST `/webhooks/liqpay/callback`

- Reads `data` and `signature` from POST form fields.
- Verifies signature with:
  - `base64(sha1(private_key + data + private_key))`
- Decodes callback `data` JSON and extracts:
  - `order_id`, `status`, `amount`, `currency`
- Stores callback payload/signature fields in `payments` for audit.
- Validates amount/currency against stored payment.
- On `status == 'success'`:
  - idempotently marks payment `PAID`
  - updates application status to `PAID_AWAITING_JOIN` (when eligible)
  - activates/extends user subscription:
    - `subscription_expires_at = max(now, current_expires_at) + 365 days`
    - `subscription_status = 'ACTIVE'`
  - sends Telegram confirmation + `Get group access` button

## Idempotency

- `provider_order_id` uniqueness is enforced in DB (existing partial unique index).
- Success callback processing uses transaction + `FOR UPDATE`.
- Repeated successful callbacks do not extend subscription twice.
- Callback payload/signature are still persisted for audit.
