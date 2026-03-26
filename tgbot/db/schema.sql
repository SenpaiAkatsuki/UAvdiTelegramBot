-- Idempotent PostgreSQL schema for the two-branch application flow:
-- Branch A: bot -> site with tg_token correlation
-- Branch B: direct site submission without tg_token (unlinked, bind later)

CREATE TABLE IF NOT EXISTS users (
    tg_user_id BIGINT PRIMARY KEY,
    username VARCHAR(128),
    full_name VARCHAR(255) NOT NULL,
    language_code VARCHAR(10),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    member_since TIMESTAMPTZ,
    subscription_expires_at TIMESTAMPTZ,
    subscription_status TEXT NOT NULL DEFAULT 'NONE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS applications (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('bot_link', 'site_direct', 'manual')),
    status TEXT NOT NULL DEFAULT 'NEW' CHECK (
        status IN (
            'NEW',
            'APPLICATION_REQUIRED',
            'APPLICATION_PENDING',
            'UNLINKED_APPLICATION_PENDING',
            'UNLINKED_APPLICATION_APPROVED',
            'APPROVED_AWAITING_PAYMENT',
            'PAID_AWAITING_JOIN',
            'ACTIVE_MEMBER',
            'REJECTED'
        )
    ),
    tg_user_id BIGINT REFERENCES users(tg_user_id) ON DELETE SET NULL,
    contact_phone VARCHAR(64),
    contact_email VARCHAR(255),
    applicant_name VARCHAR(255),
    specialization VARCHAR(255),
    document_url TEXT,
    document_file_name TEXT,
    weblium_referer TEXT,
    vote_chat_id BIGINT,
    vote_message_id INTEGER,
    vote_poll_id TEXT,
    vote_status TEXT,
    vote_closes_at TIMESTAMPTZ,
    vote_yes_count INTEGER,
    vote_no_count INTEGER,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    rejected_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS application_tokens (
    id BIGSERIAL PRIMARY KEY,
    token TEXT NOT NULL UNIQUE CHECK (length(trim(token)) > 0),
    tg_user_id BIGINT NOT NULL REFERENCES users(tg_user_id) ON DELETE CASCADE,
    application_id BIGINT REFERENCES applications(id) ON DELETE SET NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bind_tokens (
    id BIGSERIAL PRIMARY KEY,
    token TEXT NOT NULL UNIQUE CHECK (length(trim(token)) > 0),
    application_id BIGINT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    claimed_tg_user_id BIGINT REFERENCES users(tg_user_id) ON DELETE SET NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    application_id BIGINT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'telegram',
    provider_payment_id TEXT,
    provider_order_id TEXT,
    provider_status TEXT,
    callback_data TEXT,
    callback_signature TEXT,
    signature_valid BOOLEAN,
    amount_minor BIGINT NOT NULL CHECK (amount_minor >= 0),
    currency VARCHAR(8) NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING', 'PAID', 'FAILED', 'REFUNDED', 'CANCELED')
    ),
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_payment_id)
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'weblium',
    event_key TEXT NOT NULL CHECK (length(trim(event_key)) > 0),
    request_path TEXT NOT NULL,
    source_ip INET,
    headers JSONB NOT NULL DEFAULT '{}'::jsonb,
    query_params JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_raw TEXT,
    payload_json JSONB,
    processing_status TEXT NOT NULL DEFAULT 'RECEIVED' CHECK (
        processing_status IN ('RECEIVED', 'PROCESSED', 'FAILED', 'IGNORED')
    ),
    processing_error TEXT,
    application_id BIGINT REFERENCES applications(id) ON DELETE SET NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    UNIQUE (provider, event_key)
);

CREATE TABLE IF NOT EXISTS renewal_notifications (
    id BIGSERIAL PRIMARY KEY,
    tg_user_id BIGINT NOT NULL REFERENCES users(tg_user_id) ON DELETE CASCADE,
    subscription_expires_at TIMESTAMPTZ NOT NULL,
    days_left INTEGER NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tg_user_id, subscription_expires_at, days_left)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY CHECK (length(trim(key)) > 0),
    value_text TEXT NOT NULL,
    updated_by_tg_user_id BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Safe schema evolution for existing databases.
ALTER TABLE users ADD COLUMN IF NOT EXISTS member_since TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status TEXT;
UPDATE users SET subscription_status = 'NONE' WHERE subscription_status IS NULL;
ALTER TABLE users ALTER COLUMN subscription_status SET DEFAULT 'NONE';
ALTER TABLE users ALTER COLUMN subscription_status SET NOT NULL;

ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_chat_id BIGINT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_message_id INTEGER;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_poll_id TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_status TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_closes_at TIMESTAMPTZ;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_yes_count INTEGER;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_no_count INTEGER;

ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider TEXT;
UPDATE payments SET provider = 'telegram' WHERE provider IS NULL;
ALTER TABLE payments ALTER COLUMN provider SET DEFAULT 'telegram';
ALTER TABLE payments ALTER COLUMN provider SET NOT NULL;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider_order_id TEXT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider_status TEXT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS callback_data TEXT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS callback_signature TEXT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS signature_valid BOOLEAN;

CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_subscription_expires_at ON users (subscription_expires_at);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications (status);
CREATE INDEX IF NOT EXISTS idx_applications_source ON applications (source);
CREATE INDEX IF NOT EXISTS idx_applications_tg_user_id ON applications (tg_user_id);
CREATE INDEX IF NOT EXISTS idx_applications_phone ON applications (contact_phone);
CREATE INDEX IF NOT EXISTS idx_applications_email ON applications (contact_email);
CREATE INDEX IF NOT EXISTS idx_applications_vote_status_closes_at
    ON applications (vote_status, vote_closes_at);

CREATE INDEX IF NOT EXISTS idx_application_tokens_tg_user_id ON application_tokens (tg_user_id);
CREATE INDEX IF NOT EXISTS idx_application_tokens_expires_at ON application_tokens (expires_at);
CREATE INDEX IF NOT EXISTS idx_application_tokens_used_at ON application_tokens (used_at);

CREATE INDEX IF NOT EXISTS idx_bind_tokens_application_id ON bind_tokens (application_id);
CREATE INDEX IF NOT EXISTS idx_bind_tokens_claimed_tg_user_id ON bind_tokens (claimed_tg_user_id);
CREATE INDEX IF NOT EXISTS idx_bind_tokens_expires_at ON bind_tokens (expires_at);
CREATE INDEX IF NOT EXISTS idx_bind_tokens_used_at ON bind_tokens (used_at);

CREATE INDEX IF NOT EXISTS idx_payments_application_id ON payments (application_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments (status);
CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments (paid_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_provider_order_id_not_null
    ON payments (provider, provider_order_id)
    WHERE provider_order_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_webhook_events_received_at ON webhook_events (received_at);
CREATE INDEX IF NOT EXISTS idx_webhook_events_processing_status ON webhook_events (processing_status);
CREATE INDEX IF NOT EXISTS idx_webhook_events_application_id ON webhook_events (application_id);

CREATE INDEX IF NOT EXISTS idx_renewal_notifications_tg_user_id
    ON renewal_notifications (tg_user_id);

CREATE INDEX IF NOT EXISTS idx_app_settings_updated_at
    ON app_settings (updated_at);
