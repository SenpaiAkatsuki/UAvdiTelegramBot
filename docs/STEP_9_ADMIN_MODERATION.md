# Step 9: Admin Moderation For Matched And Unlinked Applications

## Added file

- `tgbot/handlers/admin_applications.py`

## What was implemented

- Added admin-only callback handlers for application moderation actions from inline buttons.
- Added idempotent status-transition handling with row-level locking (`SELECT ... FOR UPDATE`).

## Matched application moderation

Callback actions:

- `admin_application_approve:{application_id}`
  - transition: `APPLICATION_PENDING -> APPROVED_AWAITING_PAYMENT`
  - user gets approval notification
- `admin_application_reject:{application_id}`
  - transition: `APPLICATION_PENDING -> REJECTED`
  - user gets rejection notification

Idempotency:

- repeated approve/reject clicks return safe “already processed” responses
- invalid state transitions are rejected safely

## Unlinked application moderation

Callback actions:

- `admin_unlinked_contact:{application_id}`
  - no status change
  - marks manual contact action in callback feedback
- `admin_unlinked_mark_approved:{application_id}`
  - transition: `UNLINKED_APPLICATION_PENDING -> UNLINKED_APPLICATION_APPROVED`
- `admin_unlinked_reject:{application_id}`
  - transition:
    - `UNLINKED_APPLICATION_PENDING -> REJECTED`
    - `UNLINKED_APPLICATION_APPROVED -> REJECTED`

Important rule preserved:

- `UNLINKED_APPLICATION_APPROVED` does **not** unlock payment.
- Payment remains locked until safe bind to a real `tg_user_id`.

## Router wiring

- `admin_applications_router` registered in handlers list so callbacks are active in polling runtime.
