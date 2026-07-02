---
name: Project setup
description: How this Senpai TV project is structured and what was needed to get it running on Replit
---

## Stack
- Python 3, plain `HTTPServer` (no Flask) serving dashboard on port 5000
- `pipeline.py` scrapes animesalt.ac → Supabase
- `bot.py` runs Telegram bot in a daemon thread via `python-telegram-bot>=20`
- `telegram_bot.py` is only a lightweight scraper notification helper (not the real bot)

## Setup required on Replit
- `pip install requests beautifulsoup4 lxml supabase python-dotenv "python-telegram-bot[ext]>=20.0"` — these were missing and caused `No module named 'requests'` at startup
- All secrets set: SUPABASE_URL, SUPABASE_SERVICE_KEY, TELEGRAM_BOT_TOKEN, ADMIN_USER_IDS, GITHUB_TOKEN

**Why:** Replit's NixOS environment doesn't auto-install pip packages from requirements.txt unless configured; they must be installed explicitly.

**How to apply:** Any new dependency must be `pip install`-ed, not just added to requirements.txt.
