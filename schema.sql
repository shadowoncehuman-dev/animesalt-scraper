-- Senpai TV Bot — User Management Table
-- Run this once in your Supabase SQL Editor

CREATE TABLE IF NOT EXISTS bot_users (
    id           UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    telegram_id  TEXT        UNIQUE NOT NULL,
    username     TEXT        DEFAULT '',
    first_name   TEXT        DEFAULT '',
    allowed      BOOLEAN     DEFAULT FALSE,
    can_watch    BOOLEAN     DEFAULT FALSE,
    requested    BOOLEAN     DEFAULT FALSE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    allowed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS bot_users_telegram_id_idx ON bot_users(telegram_id);
CREATE INDEX IF NOT EXISTS bot_users_allowed_idx     ON bot_users(allowed);
CREATE INDEX IF NOT EXISTS bot_users_requested_idx   ON bot_users(requested);
