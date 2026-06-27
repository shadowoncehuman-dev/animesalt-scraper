#!/usr/bin/env python3
"""
Senpai TV — Telegram Bot
Professional anime database bot with admin user management,
anime browsing, search, episode access, and anime images.
"""

import os, asyncio, logging, math, random, threading
import requests as _http
from functools import wraps
from typing import Optional

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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

# Admin IDs: set ADMIN_USER_IDS env var as comma-separated Telegram user IDs
# Falls back to TELEGRAM_CHAT_ID if nothing set
_raw_admins = os.environ.get("ADMIN_USER_IDS", "") or os.environ.get("TELEGRAM_CHAT_ID", "")
ADMIN_IDS: set[int] = set(
    int(x.strip()) for x in _raw_admins.replace(",", " ").split() if x.strip().lstrip("-").isdigit()
)

NEKOS_UA   = "SenpaiTV-AnimeBot (https://github.com/shadowoncehuman-dev/animesalt-scraper)"

# ── Image APIs ─────────────────────────────────────────────────────────────────
_GIF_CATS = ["wave", "wink", "happy", "smile", "thumbsup", "nod", "clap", "dance", "nod", "happy"]
_IMG_CATS = ["waifu", "neko", "kitsune"]

def _neko_gif() -> Optional[str]:
    cat = random.choice(_GIF_CATS)
    try:
        r = _http.get(f"https://nekos.best/api/v2/{cat}",
                      headers={"User-Agent": NEKOS_UA}, timeout=6)
        if r.ok:
            results = r.json().get("results", [])
            if results:
                return results[0]["url"]
    except Exception:
        pass
    return None

def _waifu_img() -> Optional[str]:
    try:
        r = _http.get("https://api.waifu.im/search",
                      params={"IncludedTags": "waifu", "IsNsfw": "False"},
                      headers={"User-Agent": NEKOS_UA}, timeout=6)
        if r.ok:
            items = r.json().get("items", [])
            if items:
                return items[0]["url"]
    except Exception:
        pass
    # fallback to nekos.best image
    try:
        cat = random.choice(_IMG_CATS)
        r = _http.get(f"https://nekos.best/api/v2/{cat}",
                      headers={"User-Agent": NEKOS_UA}, timeout=6)
        if r.ok:
            results = r.json().get("results", [])
            if results:
                return results[0]["url"]
    except Exception:
        pass
    return None

# ── Database helpers ───────────────────────────────────────────────────────────
def db() -> Optional[SBClient]:
    if SB_URL and SB_KEY:
        return create_client(SB_URL, SB_KEY)
    return None

def _get_user(d: SBClient, tg_id: int) -> Optional[dict]:
    try:
        r = d.table("bot_users").select("*").eq("telegram_id", str(tg_id)).execute()
        return r.data[0] if r.data else None
    except Exception:
        return None

def _upsert_user(d: SBClient, tg_id: int, username: str, first_name: str, **kw):
    try:
        existing = _get_user(d, tg_id)
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

def _pending_users(d: SBClient) -> list:
    try:
        return d.table("bot_users").select("*").eq("requested", True).eq("allowed", False).execute().data or []
    except Exception:
        return []

def _db_stats(d: SBClient) -> dict:
    try:
        titles  = d.table("content").select("id", count="exact", head=True).execute().count or 0
        eps     = d.table("episodes").select("id", count="exact", head=True).execute().count or 0
        srvs    = d.table("video_servers").select("id", count="exact", head=True).execute().count or 0
        anime   = d.table("content").select("id", count="exact", head=True).eq("type", "series").execute().count or 0
        movies  = d.table("content").select("id", count="exact", head=True).eq("type", "movie").execute().count or 0
        return {"titles": titles, "eps": eps, "servers": srvs, "anime": anime, "movies": movies}
    except Exception:
        return {}

def _search(d: SBClient, q: str, limit: int = 15) -> list:
    try:
        return d.table("content").select(
            "id,title,type,release_year,rating,poster_url,language,status"
        ).ilike("title", f"%{q}%").limit(limit).execute().data or []
    except Exception:
        return []

