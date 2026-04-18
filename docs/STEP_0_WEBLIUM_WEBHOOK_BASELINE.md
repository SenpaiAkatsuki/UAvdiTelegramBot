# Step 0: Weblium Webhook Global Baseline

This document is the single source of truth for all next implementation steps.

## Scope

- Telegram bot updates: keep long polling as default.
- Website applications: ingest via Weblium outgoing webhook into our aiohttp server.

## Guardrails

1. "Do not add webhooks" applies only to Telegram bot updates.
2. Do not migrate bot update handling from polling to Telegram webhook mode.
3. Weblium JSON POST webhook is the primary path for "application submitted".
4. The old in-bot "I submitted the application" callback/button is fallback only (optional).
5. If a future step says "application_submitted callback":
   - implement callback flow as fallback only,
   - implement the main business logic in the Weblium webhook handler.

## Endpoint Baseline

- Diagnostic endpoint (current, temporary):
  - `POST /webhooks/weblium/test`
  - implemented in `infrastructure/api/weblium_smoke.py`
- Production endpoint (future main path):
  - `POST /webhooks/weblium/application` (or close equivalent)
  - skeleton lives in `infrastructure/api/weblium_app.py`

## Architectural Intent For Later Steps

- Use Weblium webhook payload as the canonical input for application ingestion.
- Normalize payload fields before persistence and downstream actions.
- Keep raw payload for traceability/auditing.
- Add validation/security hooks before DB writes and notifications.

## Non-Goals At Step 0

- No DB writes in smoke handler.
- No Telegram business notification logic in smoke handler.
- No payment/approval/group-join logic.
- No Telegram webhook migration.
