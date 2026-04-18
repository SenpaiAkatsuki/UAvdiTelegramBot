# Step 10: Self-Bind Flow For Direct Website Applicants

## Added files

- `tgbot/handlers/binding.py`

## Updated files

- `tgbot/keyboards/membership.py`
- `tgbot/db/repo.py`
- `tgbot/handlers/__init__.py`

## What was implemented

- Added user self-bind flow with entry button:
  - `I already applied on the website`
- On click, bot immediately switches to phone input mode.
- Added one `Back` button for returning to the application entry message.
- Updated callback UX to edit one message instead of sending new button messages (anti-spam chat behavior).

## Candidate lookup rules

- Lookup is only performed among unlinked applications:
  - `tg_user_id IS NULL`
  - statuses in:
    - `UNLINKED_APPLICATION_PENDING`
    - `UNLINKED_APPLICATION_APPROVED`
- Supports lookup by:
  - phone only
  - normalized by last 10 digits to match formats like:
    - `+380505995520`
    - `0505995520`

## Binding outcomes

When exactly one candidate is found:

- If candidate status is `UNLINKED_APPLICATION_PENDING`:
  - bind to current `tg_user_id`
  - set status to `APPLICATION_PENDING`
  - show "under review" message
- If candidate status is `UNLINKED_APPLICATION_APPROVED`:
  - bind to current `tg_user_id`
  - set status to `APPROVED_AWAITING_PAYMENT`
  - show payment button

When multiple candidates are found:

- Do not auto-bind.
- Notify admins for manual confirmation.
- Inform user to wait for admin confirmation.

When no candidates are found:

- Show safe failure guidance and next steps.

## Safety and idempotency

- `bind_application_to_tg_user` in repo now locks row and validates bindability:
  - only unlinked statuses are bindable
  - already linked applications cannot be rebound
  - transaction-safe behavior with deterministic errors on races
- Added anti-abuse checks:
  - if requester already has active membership flow, auto-bind is blocked
  - if phone is already linked to another Telegram account, auto-bind is blocked and admins are notified
