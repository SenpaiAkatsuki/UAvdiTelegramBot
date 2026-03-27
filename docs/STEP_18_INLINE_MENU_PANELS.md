# Step 18: Inline Menu Panels (User + Admin)

## Goal

Build one-message inline menu navigation for users and admins.

## Added files

- `tgbot/callbacks/menu.py`
- `tgbot/keyboards/menu.py`
- `tgbot/services/menu_renderer.py`
- `tgbot/handlers/menu.py`

## Updated files

- `tgbot/db/repo.py`
- `tgbot/handlers/membership.py`
- `tgbot/handlers/admin.py`
- `tgbot/handlers/__init__.py`

## Entry points

- `/start` remains normal onboarding/status entrypoint.
- For usable account states, `/start` now also offers a `Menu` button.
- `/menu` opens the same root menu renderer as inline `Menu` button.

## One-message UX

- Internal navigation is callback-based (`MenuCallbackData`).
- Menu callbacks edit the same message (`edit_text`).
- If editing fails (message gone / not editable), bot sends a fallback menu message.
- Callback queries are always answered.

## User panel

- `root` screen
- `profile` screen with:
  - `member_since`
  - `subscription_expires_at`
  - `days_left`
  - current status
- Conditional actions:
  - `Renew` when `days_left <= 20`
  - `Get group access` when access eligibility is met
- `Back` navigation included.

## Admin panel

- Access restricted to `ADMINS`.
- Screens:
  - `admin_root`
  - `active members`
  - `expiring <= 30 days`
  - `expired`
  - `user detail`
- Pagination supported on list screens.
- Data source is PostgreSQL only.

### Admin pricing control

- Added inline screen `Subscription price (UAH)` under admin management.
- Admin can change runtime subscription price from menu buttons.
- Price is stored in DB (`app_settings`) and used by new LiqPay payment creation.

## Repository additions

- `get_user_panel_data(tg_user_id)`
- `list_active_members(limit, offset)`
- `list_expiring_members(max_days, limit, offset)`
- `list_expired_members(limit, offset)`
- `get_member_detail(tg_user_id)`
