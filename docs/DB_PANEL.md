# DB Panel (Client Guide)

This project uses **NocoDB** as the client-facing database panel.

## URL

- Public URL: `https://api.uavdi.com.ua/db/`
- It redirects to NocoDB UI under `/nocodb/`.

## Login

- Use the NocoDB account created for the client (email + password from deploy admin).
- If login fails, ask deploy admin to reset password in NocoDB.

## Database Connection (inside NocoDB)

- Host: `pg_database`
- Port: `5432`
- Database: `bot`
- Username: `uavdi_client_panel`
- Password: `UavdiClient2026!`
- Schema: `public`
- SSL: `disable`

## What You Should See

Exactly 4 editable business tables:

- `users`
- `payments`
- `voting_members`
- `applications`

## Notes About Imported Legacy Members

- The transferred legacy cohort is stored in `applications` until Telegram bind.
- Their legacy expiry marker is stored as:
  - `weblium_referer = legacy_import_expiry=YYYY-MM-DD`
- After bind, real active subscription date is written to:
  - `users.subscription_expires_at`

## Safe Editing Rules

- Edit only required fields.
- Do not mass-delete rows.
- Do not rename columns/tables from NocoDB.
- Prefer small updates and verify bot behavior after each important change.

## Quick Troubleshooting

- Only one table appears:
  - Open DB source settings and ensure schema is `public` (not `client_panel`).
  - If needed, remove old source and reconnect with the params above.

- Table opens but cannot edit:
  - Reconnect using `uavdi_client_panel`.
  - Verify grants were applied by running `scripts/postgres/setup_client_panel.sql`.

- Page does not open:
  - Check Nginx route `/db/` -> `/nocodb/`.
  - Check container status: `docker compose ps nocodb`.

## Operator Commands (Server)

```bash
cd /home/bots/UAvdiTelegramBot
docker compose ps nocodb
docker compose logs --tail=200 nocodb
docker compose restart nocodb
```
