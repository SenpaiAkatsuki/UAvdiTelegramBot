# Step 17: Yearly Subscription Lifecycle + Renewal Reminders

## Updated files

- `tgbot/db/repo.py`
- `tgbot/services/subscription_reminders.py`
- `bot.py`
- `tgbot/handlers/group_access.py`
- `tgbot/handlers/membership.py`
- `tgbot/handlers/payments.py`
- `tgbot/config.py`

## New config

- `ENFORCE_EXPIRED_REMOVAL` (default `false`)
- `ENFORCE_EXPIRED_REMOVAL_DRY_RUN` (default `true`)
- `ENFORCE_EXPIRED_REMOVAL_MAX_PER_RUN` (default `25`)

## Repository additions

Added in `PostgresRepo`:

- `compute_days_left(subscription_expires_at)`
- `get_users_with_subscription_expiring(days_left)`
  - matches users by calendar day:
    - `subscription_expires_at::date == (now + days_left)::date` (UTC-based date comparison)
  - excludes users already notified for the same `(tg_user_id, subscription_expires_at, days_left)`
- `mark_renewal_notified(...)`
  - `INSERT ... ON CONFLICT DO NOTHING`
- `get_users_with_expired_subscription()`
  - used by optional expired-removal enforcement

## Reminder service

Added new file:

- `tgbot/services/subscription_reminders.py`

Behavior:

- Loop runs every 6 hours.
- For `days_left in [20, 10, 5]`:
  - fetch due users
  - send reminder with payment keyboard (`Pay membership` / `I paid, check status`)
  - persist renewal notification marker

## Optional expired-member enforcement

When `ENFORCE_EXPIRED_REMOVAL=true`:

- once per day, fetch users with expired active subscriptions
- apply safety cap (`ENFORCE_EXPIRED_REMOVAL_MAX_PER_RUN`)
- if dry-run is enabled, only log who would be removed (no ban/unban calls)
- remove only users currently in removable membership statuses (`member` / `restricted`)
- remove from membership group using:
  - `ban_chat_member(...)`
  - `unban_chat_member(..., only_if_banned=True)` to allow future rejoin
- log Telegram failures (missing rights, API errors)

## Bot wiring

`bot.py` now starts an additional background task:

- `subscription_reminder_loop(...)`

Task is canceled cleanly on shutdown together with vote loop.

## Eligibility updates

### Group join request / access

Group access now requires both:

- application status in `{PAID_AWAITING_JOIN, ACTIVE_MEMBER}`
- user subscription is active:
  - `subscription_status == 'ACTIVE'`
  - `subscription_expires_at > now`

If expired, user is asked to renew.

### `/start` membership UX

For users in `PAID_AWAITING_JOIN` / `ACTIVE_MEMBER`:

- if subscription is active -> show `Get group access`
- if expired -> show renew message + payment keyboard

### Payment flow for renewal

`membership_pay` now also allows renewals for:

- `PAID_AWAITING_JOIN`
- `ACTIVE_MEMBER`

It still keeps strict first-payment eligibility for:

- `APPROVED_AWAITING_PAYMENT` with processed vote
