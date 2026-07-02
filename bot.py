#!/usr/bin/env python3
"""
Senpai TV — Telegram Bot
Anime database bot with admin user management, browsing, search,
episode access, scraper status, broadcasts, and more.
"""

import os, re, asyncio, logging, math, random, threading, time
import requests as _http
from functools import wraps
from typing import Optional
from datetime import datetime, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from supabase import create_client, Client as SBClient

log = logging.getLogger("senpai_bot")

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SB_URL     = os.environ.get("SUPABASE_URL", "")
SB_KEY     = os.environ.get("SUPABASE_SERVICE_KEY", "")
DASHBOARD_PORT = int(os.environ.get("PORT", 5000))

_raw_admins = os.environ.get("ADMIN_USER_IDS", "") or os.environ.get("TELEGRAM_CHAT_ID", "")
ADMIN_IDS: set[int] = set(
    int(x.strip()) for x in _raw_admins.replace(",", " ").split() if x.strip().lstrip("-").isdigit()
)

NEKOS_UA = "SenpaiTV-AnimeBot (https://github.com/shadowoncehuman-dev/animesalt-scraper)"

# ── Image APIs ─────────────────────────────────────────────────────────────────
_GIF_HAPPY   = ["wave", "wink", "happy", "smile", "thumbsup", "nod", "clap", "dance", "teehee"]
_GIF_WELCOME = ["wave", "happy", "smile", "nod", "thumbsup"]
_GIF_APPROVE = ["clap", "thumbsup", "happy", "dance", "highfive"]
_GIF_SEARCH  = ["think", "stare", "nod", "lurk"]
_IMG_CATS    = ["waifu", "neko", "kitsune"]


def _neko_gif(category_list: list = None) -> Optional[str]:
    cats = category_list or _GIF_HAPPY
    cat = random.choice(cats)
    try:
        r = _http.get(
            f"https://nekos.best/api/v2/{cat}",
            headers={"User-Agent": NEKOS_UA}, timeout=6
        )
        if r.ok:
            results = r.json().get("results", [])
            if results:
                return results[0]["url"]
    except Exception:
        pass
    return None


def _waifu_img() -> Optional[str]:
    try:
        r = _http.get(
            "https://api.waifu.im/images",
            params={"IncludedTags": "waifu", "IsNsfw": "False"},
            headers={"User-Agent": NEKOS_UA}, timeout=6
        )
        if r.ok:
            items = r.json().get("items", [])
            if items:
                return items[0]["url"]
    except Exception:
        pass
    # Fallback: nekos.best image
    try:
        cat = random.choice(_IMG_CATS)
        r = _http.get(
            f"https://nekos.best/api/v2/{cat}",
            headers={"User-Agent": NEKOS_UA}, timeout=6
        )
        if r.ok:
            results = r.json().get("results", [])
            if results:
                return results[0]["url"]
    except Exception:
        pass
    return None


async def _send_with_media(msg_or_chat, text: str, kb=None, gif_list: list = None,
                            use_image: bool = False, bot=None, chat_id: int = None):
    """Helper: send a message with a GIF or image if available, fallback to text."""
    parse_mode = ParseMode.HTML
    kwargs = {"parse_mode": parse_mode, "reply_markup": kb}

    if use_image:
        img = _waifu_img()
        if img:
            try:
                if chat_id and bot:
                    await bot.send_photo(chat_id=chat_id, photo=img, caption=text, **kwargs)
                    return
                elif hasattr(msg_or_chat, "reply_photo"):
                    await msg_or_chat.reply_photo(photo=img, caption=text, **kwargs)
                    return
            except Exception:
                pass
    else:
        gif = _neko_gif(gif_list)
        if gif:
            try:
                if chat_id and bot:
                    await bot.send_animation(chat_id=chat_id, animation=gif, caption=text, **kwargs)
                    return
                elif hasattr(msg_or_chat, "reply_animation"):
                    await msg_or_chat.reply_animation(animation=gif, caption=text, **kwargs)
                    return
            except Exception:
                pass

    if chat_id and bot:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    else:
        await msg_or_chat.reply_text(text, **kwargs)


async def _schedule_delete(bot, chat_id: int, message_id: int, delay_sec: int):
    """Delete a message after `delay_sec` seconds — used for auto-expiring episode videos."""
    if delay_sec <= 0:
        return
    try:
        await asyncio.sleep(delay_sec)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        log.info(f"[auto-delete] Removed msg {message_id} in chat {chat_id}")
    except Exception as e:
        log.debug(f"[auto-delete] Could not delete msg {message_id}: {e}")


async def _try_send_video_file(ctx, chat_id: int, video_url: str, caption: str,
                                duration_sec: int = 0, thumbnail_url: str = None) -> bool:
    """
    Attempt to deliver a video directly in Telegram.
    Only works for direct .mp4/.webm/.m4v URLs (not embed pages).
    Sent with protect_content=True (prevents forwarding/saving).
    Schedules auto-delete after duration_sec if provided.
    Returns True on success.
    """
    url_l = video_url.lower().split("?")[0]
    # Only attempt for direct media file URLs
    is_direct = url_l.endswith(".mp4") or url_l.endswith(".webm") or url_l.endswith(".m4v")
    if not is_direct:
        return False
    try:
        msg = await ctx.bot.send_video(
            chat_id=chat_id,
            video=video_url,
            caption=caption,
            parse_mode=ParseMode.HTML,
            duration=duration_sec or None,
            thumbnail=thumbnail_url or None,
            protect_content=True,
            supports_streaming=True,
            read_timeout=60,
            write_timeout=60,
        )
        if duration_sec and duration_sec > 0:
            asyncio.create_task(_schedule_delete(ctx.bot, chat_id, msg.message_id, duration_sec))
        return True
    except Exception as e:
        log.debug(f"Direct video send failed ({video_url[:60]}): {e}")
        return False


async def _edit_msg(q, text: str, kb=None):
    """Edit a message regardless of whether it's text, photo, or animation."""
    kwargs = {"parse_mode": ParseMode.HTML, "reply_markup": kb}
    try:
        await q.edit_message_text(text, **kwargs)
        return
    except Exception:
        pass
    try:
        await q.edit_message_caption(caption=text, **kwargs)
        return
    except Exception:
        pass
    await q.message.reply_text(text, **kwargs)


# ── Database helpers ───────────────────────────────────────────────────────────
def db() -> Optional[SBClient]:
    if SB_URL and SB_KEY:
        return create_client(SB_URL, SB_KEY)
    return None


def _get_user(d: SBClient, tg_id: int) -> Optional[dict]:
    try:
        r = d.table("bot_users").select("*").eq("telegram_id", str(tg_id)).execute()
        return r.data[0] if r.data else None
    except Exception as e:
        if "PGRST205" in str(e):
            log.error("bot_users table missing — run setup_db.sql in Supabase SQL editor")
        return None


def _upsert_user(d: SBClient, tg_id: int, username: str, first_name: str, **kw):
    try:
        existing = _get_user(d, tg_id)
        if existing is None and not _bot_users_exist(d):
            log.error("bot_users table missing — user data cannot be saved")
            return
        row = {"telegram_id": str(tg_id), "username": username or "", "first_name": first_name or "", **kw}
        if existing:
            d.table("bot_users").update(kw or row).eq("telegram_id", str(tg_id)).execute()
        else:
            row.setdefault("allowed", False)
            row.setdefault("can_watch", False)
            row.setdefault("requested", False)
            d.table("bot_users").insert(row).execute()
    except Exception as e:
        log.warning(f"upsert_user: {e}")


def _all_users(d: SBClient) -> list:
    try:
        return d.table("bot_users").select("*").order("created_at", desc=True).execute().data or []
    except Exception:
        return []


def _allowed_users(d: SBClient) -> list:
    try:
        return d.table("bot_users").select("*").eq("allowed", True).execute().data or []
    except Exception:
        return []


def _pending_users(d: SBClient) -> list:
    try:
        return d.table("bot_users").select("*").eq("requested", True).eq("allowed", False).execute().data or []
    except Exception:
        return []


def _bot_users_exist(d: SBClient) -> bool:
    """Return True if the bot_users table exists in the DB."""
    try:
        d.table("bot_users").select("telegram_id").limit(1).execute()
        return True
    except Exception as e:
        return "PGRST205" not in str(e)  # PGRST205 = table not found


