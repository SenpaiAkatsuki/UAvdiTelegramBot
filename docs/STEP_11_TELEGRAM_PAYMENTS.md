# Step 11: Telegram Payments For Approved Users

## Added file

- `tgbot/handlers/payments.py`

## Updated files

- `tgbot/handlers/__init__.py` (router registration)

## What was implemented

- Added `payments_router` for Telegram payments flow.
- Payment entry callback: `membership_pay`.
- Pre-checkout handling via `pre_checkout_query`.
- Successful payment handling via `successful_payment`.

## Allowed payment status

Payment is allowed only when latest user application status is exactly:

- `APPROVED_AWAITING_PAYMENT`

Payment is rejected for all other statuses, including:

- `NEW`
- `APPLICATION_PENDING`
- `UNLINKED_APPLICATION_PENDING`
- `UNLINKED_APPLICATION_APPROVED`
- `REJECTED`

## Pay callback behavior

On `membership_pay`:

- validates payments are enabled and provider token exists
- loads latest application for `tg_user_id`
- enforces exact allowed status
- reuses existing `PENDING` payment if present
- blocks if latest payment already `PAID`
- otherwise creates a new `payments` row with status `PENDING`
- sends Telegram invoice

## Pre-checkout behavior

- validates invoice payload format
- validates payment record exists and is `PENDING`
- validates linked application is still `APPROVED_AWAITING_PAYMENT`
- answers pre-checkout quickly with `ok=True/False`

## Successful payment behavior (idempotent)

- parses payment id from invoice payload
- if already `PAID`, returns safe "already confirmed" response
- otherwise updates payment status to `PAID`
- repo transaction updates application status to `PAID_AWAITING_JOIN`
- sends `Get group access` button
