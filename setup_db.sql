-- ════════════════════════════════════════════════════════════════
--  Senpai TV — Complete Database Setup
--  Run this once in: Supabase Dashboard → SQL Editor → New Query
-- ════════════════════════════════════════════════════════════════

-- ── Content (anime, movies, cartoons) ──────────────────────────
CREATE TABLE IF NOT EXISTS content (
    id               UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    title            TEXT        NOT NULL,
    description      TEXT,
    type             TEXT        NOT NULL DEFAULT 'series',  -- 'series' | 'movie'
    release_year     INT,
    rating           NUMERIC(3,1) DEFAULT 0,
    poster_url       TEXT,
    banner_url       TEXT,
    thumbnail_url    TEXT,
    duration_minutes INT,
    language         TEXT,
    status           TEXT,                                   -- 'ongoing' | 'completed'
    featured         BOOLEAN     DEFAULT FALSE,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS content_title_idx   ON content(title);
CREATE INDEX IF NOT EXISTS content_type_idx    ON content(type);
CREATE INDEX IF NOT EXISTS content_status_idx  ON content(status);
CREATE INDEX IF NOT EXISTS content_created_idx ON content(created_at DESC);

-- ── Episodes ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS episodes (
    id               UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    content_id       UUID        NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    season_number    INT         NOT NULL DEFAULT 1,
    episode_number   INT         NOT NULL DEFAULT 1,
    title            TEXT,
    description      TEXT,
    thumbnail_url    TEXT,
    duration_seconds INT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (content_id, season_number, episode_number)
);
CREATE INDEX IF NOT EXISTS episodes_content_idx ON episodes(content_id);
CREATE INDEX IF NOT EXISTS episodes_order_idx   ON episodes(content_id, season_number, episode_number);

-- ── Video Servers ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS video_servers (
    id           UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    episode_id   UUID        NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    server_name  TEXT        NOT NULL DEFAULT 'SERVER',
    stream_url   TEXT        NOT NULL,
    quality      TEXT        DEFAULT '1080p',
    language     TEXT        DEFAULT 'Japanese',
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS video_servers_episode_idx ON video_servers(episode_id);

-- ── Genres ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS genres (
    id         UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Content ↔ Genres (many-to-many) ────────────────────────────
CREATE TABLE IF NOT EXISTS content_genres (
    id         UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    content_id UUID NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    genre_id   UUID NOT NULL REFERENCES genres(id)  ON DELETE CASCADE,
    UNIQUE (content_id, genre_id)
);
CREATE INDEX IF NOT EXISTS content_genres_content_idx ON content_genres(content_id);
CREATE INDEX IF NOT EXISTS content_genres_genre_idx   ON content_genres(genre_id);

-- ── Bot Users (Telegram bot access management) ──────────────────
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
