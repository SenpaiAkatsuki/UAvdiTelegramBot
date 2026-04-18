-- Idempotent PostgreSQL schema for the two-branch application flow:
-- Branch A: bot -> site with tg_token correlation
-- Branch B: direct site submission without tg_token (unlinked, bind later)

CREATE TABLE IF NOT EXISTS users (
    tg_user_id BIGINT PRIMARY KEY,
    username VARCHAR(128),
    full_name VARCHAR(255) NOT NULL,
    language_code VARCHAR(10),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_bot_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_bot_admin_text TEXT GENERATED ALWAYS AS (
        CASE WHEN is_bot_admin THEN 'TRUE' ELSE 'FALSE' END
    ) STORED,
    member_since TIMESTAMPTZ,
    subscription_expires_at TIMESTAMPTZ,
    subscription_status TEXT NOT NULL DEFAULT 'NONE',
    last_membership_invite_link TEXT,
    last_membership_invite_expires_at TIMESTAMPTZ,
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

CREATE TABLE IF NOT EXISTS voting_members (
    tg_user_id BIGINT PRIMARY KEY,
    username VARCHAR(128),
    full_name VARCHAR(255) NOT NULL,
    language_code VARCHAR(10),
    is_bot_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_bot_admin_text TEXT GENERATED ALWAYS AS (
        CASE WHEN is_bot_admin THEN 'TRUE' ELSE 'FALSE' END
    ) STORED,
    member_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (member_status IN ('ACTIVE', 'LEFT')),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS application_votes (
    application_id BIGINT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    tg_user_id BIGINT NOT NULL REFERENCES users(tg_user_id) ON DELETE CASCADE,
    vote BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (application_id, tg_user_id)
);

CREATE TABLE IF NOT EXISTS library_topics (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (title)
);

CREATE TABLE IF NOT EXISTS library_articles (
    id BIGSERIAL PRIMARY KEY,
    topic_id BIGINT NOT NULL REFERENCES library_topics(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (topic_id, title)
);

-- Safe schema evolution for existing databases.
ALTER TABLE users ADD COLUMN IF NOT EXISTS member_since TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_bot_admin BOOLEAN;
UPDATE users SET is_bot_admin = FALSE WHERE is_bot_admin IS NULL;
ALTER TABLE users ALTER COLUMN is_bot_admin SET DEFAULT FALSE;
ALTER TABLE users ALTER COLUMN is_bot_admin SET NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_bot_admin_text TEXT GENERATED ALWAYS AS (
    CASE WHEN is_bot_admin THEN 'TRUE' ELSE 'FALSE' END
) STORED;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_membership_invite_link TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_membership_invite_expires_at TIMESTAMPTZ;
UPDATE users SET subscription_status = 'NONE' WHERE subscription_status IS NULL;
ALTER TABLE users ALTER COLUMN subscription_status SET DEFAULT 'NONE';
ALTER TABLE users ALTER COLUMN subscription_status SET NOT NULL;

ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS username VARCHAR(128);
ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS full_name VARCHAR(255);
UPDATE voting_members
SET full_name = CONCAT('User ', tg_user_id::text)
WHERE full_name IS NULL OR length(trim(full_name)) = 0;
ALTER TABLE voting_members ALTER COLUMN full_name SET NOT NULL;
ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS language_code VARCHAR(10);
ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS is_bot_admin BOOLEAN;
UPDATE voting_members SET is_bot_admin = FALSE WHERE is_bot_admin IS NULL;
ALTER TABLE voting_members ALTER COLUMN is_bot_admin SET DEFAULT FALSE;
ALTER TABLE voting_members ALTER COLUMN is_bot_admin SET NOT NULL;
ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS is_bot_admin_text TEXT GENERATED ALWAYS AS (
    CASE WHEN is_bot_admin THEN 'TRUE' ELSE 'FALSE' END
) STORED;
ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS member_status TEXT;
UPDATE voting_members SET member_status = 'ACTIVE' WHERE member_status IS NULL;
ALTER TABLE voting_members ALTER COLUMN member_status SET DEFAULT 'ACTIVE';
ALTER TABLE voting_members ALTER COLUMN member_status SET NOT NULL;
ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ;
ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
UPDATE voting_members SET first_seen_at = NOW() WHERE first_seen_at IS NULL;
UPDATE voting_members SET last_seen_at = NOW() WHERE last_seen_at IS NULL;
ALTER TABLE voting_members ALTER COLUMN first_seen_at SET DEFAULT NOW();
ALTER TABLE voting_members ALTER COLUMN first_seen_at SET NOT NULL;
ALTER TABLE voting_members ALTER COLUMN last_seen_at SET DEFAULT NOW();
ALTER TABLE voting_members ALTER COLUMN last_seen_at SET NOT NULL;
ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ;
ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE voting_members ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
UPDATE voting_members SET created_at = NOW() WHERE created_at IS NULL;
UPDATE voting_members SET updated_at = NOW() WHERE updated_at IS NULL;
ALTER TABLE voting_members ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE voting_members ALTER COLUMN created_at SET NOT NULL;
ALTER TABLE voting_members ALTER COLUMN updated_at SET DEFAULT NOW();
ALTER TABLE voting_members ALTER COLUMN updated_at SET NOT NULL;

INSERT INTO voting_members (
    tg_user_id,
    username,
    full_name,
    language_code,
    is_bot_admin,
    member_status,
    first_seen_at,
    last_seen_at,
    last_verified_at
)
SELECT
    u.tg_user_id,
    u.username,
    COALESCE(NULLIF(trim(u.full_name), ''), CONCAT('User ', u.tg_user_id::text)),
    u.language_code,
    TRUE,
    'ACTIVE',
    COALESCE(u.created_at, NOW()),
    NOW(),
    NOW()
FROM users u
WHERE COALESCE(u.is_bot_admin, FALSE) = TRUE
ON CONFLICT (tg_user_id) DO UPDATE
SET
    username = COALESCE(EXCLUDED.username, voting_members.username),
    full_name = COALESCE(EXCLUDED.full_name, voting_members.full_name),
    language_code = COALESCE(EXCLUDED.language_code, voting_members.language_code),
    is_bot_admin = TRUE,
    member_status = 'ACTIVE',
    last_seen_at = NOW(),
    last_verified_at = NOW(),
    updated_at = NOW();

ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_chat_id BIGINT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_message_id INTEGER;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_poll_id TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_status TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_closes_at TIMESTAMPTZ;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_yes_count INTEGER;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS vote_no_count INTEGER;

ALTER TABLE library_topics ADD COLUMN IF NOT EXISTS title VARCHAR(255);
ALTER TABLE library_topics ADD COLUMN IF NOT EXISTS sort_order INTEGER;
UPDATE library_topics SET sort_order = 0 WHERE sort_order IS NULL;
ALTER TABLE library_topics ALTER COLUMN sort_order SET DEFAULT 0;
ALTER TABLE library_topics ALTER COLUMN sort_order SET NOT NULL;
ALTER TABLE library_topics ADD COLUMN IF NOT EXISTS is_active BOOLEAN;
UPDATE library_topics SET is_active = TRUE WHERE is_active IS NULL;
ALTER TABLE library_topics ALTER COLUMN is_active SET DEFAULT TRUE;
ALTER TABLE library_topics ALTER COLUMN is_active SET NOT NULL;
ALTER TABLE library_topics ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE library_topics ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
UPDATE library_topics SET created_at = NOW() WHERE created_at IS NULL;
UPDATE library_topics SET updated_at = NOW() WHERE updated_at IS NULL;
ALTER TABLE library_topics ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE library_topics ALTER COLUMN created_at SET NOT NULL;
ALTER TABLE library_topics ALTER COLUMN updated_at SET DEFAULT NOW();
ALTER TABLE library_topics ALTER COLUMN updated_at SET NOT NULL;

ALTER TABLE library_articles ADD COLUMN IF NOT EXISTS topic_id BIGINT;
ALTER TABLE library_articles ADD COLUMN IF NOT EXISTS title VARCHAR(255);
ALTER TABLE library_articles ADD COLUMN IF NOT EXISTS content TEXT;
ALTER TABLE library_articles ADD COLUMN IF NOT EXISTS sort_order INTEGER;
UPDATE library_articles SET sort_order = 0 WHERE sort_order IS NULL;
ALTER TABLE library_articles ALTER COLUMN sort_order SET DEFAULT 0;
ALTER TABLE library_articles ALTER COLUMN sort_order SET NOT NULL;
ALTER TABLE library_articles ADD COLUMN IF NOT EXISTS is_active BOOLEAN;
UPDATE library_articles SET is_active = TRUE WHERE is_active IS NULL;
ALTER TABLE library_articles ALTER COLUMN is_active SET DEFAULT TRUE;
ALTER TABLE library_articles ALTER COLUMN is_active SET NOT NULL;
ALTER TABLE library_articles ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE library_articles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
UPDATE library_articles SET created_at = NOW() WHERE created_at IS NULL;
UPDATE library_articles SET updated_at = NOW() WHERE updated_at IS NULL;
ALTER TABLE library_articles ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE library_articles ALTER COLUMN created_at SET NOT NULL;
ALTER TABLE library_articles ALTER COLUMN updated_at SET DEFAULT NOW();
ALTER TABLE library_articles ALTER COLUMN updated_at SET NOT NULL;

INSERT INTO library_topics (title, sort_order, is_active)
VALUES
    ('Рентген КТ', 10, TRUE),
    ('УЗД', 20, TRUE),
    ('Направлення на дослідженя', 30, TRUE),
    ('МРТ', 40, TRUE)
ON CONFLICT (title) DO NOTHING;

INSERT INTO library_articles (topic_id, title, content, sort_order, is_active)
SELECT t.id, v.title, v.content, v.sort_order, TRUE
FROM (
    VALUES
        ('Рентген КТ', 'Базові принципи Рентген/КТ', 'Тестова стаття: огляд базових принципів діагностики Рентген/КТ.', 10),
        ('Рентген КТ', 'Часті помилки у Рентген/КТ', 'Тестова стаття: типові помилки та як їх уникати.', 20),
        ('УЗД', 'УЗД: базовий протокол', 'Тестова стаття: послідовність базового УЗД-обстеження.', 10),
        ('УЗД', 'УЗД черевної порожнини', 'Тестова стаття: ключові зони огляду для щоденної практики.', 20),
        ('Направлення на дослідженя', 'Як правильно оформити направлення', 'Тестова стаття: обовʼязкові поля та чекліст перед відправкою.', 10),
        ('Направлення на дослідженя', 'Поширені помилки у направленнях', 'Тестова стаття: приклади неточностей і як їх виправити.', 20),
        ('МРТ', 'МРТ: показання до дослідження', 'Тестова стаття: коли доцільно направляти пацієнта на МРТ.', 10),
        ('МРТ', 'Підготовка пацієнта до МРТ', 'Тестова стаття: мінімальні вимоги перед проведенням МРТ.', 20)
) AS v(topic_title, title, content, sort_order)
JOIN library_topics t ON t.title = v.topic_title
ON CONFLICT (topic_id, title) DO NOTHING;

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
CREATE INDEX IF NOT EXISTS idx_users_is_bot_admin ON users (is_bot_admin);
CREATE INDEX IF NOT EXISTS idx_voting_members_status ON voting_members (member_status);
CREATE INDEX IF NOT EXISTS idx_voting_members_is_bot_admin ON voting_members (is_bot_admin);
CREATE INDEX IF NOT EXISTS idx_voting_members_last_seen_at ON voting_members (last_seen_at);

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

CREATE INDEX IF NOT EXISTS idx_application_votes_application_id
    ON application_votes (application_id);

CREATE INDEX IF NOT EXISTS idx_library_topics_active_sort
    ON library_topics (is_active, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_library_articles_topic_active_sort
    ON library_articles (topic_id, is_active, sort_order, id);