def _db_stats(d: SBClient) -> dict:
    try:
        titles  = d.table("content").select("id", count="exact", head=True).execute().count or 0
        eps     = d.table("episodes").select("id", count="exact", head=True).execute().count or 0
        srvs    = d.table("video_servers").select("id", count="exact", head=True).execute().count or 0
        anime   = d.table("content").select("id", count="exact", head=True).eq("type", "series").execute().count or 0
        movies  = d.table("content").select("id", count="exact", head=True).eq("type", "movie").execute().count or 0
        users, allowed = 0, 0
        if _bot_users_exist(d):
            users   = d.table("bot_users").select("id", count="exact", head=True).execute().count or 0
            allowed = d.table("bot_users").select("id", count="exact", head=True).eq("allowed", True).execute().count or 0
        return {"titles": titles, "eps": eps, "servers": srvs, "anime": anime,
                "movies": movies, "users": users, "allowed": allowed}
    except Exception:
        return {}


def _search(d: SBClient, q: str, limit: int = 15) -> list:
    try:
        return d.table("content").select(
            "id,title,type,release_year,rating,poster_url,language,status"
        ).ilike("title", f"%{q}%").limit(limit).execute().data or []
    except Exception:
        return []


def _content_page(d: SBClient, page: int, size: int = 8, ctype: str = None,
                  language: str = None, genre_slug: str = None, sort: str = "title"):
    try:
        offset = (page - 1) * size
        q = d.table("content").select("id,title,type,release_year,rating,poster_url,language,status")
        tq = d.table("content").select("id", count="exact", head=True)
        if ctype and ctype != "all":
            q, tq = q.eq("type", ctype), tq.eq("type", ctype)
        if language:
            q, tq = q.ilike("language", f"%{language}%"), tq.ilike("language", f"%{language}%")
        total = tq.execute().count or 0
        if sort == "newest":
            q = q.order("created_at", desc=True)
        elif sort == "rating":
            q = q.order("rating", desc=True)
        else:
            q = q.order("title")
        items = q.range(offset, offset + size - 1).execute().data or []
        return items, total
    except Exception:
        return [], 0


def _recently_added(d: SBClient, limit: int = 20) -> list:
    try:
        return d.table("content").select(
            "id,title,type,release_year,rating,poster_url,language,status,created_at"
        ).order("created_at", desc=True).limit(limit).execute().data or []
    except Exception:
        return []


def _content_detail(d: SBClient, cid: str) -> Optional[dict]:
    try:
        r = d.table("content").select("*").eq("id", cid).execute()
        return r.data[0] if r.data else None
    except Exception:
        return None


def _genres(d: SBClient, cid: str) -> list[str]:
    try:
        r = d.table("content_genres").select("genres(name)").eq("content_id", cid).execute()
        return [x["genres"]["name"] for x in r.data if x.get("genres")] if r.data else []
    except Exception:
        return []


def _episodes(d: SBClient, cid: str) -> list:
    try:
        return d.table("episodes").select(
            "id,season_number,episode_number,title,content_id"
        ).eq("content_id", cid).order("season_number").order("episode_number").execute().data or []
    except Exception:
        return []


def _servers(d: SBClient, ep_id: str) -> list:
    try:
        return d.table("video_servers").select(
            "server_name,stream_url,quality,language"
        ).eq("episode_id", ep_id).execute().data or []
    except Exception:
        return []


def _ep_detail(d: SBClient, ep_id: str) -> Optional[dict]:
    try:
        r = d.table("episodes").select("*").eq("id", ep_id).execute()
        return r.data[0] if r.data else None
    except Exception:
        return None


def _get_scraper_status() -> Optional[dict]:
    """Fetch live scraper state from dashboard API."""
    try:
        r = _http.get(f"http://localhost:{DASHBOARD_PORT}/status", timeout=3)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return None


# ── Auth decorators ────────────────────────────────────────────────────────────
def admin_only(fn):
    @wraps(fn)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *a, **kw):
        if update.effective_user.id not in ADMIN_IDS:
            await update.effective_message.reply_text("⛔ Admin only.")
            return
        return await fn(update, ctx, *a, **kw)
    return wrapper


def approved_only(fn):
    @wraps(fn)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *a, **kw):
        uid = update.effective_user.id
        if uid in ADMIN_IDS:
            return await fn(update, ctx, *a, **kw)
        d = db()
        u = _get_user(d, uid) if d else None
        if not u or not u.get("allowed"):
            await update.effective_message.reply_text(
                "🔒 You need access to use this.\nSend /start to request access."
            )
            return
        return await fn(update, ctx, *a, **kw)
    return wrapper


# ── Formatting helpers ─────────────────────────────────────────────────────────
def _stars(r) -> str:
    r = float(r or 0)
    n = round(r / 2)
    return "★" * n + "☆" * (5 - n)


def _card(c: dict, genres: list = None) -> str:
    icon = "🎬" if c.get("type") == "movie" else "📺"
    ctype = c.get("type", "?").upper()
    rating = float(c.get("rating") or 0)
    desc = c.get("description") or ""
    if len(desc) > 350:
        desc = desc[:350] + "…"
    lines = [
        f"{icon} <b>{c['title']}</b>",
        "",
        f"📂 <b>Type:</b> {ctype}",
        f"🌏 <b>Language:</b> {c.get('language') or '—'}",
        f"📅 <b>Year:</b> {c.get('release_year') or '—'}",
        f"⭐ <b>Rating:</b> {_stars(rating)} ({rating}/10)" if rating else "⭐ <b>Rating:</b> N/A",
        f"📊 <b>Status:</b> {(c.get('status') or '—').title()}",
    ]
    if genres:
        lines.append(f"🏷️ <b>Genres:</b> {' · '.join(genres[:5])}")
    if desc:
        lines += ["", f"📝 {desc}"]
    return "\n".join(lines)


def _can_watch(uid: int, u: Optional[dict]) -> bool:
    return uid in ADMIN_IDS or bool(u and u.get("can_watch"))


def _fmt_scraper_status(state: dict) -> str:
    running = state.get("running", False)
    status_icon = "🟢" if running else "🟡"
    phase = state.get("phase", "—")
    cycle = state.get("cycle", 0)
    current = state.get("current", 0)
    total = state.get("total", 0)
    pct = int(current / total * 100) if total else 0
    pct_bar = "█" * (pct // 5) + "░" * (20 - pct // 5)

    lines = [
        f"{status_icon} <b>Scraper Status</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🔄 Cycle: <b>#{cycle}</b>",
        f"📍 Phase: <b>{phase}</b>",
    ]
    if total:
        lines.append(f"<code>{pct_bar}</code> {pct}%")
        lines.append(f"📋 Progress: <b>{current:,}/{total:,}</b> titles")
    if state.get("titles_new") or state.get("titles_updated"):
        lines += [
            "",
            f"✅ New: <b>{state.get('titles_new', 0):,}</b>",
            f"✏️ Updated: <b>{state.get('titles_updated', 0):,}</b>",
            f"⏭️ Skipped: <b>{state.get('titles_skipped', 0):,}</b>",
            f"🎞️ Episodes: <b>{state.get('episodes_new', 0):,}</b>",
            f"🖥️ Servers: <b>{state.get('servers_new', 0):,}</b>",
            f"❌ Errors: <b>{state.get('errors', 0):,}</b>",
        ]
    if state.get("last_started"):
        lines.append(f"\n🕐 Started: <b>{state['last_started']}</b>")
    if state.get("last_finished"):
        lines.append(f"✅ Last done: <b>{state['last_finished']}</b>")
    if state.get("next_run"):
        lines.append(f"⏰ Next run: <b>{state['next_run']}</b>")
    if state.get("current_title") and running:
        lines.append(f"\n🎌 Now: <b>{state['current_title'][:40]}</b>")
    return "\n".join(lines)


# ── /start ─────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = user.id
    d    = db()

    if d and uid not in ADMIN_IDS:
        existing = _get_user(d, uid)
        if not existing:
            _upsert_user(d, uid, user.username or "", user.first_name or "",
                         allowed=False, can_watch=False, requested=False)
            existing = _get_user(d, uid)
    else:
        existing = {"allowed": True, "can_watch": True}

    if uid in ADMIN_IDS:
        text = (
            f"👋 Welcome back, <b>{user.first_name}</b>!\n\n"
            "🛡️ <b>Admin access active.</b>\n\n"
            "Manage users, browse the library, and monitor the scraper."
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛡️ Admin Panel", callback_data="admin:panel"),
            InlineKeyboardButton("🎌 Browse", callback_data="anime:pg:1:all:title"),
        ], [
            InlineKeyboardButton("📊 Stats", callback_data="show:stats"),
            InlineKeyboardButton("🆕 Recently Added", callback_data="show:new"),
        ], [
            InlineKeyboardButton("🖥️ Scraper Status", callback_data="show:scraper"),
        ]])
        await _send_with_media(update.message, text, kb, gif_list=_GIF_WELCOME)
    elif existing and existing.get("allowed"):
        text = (
            f"🎌 Welcome back, <b>{user.first_name}</b>!\n\n"
            "✅ <b>You have library access.</b>\n\n"
            "Browse thousands of anime, cartoons & movies."
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎌 Browse", callback_data="anime:pg:1:all:title"),
            InlineKeyboardButton("🔍 Search", callback_data="search:ask"),
        ], [
            InlineKeyboardButton("🆕 Recently Added", callback_data="show:new"),
            InlineKeyboardButton("📊 Stats", callback_data="show:stats"),
        ]])
        await _send_with_media(update.message, text, kb, gif_list=_GIF_WELCOME)
    else:
        requested = existing and existing.get("requested")
        if requested:
            text = (
                f"⏳ Hey <b>{user.first_name}</b>!\n\n"
                "Your access request is <b>pending admin approval</b>.\n\n"
                "You'll get a notification when approved. Please wait! ✨"
            )
            await _send_with_media(update.message, text, gif_list=_GIF_HAPPY)
        else:
            text = (
                f"🎌 <b>Welcome to Senpai TV!</b>\n\n"
                f"Hello, <b>{user.first_name}</b>! 👋\n\n"
                "A private anime & cartoon database with thousands of titles.\n\n"
                "Tap below to request library access."
            )
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🙋 Request Access", callback_data="user:request"),
            ]])
            await _send_with_media(update.message, text, kb, gif_list=_GIF_WELCOME)


