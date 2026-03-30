# Step 15: Replace Admin Inline Moderation with Group Poll Approval

## Added files

- `tgbot/services/application_voting.py`
- `tgbot/handlers/admin_applications.py` (legacy callbacks disabled)

## Updated files

- `tgbot/config.py`
- `infrastructure/api/weblium_app.py`
- `bot.py`
- `tgbot/handlers/__init__.py`

## Config added

- `VOTING_CHAT_ID`
- `VOTING_TOPIC_ID` (optional forum topic id)
- `VOTE_DURATION_SECONDS` (default `86400`)
- `VOTE_MIN_TOTAL` (optional quorum)
- `VOTE_REQUIRE_YES_GT_NO` (default `True`)

## New voting service

`tgbot/services/application_voting.py`:

- `start_vote(application_id, application_text, bot, config, repo)`
  - sends application summary to voting chat
  - sends poll (`Approve`, `Reject`)
  - stores `vote_chat_id`, `vote_message_id`, `vote_poll_id`, `vote_status='OPEN'`, `vote_closes_at`

- `close_due_votes(bot, config, repo)`
  - finds due `OPEN` votes
  - closes poll with `bot.stop_poll(...)`
  - computes `yes/no`
  - applies status transition:
    - `APPLICATION_PENDING` -> `APPROVED_AWAITING_PAYMENT` or `REJECTED`
    - `UNLINKED_APPLICATION_PENDING` -> `UNLINKED_APPLICATION_APPROVED` or `REJECTED`
  - saves final vote counts and sets `vote_status='PROCESSED'`

Unlinked approval still does **not** unlock payment until safe Telegram binding.

## Ingestion changes

Weblium ingestion now starts voting instead of sending admin inline approve/reject keyboards.

## Background close loop

`bot.py` starts a DB-driven background task (every ~45 seconds):

- calls `close_due_votes(...)`
- safe on restart (no in-memory timers used for vote ownership)

## Legacy admin inline moderation

- Old `admin_application_*` and `admin_unlinked_*` callbacks are disabled.
- Admin receives callback alert: moderation moved to group voting.
- `AdminFilter` remains available for admin-only command routes.