def _content_page(d: SBClient, page: int, size: int = 8, ctype: str = None):
    try:
        offset = (page - 1) * size
        q = d.table("content").select("id,title,type,release_year,rating,poster_url,language,status")
        tq = d.table("content").select("id", count="exact", head=True)
        if ctype:
            q, tq = q.eq("type", ctype), tq.eq("type", ctype)
        total = tq.execute().count or 0
        items = q.order("title").range(offset, offset + size - 1).execute().data or []
        return items, total
    except Exception:
        return [], 0

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

def watch_only(fn):
    @wraps(fn)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *a, **kw):
        uid = update.effective_user.id
        if uid in ADMIN_IDS:
            return await fn(update, ctx, *a, **kw)
        d = db()
        u = _get_user(d, uid) if d else None
        if not u or not u.get("can_watch"):
            await update.effective_message.reply_text(
                "🎬 Watch access required.\nAsk an admin to grant you episode access."
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

    gif = _neko_gif()

    if uid in ADMIN_IDS:
        text = (
            f"👋 Welcome back, <b>{user.first_name}</b>!\n\n"
            "🛡️ <b>Admin Panel is ready.</b>\n\n"
            "Manage users, browse the anime library, and monitor scraper status."
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛡️ Admin Panel", callback_data="admin:panel"),
            InlineKeyboardButton("🎌 Browse", callback_data="anime:pg:1:all"),
        ], [
            InlineKeyboardButton("📊 Stats", callback_data="show:stats"),
            InlineKeyboardButton("🔍 Search", callback_data="search:ask"),
        ]])
    elif existing and existing.get("allowed"):
        text = (
            f"🎌 Welcome back, <b>{user.first_name}</b>!\n\n"
            "✅ <b>You have library access.</b>\n\n"
            "Browse thousands of anime, cartoons & movies."
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎌 Browse Anime", callback_data="anime:pg:1:all"),
            InlineKeyboardButton("🔍 Search", callback_data="search:ask"),
        ], [
            InlineKeyboardButton("📊 Stats", callback_data="show:stats"),
        ]])
    else:
        requested = existing and existing.get("requested")
        if requested:
            text = (
                f"⏳ Hey <b>{user.first_name}</b>!\n\n"
                "Your access request is <b>pending admin approval</b>.\n\n"
                "You'll get a notification when approved. Please wait! ✨"
            )
            kb = None
        else:
            text = (
                f"🎌 <b>Welcome to Senpai TV!</b>\n\n"
                f"Hello, <b>{user.first_name}</b>! 👋\n\n"
                "A private anime & cartoon database with thousands of titles.\n\n"
                "Tap below to request library access from an admin."
            )
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🙋 Request Access", callback_data="user:request"),
            ]])

    try:
        if gif:
            await update.message.reply_animation(animation=gif, caption=text,
                                                 parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            img = _waifu_img()
            if img:
                await update.message.reply_photo(photo=img, caption=text,
                                                 parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ── /help ──────────────────────────────────────────────────────────────────────
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in ADMIN_IDS:
        text = (
            "🛡️ <b>Admin Commands</b>\n"
            "/admin — Admin panel\n"
            "/users — List all users\n"
            "/allow &lt;id&gt; — Approve user\n"
            "/block &lt;id&gt; — Block user\n"
            "/grant &lt;id&gt; — Grant episode access\n"
            "/revoke &lt;id&gt; — Revoke episode access\n\n"
            "📺 <b>User Commands</b>\n"
            "/anime — Browse anime library\n"
            "/search &lt;title&gt; — Search anime\n"
            "/stats — Library stats\n"
            "/help — This help"
        )
    else:
        text = (
            "🎌 <b>Senpai TV Commands</b>\n\n"
            "/start — Welcome screen\n"
            "/anime — Browse the library\n"
            "/search &lt;title&gt; — Search for anime\n"
            "/stats — View library statistics\n"
            "/help — Show this help"
        )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /stats ─────────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = db()
    s = _db_stats(d) if d else {}
    img = _waifu_img()
    text = (
        "📊 <b>Senpai TV — Library Stats</b>\n\n"
        f"📺 Anime Series: <b>{s.get('anime', 0):,}</b>\n"
        f"🎬 Movies:       <b>{s.get('movies', 0):,}</b>\n"
        f"📋 Total Titles: <b>{s.get('titles', 0):,}</b>\n"
        f"🎞️ Episodes:     <b>{s.get('eps', 0):,}</b>\n"
        f"🖥️ Servers:      <b>{s.get('servers', 0):,}</b>"
    )
    try:
        if img:
            await update.effective_message.reply_photo(photo=img, caption=text,
                                                       parse_mode=ParseMode.HTML)
            return
    except Exception:
        pass
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /anime ─────────────────────────────────────────────────────────────────────
@approved_only
async def cmd_anime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_anime_list(update.message, page=1, ctype="all", edit=False)


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
            gif = _neko_gif()
            if gif:
                await ctx.bot.send_animation(chat_id=target, animation=gif,
                                             caption=user_msg, parse_mode=ParseMode.HTML)
            else:
                await ctx.bot.send_message(chat_id=target, text=user_msg, parse_mode=ParseMode.HTML)
        except (Forbidden, BadRequest) as e:
            log.warning(f"Could not notify user {target}: {e}")


# ── Shared view builders ───────────────────────────────────────────────────────
async def _send_admin_panel(msg, edit: bool = True):
    d = db()
    s = _db_stats(d) if d else {}
    pending = _pending_users(d) if d else []
    users = _all_users(d) if d else []
    approved = sum(1 for u in users if u.get("allowed"))
    text = (
        "🛡️ <b>Admin Panel — Senpai TV</b>\n\n"
        f"👥 Users: <b>{len(users)}</b>  |  Approved: <b>{approved}</b>  |  "
        f"🔔 Pending: <b>{len(pending)}</b>\n\n"
        f"📊 <b>{s.get('titles', 0):,}</b> titles · "
        f"<b>{s.get('eps', 0):,}</b> episodes · "
        f"<b>{s.get('servers', 0):,}</b> servers"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔔 Requests ({len(pending)})", callback_data="admin:requests"),
         InlineKeyboardButton("👥 All Users", callback_data="admin:users:0")],
        [InlineKeyboardButton("📊 DB Stats", callback_data="show:stats"),
         InlineKeyboardButton("🎌 Browse", callback_data="anime:pg:1:all")],
    ])
    if edit:
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except BadRequest:
            pass
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _send_anime_list(msg, page: int, ctype: str, edit: bool = True):
    d = db()
    if not d:
        await msg.reply_text("⚠️ Database unavailable."); return
    ct = None if ctype == "all" else ctype
    items, total = _content_page(d, page, size=8, ctype=ct)
    total_pages = max(1, math.ceil(total / 8))
    type_label = {"series": "📺 Anime/Series", "movie": "🎬 Movies"}.get(ctype, "🎌 All Titles")
    text = f"{type_label}  ·  Page <b>{page}/{total_pages}</b>  ·  {total:,} titles\n"

    kb_rows = []
    for c in items:
        icon = "🎬" if c["type"] == "movie" else "📺"
        yr = f" ({c['release_year']})" if c.get("release_year") else ""
        lbl = f"{icon} {c['title'][:24]}{yr}"
        kb_rows.append([InlineKeyboardButton(lbl, callback_data=f"anime:view:{c['id']}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"anime:pg:{page-1}:{ctype}"))
    nav.append(InlineKeyboardButton(f"· {page}/{total_pages} ·", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"anime:pg:{page+1}:{ctype}"))

    filter_row = [
        InlineKeyboardButton("📺 Anime", callback_data="anime:pg:1:series"),
        InlineKeyboardButton("🎬 Movies", callback_data="anime:pg:1:movie"),
        InlineKeyboardButton("🌐 All", callback_data="anime:pg:1:all"),
    ]
    bottom = [InlineKeyboardButton("🔍 Search", callback_data="search:ask"),
              InlineKeyboardButton("📊 Stats", callback_data="show:stats")]

    kb = InlineKeyboardMarkup(kb_rows + [nav, filter_row, bottom])
    if edit:
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb); return
        except BadRequest:
            pass
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _run_search(msg, query: str, edit: bool = False):
    d = db()
    if not d:
        await msg.reply_text("⚠️ Database unavailable."); return
    results = _search(d, query)
    back = InlineKeyboardButton("◀️ Back", callback_data="anime:pg:1:all")
    if not results:
        text = f"🔍 No results for <b>{query}</b>"
        kb = InlineKeyboardMarkup([[back]])
        if edit:
            try:
                await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb); return
            except BadRequest:
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
        except BadRequest:
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
            await q.edit_message_text("⚠️ System unavailable, try later.")
            return
        u = q.from_user
        _upsert_user(d, uid, u.username or "", u.first_name or "", requested=True)
        await q.edit_message_text(
            f"🙋 <b>Request Sent!</b>\n\n"
            f"Hi <b>{u.first_name}</b>, your access request has been submitted!\n\n"
            "You'll receive a notification once an admin reviews it. ✨",
            parse_mode=ParseMode.HTML
        )
        for aid in ADMIN_IDS:
            try:
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"admin:ok:{uid}"),
                    InlineKeyboardButton("❌ Deny",    callback_data=f"admin:no:{uid}"),
                ]])
                await ctx.bot.send_message(
                    chat_id=aid,
                    text=(f"🔔 <b>New Access Request</b>\n\n"
                          f"👤 <b>{u.first_name}</b> (@{u.username or 'none'})\n"
                          f"🆔 <code>{uid}</code>"),
                    parse_mode=ParseMode.HTML, reply_markup=kb
                )
            except Exception:
                pass
        return

    # ── Admin: approve / deny via inline ─────────────────────────────────────
    if data.startswith("admin:ok:") or data.startswith("admin:no:"):
        if uid not in ADMIN_IDS:
            await q.answer("⛔ Admin only.", show_alert=True); return
        action, _, target_str = data.split(":", 2)
        target = int(target_str)
        if data.startswith("admin:ok:"):
            if d:
                d.table("bot_users").update({"allowed": True, "requested": False}).eq("telegram_id", str(target)).execute()
            await q.edit_message_text(f"✅ User <code>{target}</code> approved.", parse_mode=ParseMode.HTML)
            try:
                gif = _neko_gif()
                msg_text = "🎉 <b>Access Granted!</b>\n\nWelcome to Senpai TV! 🎌\nUse /anime to start browsing."
                if gif:
                    await ctx.bot.send_animation(chat_id=target, animation=gif, caption=msg_text, parse_mode=ParseMode.HTML)
                else:
                    await ctx.bot.send_message(chat_id=target, text=msg_text, parse_mode=ParseMode.HTML)
            except Exception:
                pass
        else:
            if d:
                d.table("bot_users").update({"requested": False}).eq("telegram_id", str(target)).execute()
            await q.edit_message_text(f"❌ Request from <code>{target}</code> denied.", parse_mode=ParseMode.HTML)
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
            await q.edit_message_text("✅ No pending requests.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="admin:panel")]]))
            return
        rows = []
        for u in pending:
            name = u.get("first_name") or u.get("username") or "?"
            rows.append([
                InlineKeyboardButton(f"✅ {name}", callback_data=f"admin:ok:{u['telegram_id']}"),
                InlineKeyboardButton("❌ Deny",    callback_data=f"admin:no:{u['telegram_id']}"),
            ])
        rows.append([InlineKeyboardButton("◀️ Back", callback_data="admin:panel")])
        text = f"🔔 <b>Pending Requests ({len(pending)})</b>\n\n"
        for u in pending:
            text += f"• <b>{u.get('first_name','?')}</b> @{u.get('username','none')} — <code>{u['telegram_id']}</code>\n"
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("admin:users:"):
        if uid not in ADMIN_IDS:
            await q.answer("⛔", show_alert=True); return
        page = int(data.split(":")[-1])
        users = _all_users(d) if d else []
        chunk = users[page*20:(page+1)*20]
        lines = [f"👥 <b>Users ({len(users)})</b> — page {page+1}\n"]
        for u in chunk:
            s = "✅" if u.get("allowed") else ("⏳" if u.get("requested") else "❌")
            w = "🎬" if u.get("can_watch") else "🔒"
            name = u.get("first_name") or u.get("username") or "?"
            lines.append(f"{s}{w} <code>{u['telegram_id']}</code> {name}")
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"admin:users:{page-1}"))
        if (page+1)*20 < len(users):
            nav.append(InlineKeyboardButton("▶️", callback_data=f"admin:users:{page+1}"))
        rows = [nav] if nav else []
        rows.append([InlineKeyboardButton("◀️ Back", callback_data="admin:panel")])
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                  reply_markup=InlineKeyboardMarkup(rows))
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
                await ctx.bot.send_message(chat_id=target,
                    text="🎬 <b>Watch Access Granted!</b>\nYou can now view episodes and stream links.\nUse /anime 🎌",
                    parse_mode=ParseMode.HTML)
            except Exception:
                pass
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
            f"🖥️ Servers:      <b>{s.get('servers', 0):,}</b>"
        )
        back = "admin:panel" if uid in ADMIN_IDS else "anime:pg:1:all"
        await q.edit_message_text(text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data=back)]]))
        return

    # ── Search ask ────────────────────────────────────────────────────────────
    if data == "search:ask":
        u_obj = _get_user(d, uid) if d else None
        if uid not in ADMIN_IDS and (not u_obj or not u_obj.get("allowed")):
            await q.answer("🔒 Access required.", show_alert=True); return
        ctx.user_data["searching"] = True
        await q.edit_message_text(
            "🔍 <b>Search Anime</b>\n\nType the title you're looking for:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖️ Cancel", callback_data="anime:pg:1:all")
            ]])
        )
        return

    # ── Anime list page ───────────────────────────────────────────────────────
    if data.startswith("anime:pg:"):
        u_obj = _get_user(d, uid) if d else None
        if uid not in ADMIN_IDS and (not u_obj or not u_obj.get("allowed")):
            await q.answer("🔒 Access required.", show_alert=True); return
        _, _, pg, ct = data.split(":", 3)
        await _send_anime_list(q.message, page=int(pg), ctype=ct, edit=True)
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
                rows.append([InlineKeyboardButton("🔒 Episodes (request watch access)", callback_data="noop")])
        else:
            if cw:
                rows.append([InlineKeyboardButton("▶️ Watch Movie", callback_data=f"anime:watch:{cid}")])
            else:
                rows.append([InlineKeyboardButton("🔒 Watch (request watch access)", callback_data="noop")])
        rows.append([InlineKeyboardButton("◀️ Back", callback_data="anime:pg:1:all")])
        kb = InlineKeyboardMarkup(rows)
        poster = c.get("poster_url") or c.get("thumbnail_url") or ""
        if poster and poster.startswith("http"):
            try:
                await q.message.reply_photo(photo=poster, caption=text,
                                            parse_mode=ParseMode.HTML, reply_markup=kb)
                try:
                    await q.message.delete()
                except Exception:
                    pass
                return
            except Exception:
                pass
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except BadRequest:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # ── Episodes list ─────────────────────────────────────────────────────────
    if data.startswith("anime:eps:"):
        u_obj = _get_user(d, uid) if d else None
        if not _can_watch(uid, u_obj):
            await q.answer("🔒 Watch access required.", show_alert=True); return
        parts = data.split(":")  # anime:eps:<cid>:<pg>
        cid, ep_page = parts[2], int(parts[3])
        c = _content_detail(d, cid) if d else None
        eps = _episodes(d, cid) if d else []
        if not eps:
            await q.answer("No episodes found.", show_alert=True); return

        seasons: dict[int, list] = {}
        for ep in eps:
            seasons.setdefault(ep["season_number"], []).append(ep)

        # Show 1 season at a time (paginated by season)
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
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        except BadRequest:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    # ── Episode servers ───────────────────────────────────────────────────────
    if data.startswith("anime:ep:"):
        u_obj = _get_user(d, uid) if d else None
        if not _can_watch(uid, u_obj):
            await q.answer("🔒 Watch access required.", show_alert=True); return
        parts = data.split(":")  # anime:ep:<ep_id>:<cid>
        ep_id, cid = parts[2], parts[3]
        ep = _ep_detail(d, ep_id) if d else None
        srvs = _servers(d, ep_id) if d else []
        c = _content_detail(d, cid) if d else None
        show = c["title"] if c else ""
        ep_lbl = f"S{ep['season_number']}E{ep['episode_number']}" if ep else "Episode"
        ep_title = ep.get("title") or ep_lbl if ep else ep_lbl

        text = f"▶️ <b>{show}</b>\n<b>{ep_lbl}</b>: {ep_title}\n\n"
        rows = []
        if srvs:
            text += f"🖥️ <b>{len(srvs)} stream server(s):</b>"
            for i, s in enumerate(srvs, 1):
                lbl = f"▶️ {s.get('server_name','Server')} · {s.get('quality','')} {s.get('language','')}"
                url = s.get("stream_url", "")
                if url:
                    rows.append([InlineKeyboardButton(lbl.strip(), url=url)])
        else:
            text += "⚠️ No stream servers found for this episode."

        # Try to detect season page index
        ep_page = 0
        if ep and d:
            all_eps = _episodes(d, cid)
            seasons = sorted(set(e["season_number"] for e in all_eps))
            if ep["season_number"] in seasons:
                ep_page = seasons.index(ep["season_number"])

        rows.append([InlineKeyboardButton("◀️ Back to Episodes", callback_data=f"anime:eps:{cid}:{ep_page}")])
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        except BadRequest:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    # ── Movie watch ───────────────────────────────────────────────────────────
    if data.startswith("anime:watch:"):
        u_obj = _get_user(d, uid) if d else None
        if not _can_watch(uid, u_obj):
            await q.answer("🔒 Watch access required.", show_alert=True); return
        cid = data[len("anime:watch:"):]
        c = _content_detail(d, cid) if d else None
        eps = _episodes(d, cid) if d else []
        rows = []
        if eps:
            ep = eps[0]
            srvs = _servers(d, ep["id"]) if d else []
            text = f"🎬 <b>{c['title'] if c else 'Movie'}</b>\n\n"
            if srvs:
                text += f"🖥️ <b>{len(srvs)} stream server(s):</b>"
                for i, s in enumerate(srvs, 1):
                    lbl = f"▶️ {s.get('server_name','Server')} · {s.get('quality','')} {s.get('language','')}"
                    if s.get("stream_url"):
                        rows.append([InlineKeyboardButton(lbl.strip(), url=s["stream_url"])])
            else:
                text += "⚠️ No stream servers found."
        else:
            text = f"⚠️ No stream available for <b>{c['title'] if c else 'this movie'}</b>."
        rows.append([InlineKeyboardButton("◀️ Back", callback_data=f"anime:view:{cid}")])
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        except BadRequest:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    log.debug(f"Unhandled callback: {data}")