# ── /help ──────────────────────────────────────────────────────────────────────
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in ADMIN_IDS:
        text = (
            "🛡️ <b>Admin Commands</b>\n"
            "/admin — Admin panel\n"
            "/scraper — Live scraper status\n"
            "/users — List all users\n"
            "/allow &lt;id&gt; — Approve user\n"
            "/block &lt;id&gt; — Block user\n"
            "/grant &lt;id&gt; — Grant episode access\n"
            "/revoke &lt;id&gt; — Revoke episode access\n"
            "/broadcast &lt;msg&gt; — Send to all users\n"
            "/manage &lt;id&gt; — Manage a specific user\n\n"
            "📺 <b>User Commands</b>\n"
            "/anime — Browse anime library\n"
            "/new — Recently added titles\n"
            "/search &lt;title&gt; — Search anime\n"
            "/stats — Library stats\n"
            "/profile — Your access status\n"
            "/help — This help"
        )
    else:
        text = (
            "🎌 <b>Senpai TV Commands</b>\n\n"
            "/start — Welcome screen\n"
            "/anime — Browse the library\n"
            "/new — Recently added titles\n"
            "/search &lt;title&gt; — Search for anime\n"
            "/stats — View library statistics\n"
            "/profile — Your access status\n"
            "/help — Show this help"
        )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /stats ─────────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = db()
    s = _db_stats(d) if d else {}
    text = (
        "📊 <b>Senpai TV — Library Stats</b>\n\n"
        f"📺 Anime Series: <b>{s.get('anime', 0):,}</b>\n"
        f"🎬 Movies:       <b>{s.get('movies', 0):,}</b>\n"
        f"📋 Total Titles: <b>{s.get('titles', 0):,}</b>\n"
        f"🎞️ Episodes:     <b>{s.get('eps', 0):,}</b>\n"
        f"🖥️ Servers:      <b>{s.get('servers', 0):,}</b>\n"
        f"👥 Users:        <b>{s.get('users', 0):,}</b>"
    )
    await _send_with_media(update.effective_message, text, use_image=True)


# ── /new ───────────────────────────────────────────────────────────────────────
@approved_only
async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_recently_added(update.message, edit=False)


# ── /anime ─────────────────────────────────────────────────────────────────────
@approved_only
async def cmd_anime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_anime_list(update.message, page=1, ctype="all", sort="title", edit=False)


# ── /search ────────────────────────────────────────────────────────────────────
@approved_only
async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "🔍 <b>Search Anime</b>\n\nUsage: <code>/search &lt;title&gt;</code>\nExample: <code>/search naruto</code>",
            parse_mode=ParseMode.HTML
        )
        return
    await _run_search(update.message, " ".join(ctx.args), edit=False)


# ── /profile ───────────────────────────────────────────────────────────────────
async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = update.effective_user
    d = db()

    if uid in ADMIN_IDS:
        text = (
            f"👤 <b>Your Profile</b>\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"👤 Name: <b>{user.first_name}</b>\n"
            f"🛡️ Role: <b>Admin</b>\n"
            f"✅ Access: <b>Full</b>\n"
            f"🎬 Watch: <b>All episodes</b>"
        )
    else:
        u = _get_user(d, uid) if d else None
        if not u:
            text = (
                f"👤 <b>Your Profile</b>\n\n"
                f"🆔 ID: <code>{uid}</code>\n"
                f"👤 Name: <b>{user.first_name}</b>\n"
                f"🔒 Status: Not registered — send /start"
            )
        else:
            status = "✅ Approved" if u.get("allowed") else ("⏳ Pending" if u.get("requested") else "❌ Not approved")
            watch = "🎬 Granted" if u.get("can_watch") else "🔒 Not granted"
            joined = u.get("created_at", "")[:10] if u.get("created_at") else "—"
            text = (
                f"👤 <b>Your Profile</b>\n\n"
                f"🆔 ID: <code>{uid}</code>\n"
                f"👤 Name: <b>{user.first_name}</b>\n"
                f"📊 Library Access: <b>{status}</b>\n"
                f"🎞️ Episode Access: <b>{watch}</b>\n"
                f"📅 Joined: <b>{joined}</b>"
            )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /scraper (admin) ───────────────────────────────────────────────────────────
@admin_only
async def cmd_scraper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state = _get_scraper_status()
    if not state:
        await update.effective_message.reply_text(
            "⚠️ Could not reach scraper dashboard.\nIs the scraper service running?",
            parse_mode=ParseMode.HTML
        )
        return
    text = _fmt_scraper_status(state)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data="show:scraper"),
        InlineKeyboardButton("◀️ Admin", callback_data="admin:panel"),
    ]])
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ── /broadcast (admin) ─────────────────────────────────────────────────────────
@admin_only
async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "📣 <b>Broadcast</b>\n\nUsage: <code>/broadcast &lt;message&gt;</code>\n"
            "This sends your message to all approved users.",
            parse_mode=ParseMode.HTML
        )
        return
    msg_text = " ".join(ctx.args)
    d = db()
    if not d:
        await update.message.reply_text("⚠️ Database unavailable.")
        return
    users = _allowed_users(d)
    sent = 0
    failed = 0
    broadcast_text = (
        f"📣 <b>Senpai TV Announcement</b>\n\n"
        f"{msg_text}"
    )
    status_msg = await update.message.reply_text(
        f"📣 Sending to {len(users)} users…", parse_mode=ParseMode.HTML
    )
    for u in users:
        try:
            tg_id = int(u["telegram_id"])
            if tg_id in ADMIN_IDS:
                continue
            gif = _neko_gif(_GIF_HAPPY)
            if gif:
                await ctx.bot.send_animation(
                    chat_id=tg_id, animation=gif,
                    caption=broadcast_text, parse_mode=ParseMode.HTML
                )
            else:
                await ctx.bot.send_message(
                    chat_id=tg_id, text=broadcast_text, parse_mode=ParseMode.HTML
                )
            sent += 1
            await asyncio.sleep(0.05)
        except (Forbidden, BadRequest):
            failed += 1
        except Exception as e:
            log.warning(f"Broadcast to {u['telegram_id']}: {e}")
            failed += 1

    try:
        await status_msg.edit_text(
            f"📣 Broadcast complete!\n✅ Sent: <b>{sent}</b>  ❌ Failed: <b>{failed}</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


# ── /manage (admin) ────────────────────────────────────────────────────────────
@admin_only
async def cmd_manage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "Usage: <code>/manage &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML
        )
        return
    try:
        target_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    d = db()
    u = _get_user(d, target_id) if d else None
    if not u:
        await update.message.reply_text(f"❌ User <code>{target_id}</code> not found.", parse_mode=ParseMode.HTML)
        return
    await _send_user_management(update.message, u, edit=False)


