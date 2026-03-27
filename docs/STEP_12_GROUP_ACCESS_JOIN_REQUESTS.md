# Step 12: Group Access via Join Requests

## Added file

- `tgbot/handlers/group_access.py`

## Updated files

- `tgbot/handlers/__init__.py` (router registration)

## What was implemented

- Added `group_access_router`.
- Added callback handler: `membership_get_group_access`.
- Added `chat_join_request` handler for membership group moderation.

## Invite link rules

Invite link is created only if latest application status is:

- `PAID_AWAITING_JOIN`
- `ACTIVE_MEMBER`

For all other statuses, invite link is not issued.

Invite link is created as a Telegram join-request link (`creates_join_request=True`).

## Join-request moderation rules

For membership chat join requests:

- Eligible statuses (`PAID_AWAITING_JOIN`, `ACTIVE_MEMBER`) -> request is approved.
- Non-eligible status or no application -> request is declined.

## Status transition after join

If user was `PAID_AWAITING_JOIN` and join request is approved:

- application status is updated to `ACTIVE_MEMBER`.

If already `ACTIVE_MEMBER`:

- approval remains idempotent and status stays `ACTIVE_MEMBER`.

## Polling compatibility

- Uses aiogram `chat_join_request` update handling in polling mode.
- No Telegram webhook mode was added.
