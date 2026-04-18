# UAvdiTelegramBot

**UAvdiTelegramBot** is a Telegram membership automation system for UAVDI.
It manages the full flow: application intake, voting, payment, subscription lifecycle, and private group access.

---

## Components

### Main Telegram Bot
- **Framework:** [Aiogram v3](https://docs.aiogram.dev/)
- **Role:** Handles user/admin interaction in private chat using inline-menu navigation.
- **Features:**
  - `/start` onboarding flow
  - user profile and subscription status
  - admin moderation and management panel
  - throttling and anti-spam guards

### Webhook App
- **Framework:** [aiohttp](https://docs.aiohttp.org/)
- **Role:** Receives Weblium applications and LiqPay callbacks.
- **Features:**
  - Weblium form intake (linked and unlinked)
  - LiqPay checkout page endpoint
  - callback signature verification
  - idempotent payment confirmation logic

### PostgreSQL Data Layer
- **Driver:** [asyncpg](https://magicstack.github.io/asyncpg/current/)
- **Role:** Single source of truth for users, applications, voting, subscriptions, and payments.
- **Features:**
  - raw SQL repository layer (`no ORM`)
  - schema bootstrap on startup
  - notification/reminder storage

---

## Key Features

- **End-to-End Membership Flow:**  
  Site/bot application -> admin voting -> payment -> access link -> active membership.

- **LiqPay Integration:**  
  External checkout + verified server callback with audit persistence.

- **Yearly Subscription Lifecycle:**  
  365-day activation, reminders at 20/10/5 days left, optional expired-member enforcement.

- **Inline Panels for Users/Admins:**  
  One-message navigation (edit-in-place) for cleaner UX.

- **Production Deployment Stack:**  
  Docker Compose with bot, webhook app, PostgreSQL, backups, and DB panels.

---

## Technologies

- **Language:** Python 3.12
- **Frameworks & Libraries:**
  - **Aiogram v3:** Telegram bot interaction
  - **aiohttp:** webhook server
  - **asyncpg:** async PostgreSQL access
  - **environs:** environment config loading
  - **pytest:** automated tests
- **Database & Infra:**
  - **PostgreSQL**
  - **Docker / Docker Compose**
  - **Nginx** (reverse proxy for public routes)

---

## First Setup

1. Rename `.env.dist` to `.env`.
2. Fill required values:
   - `BOT_TOKEN`, `ADMINS`
   - DB settings (`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`)
   - `CHAT_MEMBERSHIP_CHAT_ID`, `VOTING_CHAT_ID`
   - `PUBLIC_WEBHOOK_BASE_URL`
   - `WEBHOOK_WEBLIUM_SECRET`
   - `LIQPAY_*` (if payments are enabled)
3. Start with Docker:
   - `docker compose up -d --build`
4. Or run locally:
   - `pip install -r requirements.txt -r requirements-dev.txt`
   - `python -m infrastructure.api.weblium_app`
   - `python bot.py`

---

## Notes

- Polling mode is used for Telegram bot runtime.
- Public domain + HTTPS are required for stable production webhook callbacks.
- DB panel and deployment details: see `docs/DB_PANEL.md`.