# ── Admin commands ─────────────────────────────────────────────────────────────
@admin_only
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_admin_panel(update.effective_message, edit=False)


@admin_only
async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = db()
    users = _all_users(d) if d else []
    if not users:
        await update.effective_message.reply_text("No registered users yet.")
        return
    lines = [f"👥 <b>All Users ({len(users)})</b>\n"]
    for u in users[:30]:
        s = "✅" if u.get("allowed") else ("⏳" if u.get("requested") else "❌")
        w = "🎬" if u.get("can_watch") else "🔒"
        name = u.get("first_name") or u.get("username") or "?"
        un = f"@{u['username']}" if u.get("username") else ""
        lines.append(f"{s}{w} <code>{u['telegram_id']}</code> <b>{name}</b> {un}")
    if len(users) > 30:
        lines.append(f"\n…and {len(users)-30} more")
    lines.append("\n✅Allowed ⏳Pending ❌Blocked  🎬WatchOK 🔒NoWatch")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_allow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /allow <user_id>"); return
    await _admin_set(update, ctx, ctx.args[0], allowed=True)

@admin_only
async def cmd_block(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /block <user_id>"); return
    await _admin_set(update, ctx, ctx.args[0], allowed=False)

@admin_only
async def cmd_grant(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /grant <user_id>"); return
    await _admin_set(update, ctx, ctx.args[0], can_watch=True)

@admin_only
async def cmd_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /revoke <user_id>"); return
    await _admin_set(update, ctx, ctx.args[0], can_watch=False)


async def _admin_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid_str: str, **kw):
    d = db()
    if not d:
        await update.effective_message.reply_text("⚠️ DB unavailable."); return
    try:
        target = int(uid_str)
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID."); return
    u = _get_user(d, target)
    if not u:
        await update.effective_message.reply_text(f"❌ User {target} not found."); return
    d.table("bot_users").update(kw).eq("telegram_id", str(target)).execute()

    msgs = {
        "allowed=True":    ("✅ User approved!", "🎉 <b>You've been approved!</b>\n\nWelcome to Senpai TV! 🎌\nUse /anime to browse the library."),
        "allowed=False":   ("🚫 User blocked.", None),
        "can_watch=True":  ("🎬 Watch access granted!", "🎬 <b>Watch access granted!</b>\n\nYou can now view episode lists and stream links.\nUse /anime to start! 🎌"),
        "can_watch=False": ("🔒 Watch access revoked.", None),
    }
    key = next(iter(kw.items()))
    k = f"{key[0]}={key[1]}"
    admin_msg, user_msg = msgs.get(k, ("✅ Done.", None))
    await update.effective_message.reply_text(admin_msg)
    if user_msg:
        try:
            gif = _neko_gif(_GIF_APPROVE)
            if gif:
                await ctx.bot.send_animation(chat_id=target, animation=gif,
                                             caption=user_msg, parse_mode=ParseMode.HTML)
            else:
                await ctx.bot.send_message(chat_id=target, text=user_msg, parse_mode=ParseMode.HTML)
        except (Forbidden, BadRequest) as e:
            log.warning(f"Could not notify user {target}: {e}")


# ── User management panel ──────────────────────────────────────────────────────
async def _send_user_management(msg, u: dict, edit: bool = True):
    name = u.get("first_name") or u.get("username") or "Unknown"
    username = f"@{u['username']}" if u.get("username") else ""
    tg_id = u["telegram_id"]
    allowed = u.get("allowed", False)
    can_watch = u.get("can_watch", False)
    requested = u.get("requested", False)
    joined = u.get("created_at", "")[:10] if u.get("created_at") else "—"

    status = "✅ Approved" if allowed else ("⏳ Pending" if requested else "❌ Blocked")
    watch = "🎬 Watch OK" if can_watch else "🔒 No watch"

    text = (
        f"👤 <b>User Management</b>\n\n"
        f"🆔 ID: <code>{tg_id}</code>\n"
        f"👤 Name: <b>{name}</b> {username}\n"
        f"📊 Access: <b>{status}</b>\n"
        f"🎞️ Episodes: <b>{watch}</b>\n"
        f"📅 Joined: <b>{joined}</b>"
    )

    rows = []
    if allowed:
        rows.append([InlineKeyboardButton("🚫 Block", callback_data=f"admin:block:{tg_id}")])
    else:
        rows.append([InlineKeyboardButton("✅ Approve", callback_data=f"admin:ok:{tg_id}"),
                     InlineKeyboardButton("❌ Deny", callback_data=f"admin:no:{tg_id}")])

    if can_watch:
        rows.append([InlineKeyboardButton("🔒 Revoke Watch", callback_data=f"admin:revoke:{tg_id}")])
    else:
        rows.append([InlineKeyboardButton("🎬 Grant Watch", callback_data=f"admin:grant:{tg_id}")])

    rows.append([InlineKeyboardButton("◀️ Back to Users", callback_data="admin:users:0")])
    kb = InlineKeyboardMarkup(rows)

    if edit:
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            pass
        try:
            await msg.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            pass
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ── Shared view builders ───────────────────────────────────────────────────────
async def _send_admin_panel(msg, edit: bool = True):
    d = db()
    s = _db_stats(d) if d else {}
    pending = _pending_users(d) if d else []
    users = _all_users(d) if d else []
    approved = sum(1 for u in users if u.get("allowed"))
    scraper = _get_scraper_status()
    scraper_status = "🟢 Scraping" if (scraper and scraper.get("running")) else "🟡 Idle"

    text = (
        "🛡️ <b>Admin Panel — Senpai TV</b>\n\n"
        f"👥 Users: <b>{len(users)}</b>  Approved: <b>{approved}</b>  🔔 Pending: <b>{len(pending)}</b>\n\n"
        f"📊 <b>{s.get('titles', 0):,}</b> titles · "
        f"<b>{s.get('eps', 0):,}</b> episodes · "
        f"<b>{s.get('servers', 0):,}</b> servers\n\n"
        f"🖥️ Scraper: <b>{scraper_status}</b>"
    )
    if scraper and scraper.get("running") and scraper.get("total"):
        current = scraper.get("current", 0)
        total = scraper.get("total", 0)
        pct = int(current / total * 100) if total else 0
        text += f"\n   └ Cycle #{scraper.get('cycle',0)} · {current}/{total} ({pct}%)"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔔 Requests ({len(pending)})", callback_data="admin:requests"),
         InlineKeyboardButton("👥 All Users", callback_data="admin:users:0")],
        [InlineKeyboardButton("🖥️ Scraper Status", callback_data="show:scraper"),
         InlineKeyboardButton("📊 DB Stats", callback_data="show:stats")],
        [InlineKeyboardButton("📣 Broadcast", callback_data="admin:broadcast:ask"),
         InlineKeyboardButton("🎌 Browse", callback_data="anime:pg:1:all:title")],
        [InlineKeyboardButton("🆕 Recently Added", callback_data="show:new")],
    ])
    if edit:
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            pass
        try:
            await msg.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            pass
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _send_recently_added(msg, edit: bool = True):
    d = db()
    if not d:
        await msg.reply_text("⚠️ Database unavailable."); return
    items = _recently_added(d, limit=15)
    if not items:
        text = "🆕 No titles in the database yet."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="anime:pg:1:all:title")]])
        if edit:
            try:
                await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb); return
            except BadRequest:
                pass
        await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    text = "🆕 <b>Recently Added</b>\n\n"
    rows = []
    for c in items:
        icon = "🎬" if c["type"] == "movie" else "📺"
        yr = f" ({c['release_year']})" if c.get("release_year") else ""
        rows.append([InlineKeyboardButton(
            f"{icon} {c['title'][:28]}{yr}",
            callback_data=f"anime:view:{c['id']}"
        )])

    rows.append([
        InlineKeyboardButton("🎌 Browse All", callback_data="anime:pg:1:all:title"),
        InlineKeyboardButton("🔍 Search", callback_data="search:ask"),
    ])
    kb = InlineKeyboardMarkup(rows)
    if edit:
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb); return
        except Exception:
            pass
        try:
            await msg.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb); return
        except Exception:
            pass
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _send_anime_list(msg, page: int, ctype: str, sort: str = "title",
                            language: str = None, edit: bool = True):
    d = db()
    if not d:
        await msg.reply_text("⚠️ Database unavailable."); return
    ct = None if ctype == "all" else ctype
    items, total = _content_page(d, page, size=8, ctype=ct, language=language, sort=sort)
    total_pages = max(1, math.ceil(total / 8))

    type_label = {"series": "📺 Anime/Series", "movie": "🎬 Movies"}.get(ctype, "🎌 All Titles")
    sort_label = {"newest": " · Newest", "rating": " · Top Rated", "title": ""}.get(sort, "")
    if language:
        sort_label += f" · {language}"
    text = f"{type_label}{sort_label}  ·  Page <b>{page}/{total_pages}</b>  ·  {total:,} titles\n"

    kb_rows = []
    for c in items:
        icon = "🎬" if c["type"] == "movie" else "📺"
        yr = f" ({c['release_year']})" if c.get("release_year") else ""
        lbl = f"{icon} {c['title'][:24]}{yr}"
        kb_rows.append([InlineKeyboardButton(lbl, callback_data=f"anime:view:{c['id']}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"anime:pg:{page-1}:{ctype}:{sort}"))
    nav.append(InlineKeyboardButton(f"· {page}/{total_pages} ·", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"anime:pg:{page+1}:{ctype}:{sort}"))

    filter_row = [
        InlineKeyboardButton("📺 Anime", callback_data=f"anime:pg:1:series:{sort}"),
        InlineKeyboardButton("🎬 Movies", callback_data=f"anime:pg:1:movie:{sort}"),
        InlineKeyboardButton("🌐 All", callback_data=f"anime:pg:1:all:{sort}"),
    ]
    sort_row = [
        InlineKeyboardButton("🔤 A-Z", callback_data=f"anime:pg:1:{ctype}:title"),
        InlineKeyboardButton("🆕 Newest", callback_data=f"anime:pg:1:{ctype}:newest"),
        InlineKeyboardButton("⭐ Rating", callback_data=f"anime:pg:1:{ctype}:rating"),
    ]
    bottom = [
        InlineKeyboardButton("🔍 Search", callback_data="search:ask"),
        InlineKeyboardButton("🆕 New", callback_data="show:new"),
    ]

    kb = InlineKeyboardMarkup(kb_rows + [nav, filter_row, sort_row, bottom])
    if edit:
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb); return
        except Exception:
            pass
        try:
            await msg.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb); return
        except Exception:
            pass
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _run_search(msg, query: str, edit: bool = False):
    d = db()
    if not d:
        await msg.reply_text("⚠️ Database unavailable."); return
    results = _search(d, query)
    back = InlineKeyboardButton("◀️ Back", callback_data="anime:pg:1:all:title")
    if not results:
        text = f"🔍 No results for <b>{query}</b>"
        kb = InlineKeyboardMarkup([[back]])
        if edit:
            try:
                await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb); return
            except Exception:
                pass
            try:
                await msg.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb); return
            except Exception:
                pass
        await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    text = f"🔍 <b>{query}</b> — {len(results)} result(s)\n"
    rows = []
    for c in results:
        icon = "🎬" if c["type"] == "movie" else "📺"
        yr = f" ({c['release_year']})" if c.get("release_year") else ""
        rows.append([InlineKeyboardButton(f"{icon} {c['title'][:30]}{yr}",
                                          callback_data=f"anime:view:{c['id']}")])
    rows.append([back])
    kb = InlineKeyboardMarkup(rows)
    if edit:
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb); return
        except Exception:
            pass
        try:
            await msg.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb); return
        except Exception:
            pass
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ── Callback handler ───────────────────────────────────────────────────────────
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    uid  = q.from_user.id
    d    = db()

    await q.answer()

    if data == "noop":
        return

    # ── User requests access ──────────────────────────────────────────────────
    if data == "user:request":
        if not d:
            await _edit_msg(q, "⚠️ Database unavailable. Please try again later or contact admin.")
            return
        # Ensure bot_users table exists before trying to save request
        if not _bot_users_exist(d):
            await _edit_msg(
                q,
                "⚠️ <b>Setup Incomplete</b>\n\n"
                "The admin needs to finish setting up the database.\n"
                "Please contact the admin directly to get access.\n\n"
                "🔧 Admin: run <code>setup_db.sql</code> in Supabase SQL Editor."
            )
            for aid in ADMIN_IDS:
                try:
                    await ctx.bot.send_message(
                        chat_id=aid,
                        text=(
                            "⚠️ <b>Access Request Failed — DB Setup Incomplete!</b>\n\n"
                            f"User <b>{q.from_user.first_name}</b> (@{q.from_user.username or 'none'}) "
                            f"tried to request access but <code>bot_users</code> table is missing.\n\n"
                            "📋 Fix: run <code>setup_db.sql</code> in Supabase SQL Editor."
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
            return

        u = q.from_user
        _upsert_user(d, uid, u.username or "", u.first_name or "", requested=True)
        gif = _neko_gif(["wave", "happy", "smile", "nod", "thumbsup"])
        confirm_text = (
            f"🙋 <b>Request Sent!</b>\n\n"
            f"Hi <b>{u.first_name}</b>! Your access request has been submitted. ✨\n\n"
            "You'll get a notification once an admin reviews it.\n"
            "Usually approved within a few hours. 😊"
        )
        if gif:
            try:
                await ctx.bot.send_animation(chat_id=uid, animation=gif,
                                             caption=confirm_text, parse_mode=ParseMode.HTML)
            except Exception:
                await _edit_msg(q, confirm_text)
        else:
            await _edit_msg(q, confirm_text)
        for aid in ADMIN_IDS:
            try:
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"admin:ok:{uid}"),
                    InlineKeyboardButton("❌ Deny",    callback_data=f"admin:no:{uid}"),
                    InlineKeyboardButton("👤 Manage",  callback_data=f"admin:manage:{uid}"),
                ]])
                await ctx.bot.send_message(
                    chat_id=aid,
                    text=(f"🔔 <b>New Access Request!</b>\n\n"
                          f"👤 <b>{u.first_name}</b> (@{u.username or 'none'})\n"
                          f"🆔 <code>{uid}</code>\n\n"
                          f"Tap to approve or deny:"),
                    parse_mode=ParseMode.HTML, reply_markup=kb
                )
            except Exception:
                pass
        return

    # ── Admin: approve / deny via inline ─────────────────────────────────────
    if data.startswith("admin:ok:") or data.startswith("admin:no:"):
        if uid not in ADMIN_IDS:
            await q.answer("⛔ Admin only.", show_alert=True); return
        parts = data.split(":", 2)
        target = int(parts[2])
        if data.startswith("admin:ok:"):
            if d:
                d.table("bot_users").update({"allowed": True, "requested": False}).eq("telegram_id", str(target)).execute()
            await _edit_msg(q, f"✅ User <code>{target}</code> approved.")
            try:
                gif = _neko_gif(_GIF_APPROVE)
                msg_text = "🎉 <b>Access Granted!</b>\n\nWelcome to Senpai TV! 🎌\nUse /anime to start browsing."
                if gif:
                    await ctx.bot.send_animation(chat_id=target, animation=gif, caption=msg_text, parse_mode=ParseMode.HTML)
                else:
                    await ctx.bot.send_message(chat_id=target, text=msg_text, parse_mode=ParseMode.HTML)
            except Exception:
                pass
        else:
            if d:
                # On deny: clear request flag and revoke any watch access
                d.table("bot_users").update({"requested": False, "allowed": False, "can_watch": False}).eq("telegram_id", str(target)).execute()
            await _edit_msg(q, f"❌ Request from <code>{target}</code> denied.")
        return

    # ── Admin: block user ─────────────────────────────────────────────────────
    if data.startswith("admin:block:"):
        if uid not in ADMIN_IDS:
            await q.answer("⛔", show_alert=True); return
        target = int(data.split(":", 2)[2])
        if d:
            # Revoke both library and watch access on block
            d.table("bot_users").update({"allowed": False, "can_watch": False}).eq("telegram_id", str(target)).execute()
        await q.answer("🚫 User blocked.", show_alert=True)
        if d:
            u = _get_user(d, target)
            if u:
                await _send_user_management(q.message, u, edit=True)
        return

    # ── Admin: manage specific user ───────────────────────────────────────────
    if data.startswith("admin:manage:"):
        if uid not in ADMIN_IDS:
            await q.answer("⛔", show_alert=True); return
        target = int(data.split(":", 2)[2])
        u = _get_user(d, target) if d else None
        if not u:
            await q.answer("❌ User not found.", show_alert=True); return
        await _send_user_management(q.message, u, edit=True)
        return

    # ── Admin panel callbacks ─────────────────────────────────────────────────
    if data == "admin:panel":
        if uid not in ADMIN_IDS:
            await q.answer("⛔", show_alert=True); return
        await _send_admin_panel(q.message, edit=True)
        return

    if data == "admin:requests":
        if uid not in ADMIN_IDS:
            await q.answer("⛔", show_alert=True); return
        pending = _pending_users(d) if d else []
        if not pending:
            await _edit_msg(q, "✅ No pending requests.",
                InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="admin:panel")]]))
            return
        rows = []
        for u in pending:
            name = u.get("first_name") or u.get("username") or "?"
            rows.append([
                InlineKeyboardButton(f"✅ {name}", callback_data=f"admin:ok:{u['telegram_id']}"),
                InlineKeyboardButton("❌ Deny",    callback_data=f"admin:no:{u['telegram_id']}"),
                InlineKeyboardButton("👤 View",    callback_data=f"admin:manage:{u['telegram_id']}"),
            ])
        rows.append([InlineKeyboardButton("◀️ Back", callback_data="admin:panel")])
        text = f"🔔 <b>Pending Requests ({len(pending)})</b>\n\n"
        for u in pending:
            text += f"• <b>{u.get('first_name','?')}</b> @{u.get('username','none')} — <code>{u['telegram_id']}</code>\n"
        await _edit_msg(q, text, InlineKeyboardMarkup(rows))
        return

    if data.startswith("admin:users:"):
        if uid not in ADMIN_IDS:
            await q.answer("⛔", show_alert=True); return
        page = int(data.split(":")[-1])
        users = _all_users(d) if d else []
        chunk = users[page*15:(page+1)*15]
        lines = [f"👥 <b>Users ({len(users)})</b> — page {page+1}\n"]
        rows = []
        for u in chunk:
            s = "✅" if u.get("allowed") else ("⏳" if u.get("requested") else "❌")
            w = "🎬" if u.get("can_watch") else "🔒"
            name = (u.get("first_name") or u.get("username") or "?")[:12]
            lines.append(f"{s}{w} <code>{u['telegram_id']}</code> {name}")
            rows.append([InlineKeyboardButton(
                f"{s}{w} {name}",
                callback_data=f"admin:manage:{u['telegram_id']}"
            )])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"admin:users:{page-1}"))
        if (page+1)*15 < len(users):
            nav.append(InlineKeyboardButton("▶️", callback_data=f"admin:users:{page+1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("◀️ Back", callback_data="admin:panel")])
        await _edit_msg(q, "\n".join(lines), InlineKeyboardMarkup(rows))
        return

    # ── Grant/revoke watch via inline ─────────────────────────────────────────
    if data.startswith("admin:grant:") or data.startswith("admin:revoke:"):
        if uid not in ADMIN_IDS:
            await q.answer("⛔", show_alert=True); return
        parts = data.split(":")
        action, target = parts[1], int(parts[2])
        val = action == "grant"
        if d:
            d.table("bot_users").update({"can_watch": val}).eq("telegram_id", str(target)).execute()
        label = "🎬 Watch granted!" if val else "🔒 Watch revoked."
        await q.answer(label, show_alert=True)
        if val:
            try:
                gif = _neko_gif(_GIF_APPROVE)
                msg_text = "🎬 <b>Watch Access Granted!</b>\nYou can now view episodes and stream links.\nUse /anime 🎌"
                if gif:
                    await ctx.bot.send_animation(chat_id=target, animation=gif, caption=msg_text, parse_mode=ParseMode.HTML)
                else:
                    await ctx.bot.send_message(chat_id=target, text=msg_text, parse_mode=ParseMode.HTML)
            except Exception:
                pass
        # Refresh user management panel
        if d:
            u = _get_user(d, target)
            if u:
                await _send_user_management(q.message, u, edit=True)
        return

    # ── Broadcast ask ─────────────────────────────────────────────────────────
    if data == "admin:broadcast:ask":
        if uid not in ADMIN_IDS:
            await q.answer("⛔", show_alert=True); return
        ctx.user_data["broadcasting"] = True
        await _edit_msg(q,
            "📣 <b>Broadcast Message</b>\n\nType your message to send to all approved users:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("✖️ Cancel", callback_data="admin:panel")
            ]])
        )
        return

    # ── Stats ─────────────────────────────────────────────────────────────────
    if data == "show:stats":
        s = _db_stats(d) if d else {}
        text = (
            "📊 <b>Senpai TV — Library Stats</b>\n\n"
            f"📺 Anime Series: <b>{s.get('anime', 0):,}</b>\n"
            f"🎬 Movies:       <b>{s.get('movies', 0):,}</b>\n"
            f"📋 Total Titles: <b>{s.get('titles', 0):,}</b>\n"
            f"🎞️ Episodes:     <b>{s.get('eps', 0):,}</b>\n"
            f"🖥️ Servers:      <b>{s.get('servers', 0):,}</b>\n"
            f"👥 Users:        <b>{s.get('users', 0):,}</b>"
        )
        back = "admin:panel" if uid in ADMIN_IDS else "anime:pg:1:all:title"
        await _edit_msg(q, text,
            InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data=back)]]))
        return

    # ── Scraper status ────────────────────────────────────────────────────────
    if data == "show:scraper":
        if uid not in ADMIN_IDS:
            await q.answer("⛔ Admin only.", show_alert=True); return
        state = _get_scraper_status()
        if not state:
            await q.answer("⚠️ Scraper dashboard unreachable.", show_alert=True); return
        text = _fmt_scraper_status(state)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="show:scraper"),
            InlineKeyboardButton("◀️ Admin", callback_data="admin:panel"),
        ]])
        await _edit_msg(q, text, kb)
        return

    # ── Recently added ────────────────────────────────────────────────────────
    if data == "show:new":
        u_obj = _get_user(d, uid) if d else None
        if uid not in ADMIN_IDS and (not u_obj or not u_obj.get("allowed")):
            await q.answer("🔒 Access required.", show_alert=True); return
        await _send_recently_added(q.message, edit=True)
        return

    # ── Search ask ────────────────────────────────────────────────────────────
    if data == "search:ask":
        u_obj = _get_user(d, uid) if d else None
        if uid not in ADMIN_IDS and (not u_obj or not u_obj.get("allowed")):
            await q.answer("🔒 Access required.", show_alert=True); return
        ctx.user_data["searching"] = True
        await _edit_msg(q,
            "🔍 <b>Search Anime</b>\n\nType the title you're looking for:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("✖️ Cancel", callback_data="anime:pg:1:all:title")
            ]])
        )
        return

    # ── Anime list page ───────────────────────────────────────────────────────
    if data.startswith("anime:pg:"):
        u_obj = _get_user(d, uid) if d else None
        if uid not in ADMIN_IDS and (not u_obj or not u_obj.get("allowed")):
            await q.answer("🔒 Access required.", show_alert=True); return
        parts = data.split(":", 4)
        # anime:pg:<page>:<ctype>:<sort>
        pg = int(parts[2])
        ct = parts[3] if len(parts) > 3 else "all"
        sort = parts[4] if len(parts) > 4 else "title"
        await _send_anime_list(q.message, page=pg, ctype=ct, sort=sort, edit=True)
        return

    # ── Anime detail ──────────────────────────────────────────────────────────
    if data.startswith("anime:view:"):
        u_obj = _get_user(d, uid) if d else None
        if uid not in ADMIN_IDS and (not u_obj or not u_obj.get("allowed")):
            await q.answer("🔒 Access required.", show_alert=True); return
        cid = data[len("anime:view:"):]
        c = _content_detail(d, cid) if d else None
        if not c:
            await q.answer("❌ Not found.", show_alert=True); return
        genres = _genres(d, cid)
        text = _card(c, genres)
        cw = _can_watch(uid, u_obj)
        rows = []
        if c.get("type") == "series":
            if cw:
                rows.append([InlineKeyboardButton("🎞️ View Episodes", callback_data=f"anime:eps:{cid}:0")])
            else:
                rows.append([InlineKeyboardButton("🔒 Episodes (need watch access)", callback_data="noop")])
        else:
            if cw:
                rows.append([InlineKeyboardButton("▶️ Watch Movie", callback_data=f"anime:watch:{cid}")])
            else:
                rows.append([InlineKeyboardButton("🔒 Watch (need watch access)", callback_data="noop")])
        rows.append([InlineKeyboardButton("◀️ Back", callback_data="anime:pg:1:all:title")])
        kb = InlineKeyboardMarkup(rows)
        poster = c.get("poster_url") or c.get("thumbnail_url") or ""
        if poster and poster.startswith("http"):
            try:
                await q.edit_message_media(
                    media=InputMediaPhoto(media=poster, caption=text, parse_mode=ParseMode.HTML),
                    reply_markup=kb
                )
                return
            except Exception:
                pass
        await _edit_msg(q, text, kb)
        return

    # ── Episodes list ─────────────────────────────────────────────────────────
    if data.startswith("anime:eps:"):
        u_obj = _get_user(d, uid) if d else None
        if uid not in ADMIN_IDS and (not u_obj or not u_obj.get("allowed")):
            await q.answer("🔒 Library access required.", show_alert=True); return
        if not _can_watch(uid, u_obj):
            await q.answer("🔒 Watch access required. Ask admin to grant it.", show_alert=True); return
        parts = data.split(":")  # anime:eps:<cid>:<pg>
        cid, ep_page = parts[2], int(parts[3])
        c = _content_detail(d, cid) if d else None
        eps = _episodes(d, cid) if d else []
        if not eps:
            await q.answer("No episodes found.", show_alert=True); return

        seasons: dict[int, list] = {}
        for ep in eps:
            seasons.setdefault(ep["season_number"], []).append(ep)

        season_nums = sorted(seasons.keys())
        sn = season_nums[ep_page] if ep_page < len(season_nums) else season_nums[0]
        season_eps = seasons[sn]

        text = (f"🎞️ <b>{c['title'] if c else 'Episodes'}</b>\n"
                f"📺 <b>Season {sn}</b> — {len(season_eps)} episodes\n\n"
                f"Season {ep_page+1} of {len(season_nums)}")
        rows = []
        row = []
        for ep in season_eps:
            lbl = f"E{ep['episode_number']}"
            row.append(InlineKeyboardButton(lbl, callback_data=f"anime:ep:{ep['id']}:{cid}"))
            if len(row) == 5:
                rows.append(row); row = []
        if row:
            rows.append(row)

        nav = []
        if ep_page > 0:
            nav.append(InlineKeyboardButton("◀️ Season", callback_data=f"anime:eps:{cid}:{ep_page-1}"))
        if ep_page < len(season_nums) - 1:
            nav.append(InlineKeyboardButton("Season ▶️", callback_data=f"anime:eps:{cid}:{ep_page+1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("◀️ Back", callback_data=f"anime:view:{cid}")])
        await _edit_msg(q, text, InlineKeyboardMarkup(rows))
        return

    # ── Episode servers / video delivery ─────────────────────────────────────
    if data.startswith("anime:ep:"):
        u_obj = _get_user(d, uid) if d else None
        if uid not in ADMIN_IDS and (not u_obj or not u_obj.get("allowed")):
            await q.answer("🔒 Library access required.", show_alert=True); return
        if not _can_watch(uid, u_obj):
            await q.answer("🔒 Watch access required. Ask admin to grant it.", show_alert=True); return
        parts = data.split(":")  # anime:ep:<ep_id>:<cid>
        ep_id, cid = parts[2], parts[3]
        ep = _ep_detail(d, ep_id) if d else None
        srvs = _servers(d, ep_id) if d else []
        c = _content_detail(d, cid) if d else None
        show = c["title"] if c else ""
        ep_lbl = f"S{ep['season_number']}E{ep['episode_number']}" if ep else "Episode"
        ep_title = ep.get("title") or ep_lbl if ep else ep_lbl
        duration_sec = int(ep.get("duration_seconds") or 0) if ep else 0
        thumbnail = (c or {}).get("thumbnail_url") or (c or {}).get("poster_url") or None

        ep_page = 0
        if ep and d:
            all_eps = _episodes(d, cid)
            seasons = sorted(set(e["season_number"] for e in all_eps))
            if ep["season_number"] in seasons:
                ep_page = seasons.index(ep["season_number"])

        back_row = [InlineKeyboardButton("◀️ Back to Episodes", callback_data=f"anime:eps:{cid}:{ep_page}")]

        if srvs:
            # ── Try direct video delivery first (for .mp4/.webm direct URLs) ──
            caption = (
                f"▶️ <b>{show}</b>  |  <b>{ep_lbl}</b>\n"
                f"📋 {ep_title}"
                + (f"\n⏱ {duration_sec // 60}m {duration_sec % 60}s" if duration_sec else "")
                + "\n\n🔒 <i>Forwarding & saving disabled</i>"
                + ("\n⏰ <i>This video will auto-delete after playback</i>" if duration_sec else "")
            )
            delivered = False
            for srv in srvs:
                url = srv.get("stream_url", "")
                if url:
                    delivered = await _try_send_video_file(
                        ctx, uid, url, caption,
                        duration_sec=duration_sec, thumbnail_url=thumbnail
                    )
                    if delivered:
                        # Confirm delivery with a minimal edit
                        try:
                            await _edit_msg(
                                q,
                                f"▶️ <b>{show}</b> — <b>{ep_lbl}</b>\n\n"
                                f"✅ Video sent! It will auto-delete after playback.\n"
                                f"🔒 Forwarding is disabled.",
                                InlineKeyboardMarkup([back_row])
                            )
                        except Exception:
                            pass
                        break

            if not delivered:
                # Fall back to stream buttons
                text = f"▶️ <b>{show}</b>\n<b>{ep_lbl}</b>: {ep_title}\n\n"
                text += f"🖥️ <b>{len(srvs)} stream server(s):</b>"
                rows = []
                for srv in srvs:
                    lbl = f"▶️ {srv.get('server_name','Server')} · {srv.get('quality','')} {srv.get('language','')}".strip()
                    url = srv.get("stream_url", "")
                    if url:
                        rows.append([InlineKeyboardButton(lbl, url=url)])
                rows.append(back_row)
                await _edit_msg(q, text, InlineKeyboardMarkup(rows))
        else:
            text = (
                f"▶️ <b>{show}</b>\n<b>{ep_lbl}</b>: {ep_title}\n\n"
                "⚠️ <b>No stream servers stored for this episode.</b>\n\n"
                "This usually means:\n"
                "• The episode page had no embeds when last scraped\n"
                "• The source site uses dynamic loading\n\n"
                "🔗 You can watch directly on AnimeSalt:"
            )
            rows = []
            if show:
                slug = re.sub(r"[^\w\s-]", "", show.lower()).strip()
                slug = re.sub(r"[\s_]+", "-", slug).strip("-")
                ep_num = ep.get("episode_number", 1) if ep else 1
                rows.append([InlineKeyboardButton(
                    "🌐 Watch on AnimeSalt",
                    url=f"https://animesalt.ac/episode/{slug}-episode-{ep_num}/"
                )])
            rows.append(back_row)
            await _edit_msg(q, text, InlineKeyboardMarkup(rows))
        return

    # ── Movie watch ───────────────────────────────────────────────────────────
    if data.startswith("anime:watch:"):
        u_obj = _get_user(d, uid) if d else None
        if uid not in ADMIN_IDS and (not u_obj or not u_obj.get("allowed")):
            await q.answer("🔒 Library access required.", show_alert=True); return
        if not _can_watch(uid, u_obj):
            await q.answer("🔒 Watch access required. Ask admin to grant it.", show_alert=True); return
        cid = data[len("anime:watch:"):]
        c = _content_detail(d, cid) if d else None
        eps = _episodes(d, cid) if d else []
        rows = []
        movie_title = c["title"] if c else "Movie"
        thumbnail = (c or {}).get("thumbnail_url") or (c or {}).get("poster_url") or None
        back_row = [InlineKeyboardButton("◀️ Back", callback_data=f"anime:view:{cid}")]

        if eps:
            ep = eps[0]
            srvs = _servers(d, ep["id"]) if d else []
            duration_sec = int(ep.get("duration_seconds") or 0)

            if srvs:
                # Try direct video delivery first
                caption = (
                    f"🎬 <b>{movie_title}</b>\n\n"
                    + (f"⏱ {duration_sec // 60}m {duration_sec % 60}s\n" if duration_sec else "")
                    + "🔒 <i>Forwarding & saving disabled</i>"
                    + ("\n⏰ <i>Auto-deletes after playback</i>" if duration_sec else "")
                )
                delivered = False
                for srv in srvs:
                    url = srv.get("stream_url", "")
                    if url:
                        delivered = await _try_send_video_file(
                            ctx, uid, url, caption,
                            duration_sec=duration_sec, thumbnail_url=thumbnail
                        )
                        if delivered:
                            try:
                                await _edit_msg(
                                    q,
                                    f"🎬 <b>{movie_title}</b>\n\n✅ Movie sent! "
                                    "It will auto-delete after playback.\n🔒 Forwarding is disabled.",
                                    InlineKeyboardMarkup([back_row])
                                )
                            except Exception:
                                pass
                            break

                if not delivered:
                    text = f"🎬 <b>{movie_title}</b>\n\n🖥️ <b>{len(srvs)} stream server(s):</b>"
                    for srv in srvs:
                        lbl = f"▶️ {srv.get('server_name','Server')} · {srv.get('quality','')} {srv.get('language','')}".strip()
                        if srv.get("stream_url"):
                            rows.append([InlineKeyboardButton(lbl, url=srv["stream_url"])])
                    rows.append(back_row)
                    await _edit_msg(q, text, InlineKeyboardMarkup(rows))
            else:
                text = f"🎬 <b>{movie_title}</b>\n\n⚠️ <b>No stream servers stored.</b>\n\n🔗 Watch directly on AnimeSalt:"
                slug = re.sub(r"[^\w\s-]", "", movie_title.lower()).strip()
                slug = re.sub(r"[\s_]+", "-", slug).strip("-")
                rows.append([InlineKeyboardButton("🌐 Watch on AnimeSalt", url=f"https://animesalt.ac/movies/{slug}/")])
                rows.append(back_row)
                await _edit_msg(q, text, InlineKeyboardMarkup(rows))
        else:
            slug = re.sub(r"[^\w\s-]", "", movie_title.lower()).strip()
            slug = re.sub(r"[\s_]+", "-", slug).strip("-")
            rows.append([InlineKeyboardButton("🌐 Watch on AnimeSalt", url=f"https://animesalt.ac/movies/{slug}/")])
            rows.append(back_row)
            await _edit_msg(q, f"⚠️ No stream available for <b>{movie_title}</b>.", InlineKeyboardMarkup(rows))
        return

    log.debug(f"Unhandled callback: {data}")


# ── Text message handler ───────────────────────────────────────────────────────
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # Handle broadcast input from admin
    if uid in ADMIN_IDS and ctx.user_data.pop("broadcasting", False):
        msg_text = update.message.text.strip()
        d = db()
        if not d:
            await update.message.reply_text("⚠️ Database unavailable.")
            return
        users = _allowed_users(d)
        sent = 0
        failed = 0
        broadcast_text = f"📣 <b>Senpai TV Announcement</b>\n\n{msg_text}"
        status_msg = await update.message.reply_text(f"📣 Sending to {len(users)} users…")
        for u in users:
            try:
                tg_id = int(u["telegram_id"])
                if tg_id in ADMIN_IDS:
                    continue
                gif = _neko_gif(_GIF_HAPPY)
                if gif:
                    await ctx.bot.send_animation(
                        chat_id=tg_id, animation=gif,
                        caption=broadcast_text, parse_mode=ParseMode.HTML
                    )
                else:
                    await ctx.bot.send_message(
                        chat_id=tg_id, text=broadcast_text, parse_mode=ParseMode.HTML
                    )
                sent += 1
                await asyncio.sleep(0.05)
            except (Forbidden, BadRequest):
                failed += 1
            except Exception as e:
                log.warning(f"Broadcast: {e}")
                failed += 1
        try:
            await status_msg.edit_text(
                f"📣 Broadcast complete!\n✅ Sent: <b>{sent}</b>  ❌ Failed: <b>{failed}</b>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        return

    if ctx.user_data.pop("searching", False):
        await _run_search(update.message, update.message.text.strip(), edit=False)
        return

    await update.message.reply_text(
        "💡 Use /anime to browse, /search &lt;title&gt; to find anime, or /help for commands.",
        parse_mode=ParseMode.HTML
    )


# ── Bot startup ────────────────────────────────────────────────────────────────
async def _error_handler(update, context) -> None:
    """Suppress startup Conflict noise; log everything else."""
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        log.debug("[bot] startup Conflict suppressed — retrying poll automatically")
        return
    log.error("[bot] unhandled error: %s", context.error, exc_info=context.error)


def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(_error_handler)
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(CommandHandler("anime",      cmd_anime))
    app.add_handler(CommandHandler("new",        cmd_new))
    app.add_handler(CommandHandler("recently",   cmd_new))
    app.add_handler(CommandHandler("search",     cmd_search))
    app.add_handler(CommandHandler("profile",    cmd_profile))
    app.add_handler(CommandHandler("admin",      cmd_admin))
    app.add_handler(CommandHandler("users",      cmd_users))
    app.add_handler(CommandHandler("allow",      cmd_allow))
    app.add_handler(CommandHandler("block",      cmd_block))
    app.add_handler(CommandHandler("grant",      cmd_grant))
    app.add_handler(CommandHandler("revoke",     cmd_revoke))
    app.add_handler(CommandHandler("scraper",    cmd_scraper))
    app.add_handler(CommandHandler("broadcast",  cmd_broadcast))
    app.add_handler(CommandHandler("manage",     cmd_manage))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def _send_admin_startup_alert(bot_app, missing_tables: list[str]):
    """Send a one-time setup alert to all admins if required tables are missing."""
    if not missing_tables or not ADMIN_IDS:
        return
    msg = (
        "⚠️ <b>Senpai TV — Setup Required</b>\n\n"
        f"Missing database table(s): <code>{', '.join(missing_tables)}</code>\n\n"
        "📋 <b>Fix:</b>\n"
        "1. Go to <a href='https://supabase.com/dashboard'>Supabase Dashboard</a>\n"
        "2. Open your project → <b>SQL Editor</b>\n"
        "3. Copy and run the contents of <code>setup_db.sql</code>\n\n"
        "The bot will work fully once those tables exist."
    )
    import asyncio

    async def _alert():
        for aid in ADMIN_IDS:
            try:
                await bot_app.bot.send_message(chat_id=aid, text=msg,
                                               parse_mode="HTML",
                                               disable_web_page_preview=True)
            except Exception:
                pass

    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(bot_app.initialize())
        loop.run_until_complete(_alert())
        loop.run_until_complete(bot_app.shutdown())
        loop.close()
    except Exception:
        pass


def run_bot():
    """Run the bot using Application.run_polling() which owns its own event loop.
    stop_signals=() prevents signal-handler registration (not allowed in non-main threads).
    run_polling() internally retries on Conflict and other transient errors."""
    if not BOT_TOKEN:
        log.error("[bot] TELEGRAM_BOT_TOKEN not set — bot disabled.")
        return
    app = build_app()

    # Check for missing tables and alert admins once on startup
    if SB_URL and SB_KEY:
        d = None
        try:
            d = create_client(SB_URL, SB_KEY)
        except Exception:
            pass
        if d:
            missing: list[str] = []
            for tbl in ["content", "episodes", "video_servers", "bot_users"]:
                try:
                    d.table(tbl).select("*").limit(1).execute()
                except Exception as e:
                    if "PGRST205" in str(e):
                        missing.append(tbl)
            if missing:
                log.warning(f"[bot] Missing tables: {missing} — sending admin alert")
                _send_admin_startup_alert(app, missing)

    try:
        app.run_polling(
            drop_pending_updates=True,
            stop_signals=(),         # safe for non-main threads
            close_loop=True,
        )
    except Exception as e:
        log.error(f"[bot] crashed: {e}", exc_info=True)


def start_bot():
    """Launch bot in a background daemon thread."""
    t = threading.Thread(target=run_bot, name="tg-bot", daemon=True)
    t.start()
    return t
