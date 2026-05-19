"""
Senpai TV — Telegram notification helper
Sends updates to the configured bot/chat. Never crashes the scraper if Telegram is down.
"""
import os
import requests as _requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

def _send(text: str, parse_mode: str = "HTML") -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        r = _requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode,
                  "disable_web_page_preview": True},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def notify_cycle_start(cycle: int, total_in_db: int, episodes_in_db: int):
    _send(
        f"🟣 <b>Senpai TV Scraper</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"▶️ Cycle <b>#{cycle}</b> started\n"
        f"📚 DB now: <b>{total_in_db:,}</b> titles · <b>{episodes_in_db:,}</b> episodes\n"
        f"🔍 Discovering all content…"
    )


def notify_discovery_done(cycle: int, total: int, series: int, movies: int):
    _send(
        f"🔎 <b>Senpai TV</b> — Cycle #{cycle} discovery done\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Found <b>{total:,}</b> titles to scrape\n"
        f"🎬 Series: <b>{series:,}</b>  |  🎥 Movies: <b>{movies:,}</b>\n"
        f"⚙️ Scraping now…"
    )


def notify_progress(cycle: int, current: int, total: int, title: str, status: str):
    """Called every 50 titles so Telegram doesn't get flooded."""
    pct = int(current / total * 100) if total else 0
    filled = pct // 5
    bar = "█" * filled + "░" * (20 - filled)
    _send(
        f"⚙️ <b>Senpai TV</b> — Cycle #{cycle}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<code>{bar}</code> {pct}%\n"
        f"📌 <b>{current:,}/{total:,}</b> processed\n"
        f"🎌 Now: <b>{title}</b>\n"
        f"📝 Status: {status}"
    )


def notify_new_title(title: str, content_type: str, episodes: int):
    """Sent for every brand-new title found."""
    icon = "🎬" if content_type == "movie" else "📺"
    _send(
        f"{icon} <b>New title added!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎌 <b>{title}</b>\n"
        f"📂 Type: {content_type.capitalize()}\n"
        f"🎞️ Episodes: <b>{episodes}</b>"
    )


def notify_cycle_done(cycle: int, new: int, updated: int, skipped: int,
                      ep_new: int, srv_new: int, errors: int, elapsed_str: str,
                      next_run_str: str):
    status = "✅ All good" if errors == 0 else f"⚠️ {errors} error(s)"
    _send(
        f"✅ <b>Senpai TV</b> — Cycle #{cycle} complete\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"➕ New titles:    <b>{new:,}</b>\n"
        f"✏️ Updated:       <b>{updated:,}</b>\n"
        f"⏭️ Skipped:       <b>{skipped:,}</b>\n"
        f"🎞️ Episodes added: <b>{ep_new:,}</b>\n"
        f"🖥️ Servers added:  <b>{srv_new:,}</b>\n"
        f"⏱️ Time taken:     <b>{elapsed_str}</b>\n"
        f"🔴 Errors:        <b>{errors}</b> — {status}\n"
        f"⏰ Next run:      {next_run_str}"
    )


def notify_error(cycle: int, context: str, error: str):
    _send(
        f"❌ <b>Senpai TV</b> — Error in Cycle #{cycle}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Where: {context}\n"
        f"💥 Error: <code>{error[:300]}</code>"
    )
