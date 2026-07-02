# Senpai TV — Anime Scraper & Bot

A Python service that scrapes AnimeSalt.ac into a Supabase database and serves a Telegram bot for browsing the library.

## Stack

- **Python 3** — scraper, web dashboard, Telegram bot
- **Supabase** — PostgreSQL database (REST via `supabase-py`)
- **python-telegram-bot** — Telegram bot framework
- **BeautifulSoup + lxml** — HTML parsing
- **requests** — HTTP client

## Files

| File | Purpose |
|---|---|
| `main.py` | HTTP dashboard server + scraper orchestrator + bot launcher |
| `pipeline.py` | Core scrape pipeline (sitemap → pages → DB) |
| `bot.py` | Telegram bot (user management, browse, search, episodes) |
| `telegram_bot.py` | Lightweight notification helper for scraper → Telegram |
| `setup_db.sql` | **Run this in Supabase SQL Editor to create all tables** |
| `requirements.txt` | Python dependencies |
| `render.yaml` | Render.com deployment config |

## Required Secrets (set in Replit Secrets)

| Key | Description |
|---|---|
| `SUPABASE_URL` | Supabase project URL (https://xxx.supabase.co) |
| `SUPABASE_SERVICE_KEY` | Supabase service_role JWT key |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `ADMIN_USER_IDS` | Comma-separated Telegram user IDs of admins |

## Environment Variables

| Key | Default | Description |
|---|---|---|
| `PORT` | 5000 | HTTP dashboard port |
| `SCRAPE_INTERVAL_HOURS` | 6 | Hours between scrape cycles |
| `TELEGRAM_CHAT_ID` | — | Optional: chat ID for scraper notifications |

## First-time Database Setup

**Important:** Run `setup_db.sql` in [Supabase SQL Editor](https://supabase.com/dashboard) to create all required tables:

```
content, episodes, video_servers, genres, content_genres, bot_users
```

The `bot_users` table is required for the Telegram bot's user management (approve/deny access requests).

## How It Works

1. `main.py` starts the HTTP dashboard on port 5000 and launches the Telegram bot in a background thread
2. The scraper runs a full crawl of AnimeSalt.ac every `SCRAPE_INTERVAL_HOURS` hours
3. Content, episodes, and video servers are stored in Supabase
4. The Telegram bot lets approved users browse the library and watch episodes

## Running

```bash
pip install -r requirements.txt
python main.py
```

## Telegram Bot Commands

**Admin:** `/admin`, `/users`, `/allow <id>`, `/block <id>`, `/grant <id>`, `/revoke <id>`, `/broadcast <msg>`, `/scraper`
**Users:** `/start`, `/anime`, `/search <title>`, `/new`, `/stats`, `/profile`

## Deploy to Railway / Render

1. Push to GitHub (see `render.yaml` for Render config)
2. Set all secrets as environment variables in your deployment platform
3. Run `setup_db.sql` in Supabase SQL Editor
4. Deploy — the service auto-starts scraping after the first cycle

## User Preferences

- Keep existing project structure and Python stack
- All sensitive values managed as Replit Secrets
