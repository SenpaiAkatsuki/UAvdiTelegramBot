# Step 6: Notification Helpers For Users And Admins

## Added file

- `tgbot/services/notify.py`

## Core helpers

- `notify_user(...)`
- `notify_admins(...)`

Both helpers reuse broadcaster internals:

- single send: `broadcaster.send_message`
- fan-out send: `broadcaster.broadcast`

This keeps behavior safe and flood-limit aware via existing broadcaster retry/sleep logic.

## Structured logging

Added minimal structured logging in notification helpers:

- start + done logs for user/admin sends
- JSON-like context payload in logs

## Admin event helpers

Added ready-to-use admin notification wrappers for:

- matched applications
- unlinked applications
- bind confirmation requests
- approval
- rejection
- payment-ready notifications
