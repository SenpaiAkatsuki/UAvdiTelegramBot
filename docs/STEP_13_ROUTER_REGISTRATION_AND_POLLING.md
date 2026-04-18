# Step 13: Router Registration and Polling Bootstrap

## Updated files

- `tgbot/handlers/__init__.py`
- `bot.py`

## Router registration

Routers were normalized to membership flow priority and removed legacy template routers:

- `membership_router`
- `binding_router`
- `admin_applications_router`
- `payments_router`
- `group_access_router`
- `echo_router` (last)

`echo_router` remains last to avoid intercepting other handlers.

## Polling startup updates

Polling bootstrap now explicitly:

- calls `delete_webhook(drop_pending_updates=True)` before polling
- starts polling with:
  - `allowed_updates=dp.resolve_used_update_types()`

This keeps Telegram update delivery in polling mode and prevents webhook-mode leftovers from interfering.

## Separation of processes

Architecture remains cleanly separated:

- Telegram bot runtime: `bot.py` (long polling only)
- Weblium website webhook runtime: `python -m infrastructure.api.weblium_app` (aiohttp server)

No Telegram webhook mode was introduced.