# ── Text message handler ───────────────────────────────────────────────────────
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.pop("searching", False):
        await _run_search(update.message, update.message.text.strip(), edit=False)
        return
    # Default nudge
    await update.message.reply_text(
        "💡 Use /anime to browse, /search &lt;title&gt; to find anime, or /help for all commands.",
        parse_mode=ParseMode.HTML
    )


# ── Bot startup ────────────────────────────────────────────────────────────────
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("anime",  cmd_anime))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("admin",  cmd_admin))
    app.add_handler(CommandHandler("users",  cmd_users))
    app.add_handler(CommandHandler("allow",  cmd_allow))
    app.add_handler(CommandHandler("block",  cmd_block))
    app.add_handler(CommandHandler("grant",  cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


async def _bot_async():
    """Run the bot using manual async lifecycle (no signal handlers — safe in threads)."""
    app = build_app()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("[bot] Senpai TV bot is live and polling!")
    # Block forever until the loop is stopped
    await asyncio.Event().wait()


def run_bot():
    """Run bot in a dedicated thread with its own event loop (no signal handlers)."""
    if not BOT_TOKEN:
        log.error("[bot] TELEGRAM_BOT_TOKEN not set — bot disabled.")
        return
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_bot_async())
    except Exception as e:
        log.error(f"[bot] crashed: {e}", exc_info=True)
    finally:
        loop.close()


def start_bot():
    """Launch bot in a background daemon thread."""
    t = threading.Thread(target=run_bot, name="tg-bot", daemon=True)
    t.start()
    return t
