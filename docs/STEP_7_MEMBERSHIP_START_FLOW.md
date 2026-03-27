# Step 7: Membership `/start` Flow With Tokenized Website Entry

## Added files

- `tgbot/handlers/membership.py`
- `tgbot/keyboards/membership.py`

## Updated files

- `tgbot/handlers/__init__.py`

## What was implemented

- Added `membership_router` with the main `/start` membership flow.
- On `/start`, bot now:
  - upserts user in DB (`create_or_update_user`)
  - loads latest application status for current `tg_user_id`
  - branches response by membership status.

## Status branches

- `NEW` / `APPLICATION_REQUIRED`:
  - reuse active application token or create a new one
  - build tokenized website URL with `tg_token`
  - send website button
  - show fallback `I already submitted` button only when `MEMBERSHIP_FALLBACK_MANUAL_SUBMIT_ENABLED=true`.
- `APPLICATION_PENDING`:
  - sends pending review message.
- `UNLINKED_APPLICATION_APPROVED`:
  - asks user to request bind confirmation before payment.
- `APPROVED_AWAITING_PAYMENT`:
  - shows payment button.
- `PAID_AWAITING_JOIN` / `ACTIVE_MEMBER`:
  - shows group access button.

## Keyboards

- Added separate keyboard helpers:
  - application entry button (+ optional fallback)
  - pay button
  - get group access button
  - bind confirmation request button

## Routing note

- `membership_router` is registered before `user_router` so the new membership `/start` flow is used as the main path.
- Polling mode remains unchanged.
