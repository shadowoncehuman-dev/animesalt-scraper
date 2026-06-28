#!/usr/bin/env python3
"""
Senpai TV — Content Scraper Service
Run: python main.py
"""

import os, sys, io, re, json, threading, time, traceback, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone
from collections import deque

SB_URL = os.environ.get("SUPABASE_URL", "")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _sb():
    if SB_URL and SB_KEY:
        try:
            from supabase import create_client
            return create_client(SB_URL, SB_KEY)
        except Exception:
            pass
    return None


def _content_page_data(page: int = 1, ctype: str = "all", size: int = 24):
    d = _sb()
    if not d:
        return [], 0
    try:
        offset = (page - 1) * size
        q = d.table("content").select(
            "id,title,type,release_year,rating,poster_url,language,status,created_at"
        )
        tq = d.table("content").select("id", count="exact", head=True)
        if ctype and ctype != "all":
            q = q.eq("type", ctype)
            tq = tq.eq("type", ctype)
        total = tq.execute().count or 0
        items = q.order("created_at", desc=True).range(offset, offset + size - 1).execute().data or []
        return items, total
    except Exception:
        return [], 0


def _content_episodes(content_id: str):
    d = _sb()
    if not d:
        return []
    try:
        return d.table("episodes").select(
            "id,season_number,episode_number,title"
        ).eq("content_id", content_id).order("season_number").order("episode_number").execute().data or []
    except Exception:
        return []


def _episode_servers(ep_id: str):
    d = _sb()
    if not d:
        return []
    try:
        return d.table("video_servers").select(
            "server_name,stream_url,quality,language"
        ).eq("episode_id", ep_id).execute().data or []
    except Exception:
        return []

PORT           = int(os.environ.get("PORT", 5000))
INTERVAL_HRS   = float(os.environ.get("SCRAPE_INTERVAL_HOURS", "6"))

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

def _strip(s: str) -> str:
    return _ANSI.sub("", s)

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def _nowt() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# ─── Telegram (via telegram_bot.py) ──────────────────────────────────────────
try:
    import telegram_bot as _tgbot
    def tg_start(cycle, t, e):   _tgbot.notify_cycle_start(cycle, t, e)
    def tg_done(cycle, new, upd, skip, ep, srv, err, dur, nxt):
        _tgbot.notify_cycle_done(cycle, new, upd, skip, ep, srv, err, dur, nxt)
    def tg_new(title, ctype, eps): _tgbot.notify_new_title(title, ctype, eps)
    def tg_err(cycle, err):       _tgbot.notify_error(cycle, "scraper loop", str(err))
except ImportError:
    def tg_start(*a, **k): pass
    def tg_done(*a, **k): pass
    def tg_new(*a, **k): pass
    def tg_err(*a, **k): pass

# ─── Shared state ────────────────────────────────────────────────────────────
STATE = {
    "cycle": 0, "phase": "Waiting to start…", "running": False,
    "last_started": None, "last_finished": None, "next_run": None,
    "current": 0, "total": 0,
    "current_title": "", "current_url": "", "current_status": "",
    "titles_new": 0, "titles_updated": 0, "titles_skipped": 0,
    "episodes_new": 0, "servers_new": 0, "errors": 0,
    "cycles": [],
    "terminal": deque(maxlen=400),   # raw shell lines (ANSI stripped)
    "new_titles": deque(maxlen=100),
}
_lock = threading.Lock()

def _log(line: str, kind: str = "info"):
    """Append a stripped line to the terminal feed."""
    clean = _strip(line).rstrip()
    if not clean:
        return
    with _lock:
        STATE["terminal"].appendleft({"t": _nowt(), "txt": clean, "k": kind})

# ─── Stdout interceptor ───────────────────────────────────────────────────────
class _Tee(io.TextIOBase):
    """Write to real stdout AND capture every line into STATE['terminal']."""
    def __init__(self, real):
        self._real = real
        self._buf  = ""

    def write(self, s: str) -> int:
        self._real.write(s)
        self._real.flush()
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            kind = "error" if any(x in line for x in ("✗", "ERROR", "error", "Error")) \
                   else "warn" if any(x in line for x in ("⚠", "WARN", "WARNING")) \
                   else "new"  if any(x in line for x in ("+", "[NEW]", "NEW")) \
                   else "upd"  if any(x in line for x in ("↑", "[UPDATED]", "SKIP", "–")) \
                   else "info"
            _log(line, kind)
        return len(s)

    def flush(self):
        self._real.flush()

    def fileno(self):
        return self._real.fileno()

# ─── HTML template ────────────────────────────────────────────────────────────
_CSS = """
:root{
  --bg:#080b10;--s1:#0d1117;--s2:#161b22;--s3:#1c2128;
  --border:#30363d;--purple:#8b5cf6;--pink:#ec4899;--pl:#a78bfa;
  --green:#3fb950;--yellow:#d29922;--red:#f85149;--blue:#58a6ff;
  --cyan:#39d353;--text:#c9d1d9;--muted:#6e7681;--white:#f0f6fc;
  --font-mono:'Cascadia Code','Fira Code','JetBrains Mono',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px}
a{color:inherit;text-decoration:none}

/* ── Header ── */
header{
  background:linear-gradient(180deg,#0d1117 0%,#080b10 100%);
  border-bottom:1px solid var(--border);
  padding:0 28px;height:56px;display:flex;align-items:center;gap:14px;
  position:sticky;top:0;z-index:50;
}
.logo{
  width:34px;height:34px;border-radius:8px;flex-shrink:0;
  background:linear-gradient(135deg,var(--purple),var(--pink));
  display:flex;align-items:center;justify-content:center;
  font-size:17px;font-weight:900;color:#fff;
}
.brand{font-size:17px;font-weight:700;color:var(--white);letter-spacing:-.3px}
.brand em{color:var(--pl);font-style:normal}
.hdr-meta{margin-left:auto;display:flex;align-items:center;gap:18px}
.chip{
  display:inline-flex;align-items:center;gap:6px;
  padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700;
  letter-spacing:.5px;border:1px solid currentColor;
}
.chip.running{color:var(--green);background:rgba(63,185,80,.1)}
.chip.idle{color:var(--yellow);background:rgba(210,153,34,.1)}
.chip .dot{width:6px;height:6px;border-radius:50%;background:currentColor;
  animation:blink 1.4s ease infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}
.hdr-time{font-size:11px;color:var(--muted)}

/* ── Layout ── */
.layout{display:grid;grid-template-columns:320px 1fr;height:calc(100vh - 56px)}
@media(max-width:1000px){.layout{grid-template-columns:1fr;height:auto}}

/* ── Sidebar ── */
aside{
  border-right:1px solid var(--border);background:var(--s1);
  overflow-y:auto;display:flex;flex-direction:column;gap:0;
}
.aside-block{border-bottom:1px solid var(--border);padding:16px}
.aside-title{
  font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;
  color:var(--muted);margin-bottom:12px;display:flex;align-items:center;gap:6px;
}
.aside-title span{font-size:13px}

/* stats grid */
.stats-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.stat-card{
  background:var(--s2);border:1px solid var(--border);border-radius:8px;
  padding:10px 12px;position:relative;overflow:hidden;
}
.stat-card::after{
  content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:var(--c,var(--purple));
}
.stat-card .sv{font-size:22px;font-weight:800;color:var(--white);line-height:1}
.stat-card .sk{font-size:10px;color:var(--muted);margin-top:3px;text-transform:uppercase;letter-spacing:.5px}

/* progress */
.phase-txt{font-size:12px;color:var(--muted);margin-bottom:8px}
.phase-txt strong{color:var(--pl)}
.prog-wrap{background:var(--s3);border-radius:4px;height:6px;overflow:hidden;margin-bottom:6px}
.prog-bar{
  height:100%;border-radius:4px;
  background:linear-gradient(90deg,var(--purple),var(--pink));
  transition:width .5s;box-shadow:0 0 8px rgba(139,92,246,.5);
}
.prog-nums{display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}
.cur-item{
  background:var(--s3);border:1px solid var(--border);border-radius:8px;
  padding:10px;margin-top:10px;
}
.cur-title{font-size:12px;color:var(--white);font-weight:600;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cur-url{font-size:10px;color:var(--muted);margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cur-badge{
  display:inline-block;margin-top:5px;font-size:10px;padding:2px 7px;
  border-radius:10px;font-weight:700;
}
.cur-badge.new{background:rgba(63,185,80,.15);color:var(--green)}
.cur-badge.upd{background:rgba(88,166,255,.15);color:var(--blue)}
.cur-badge.err{background:rgba(248,81,73,.15);color:var(--red)}
.cur-badge.oth{background:var(--s3);color:var(--muted)}
.meta-row{display:flex;flex-direction:column;gap:4px;margin-top:8px;font-size:11px;color:var(--muted)}
.meta-row span strong{color:var(--text)}

/* new titles list */
.ntl{list-style:none;display:flex;flex-direction:column;gap:5px;max-height:200px;overflow-y:auto}
.ntl::-webkit-scrollbar{width:3px}
.ntl::-webkit-scrollbar-thumb{background:var(--border)}
.ntl li{display:flex;align-items:center;gap:6px;font-size:12px;
  background:var(--s2);border-radius:6px;padding:6px 8px}
.ntl .tb{font-size:9px;padding:2px 6px;border-radius:8px;font-weight:700;white-space:nowrap}
.ntl .tb.series{background:rgba(139,92,246,.2);color:var(--pl)}
.ntl .tb.movie{background:rgba(236,72,153,.2);color:var(--pink)}
.ntl .tn{flex:1;color:var(--white);font-weight:500;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ntl .te{color:var(--muted);font-size:10px;white-space:nowrap}
.empty-hint{font-size:12px;color:var(--muted);text-align:center;padding:16px 0;opacity:.6}

/* cycle history table */
.hist-tbl{width:100%;border-collapse:collapse;font-size:11px}
.hist-tbl th{color:var(--muted);text-align:left;padding:4px 6px;
  text-transform:uppercase;font-size:9px;letter-spacing:.6px;
  border-bottom:1px solid var(--border)}
.hist-tbl td{padding:5px 6px;border-bottom:1px solid rgba(48,54,61,.4);color:var(--text)}
.hist-tbl tr:last-child td{border:none}
.ok{color:var(--green)}.warn2{color:var(--yellow)}.err2{color:var(--red)}

/* ── Main (terminal) ── */
main{
  display:flex;flex-direction:column;background:var(--bg);overflow:hidden;
}
.term-header{
  padding:10px 20px;border-bottom:1px solid var(--border);
  background:var(--s1);display:flex;align-items:center;gap:10px;flex-shrink:0;
}
.term-dots{display:flex;gap:6px}
.td{width:12px;height:12px;border-radius:50%}
.td.r{background:#ff5f57}.td.y{background:#febc2e}.td.g{background:#28c840}
.term-label{font-size:12px;color:var(--muted);font-family:var(--font-mono)}
.term-body{
  flex:1;overflow-y:auto;padding:14px 20px;
  font-family:var(--font-mono);font-size:12.5px;line-height:1.7;
  background:var(--bg);
}
.term-body::-webkit-scrollbar{width:6px}
.term-body::-webkit-scrollbar-thumb{background:#30363d;border-radius:3px}
.term-body::-webkit-scrollbar-track{background:transparent}

.tl{list-style:none;display:flex;flex-direction:column}
.tl li{display:flex;gap:10px;padding:1px 0;border-radius:3px}
.tl li:hover{background:rgba(255,255,255,.03)}
.tl .lt{color:var(--muted);white-space:nowrap;flex-shrink:0;font-size:11px;padding-top:1px;user-select:none}
.tl .lm{flex:1;white-space:pre-wrap;word-break:break-all}
.tl li.info .lm{color:var(--text)}
.tl li.new  .lm{color:var(--green)}
.tl li.upd  .lm{color:var(--blue)}
.tl li.warn .lm{color:var(--yellow)}
.tl li.error .lm{color:var(--red)}
.tl li.head .lm{color:var(--pl);font-weight:700}
.cursor{display:inline-block;width:8px;height:14px;background:var(--pl);
  vertical-align:middle;margin-left:2px;animation:blink 1s step-end infinite}
.empty-term{color:var(--muted);font-size:13px;text-align:center;
  padding:60px 0;opacity:.5;font-family:var(--font-mono)}
"""

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta http-equiv="refresh" content="8"/>
<title>Senpai TV — Scraper</title>
<style>%%CSS%%</style>
</head>
<body>

<header>
  <div class="logo">S</div>
  <div class="brand">Senpai <em>TV</em> <span style="color:var(--muted);font-size:12px;font-weight:400">/ scraper</span></div>
  <div class="hdr-meta">
    <span class="chip %%STATUS_CLS%%"><span class="dot"></span>%%STATUS_TXT%%</span>
    <span class="hdr-time">%%TIME%% UTC · auto-refresh 8s</span>
  </div>
</header>

<div class="layout">

  <!-- ── Sidebar ── -->
  <aside>

    <!-- Stats -->
    <div class="aside-block">
      <div class="aside-title"><span>📊</span> This Cycle</div>
      <div class="stats-grid">
        <div class="stat-card" style="--c:var(--purple)">
          <div class="sv">%%CYCLE%%</div><div class="sk">Cycles run</div></div>
        <div class="stat-card" style="--c:var(--green)">
          <div class="sv">%%NEW%%</div><div class="sk">New titles</div></div>
        <div class="stat-card" style="--c:var(--blue)">
          <div class="sv">%%UPD%%</div><div class="sk">Updated</div></div>
        <div class="stat-card" style="--c:var(--muted)">
          <div class="sv">%%SKIP%%</div><div class="sk">Skipped</div></div>
        <div class="stat-card" style="--c:var(--pink)">
          <div class="sv">%%EP%%</div><div class="sk">Episodes</div></div>
        <div class="stat-card" style="--c:var(--cyan)">
          <div class="sv">%%SRV%%</div><div class="sk">Servers</div></div>
        <div class="stat-card" style="--c:var(--red);grid-column:span 2">
          <div class="sv">%%ERR%%</div><div class="sk">Errors</div></div>
      </div>
    </div>

    <!-- Progress -->
    <div class="aside-block">
      <div class="aside-title"><span>⚙️</span> Progress</div>
      <div class="phase-txt">Phase: <strong>%%PHASE%%</strong></div>
      <div class="prog-wrap"><div class="prog-bar" style="width:%%PCT%%%"></div></div>
      <div class="prog-nums"><span>%%CUR%% / %%TOT%% titles</span><span>%%PCT%%%</span></div>
      %%CUR_ITEM%%
      <div class="meta-row">
        <span>🕐 Started: <strong>%%STARTED%%</strong></span>
        <span>✅ Last done: <strong>%%DONE%%</strong></span>
        <span>⏰ Next run: <strong>%%NEXT%%</strong></span>
      </div>
    </div>

    <!-- New titles -->
    <div class="aside-block">
      <div class="aside-title"><span>🆕</span> New Titles This Cycle</div>
      %%NEW_TITLES%%
    </div>

    <!-- Cycle history -->
    <div class="aside-block">
      <div class="aside-title"><span>📋</span> Cycle History</div>
      %%HISTORY%%
    </div>

  </aside>

  <!-- ── Terminal ── -->
  <main>
    <div class="term-header">
      <div class="term-dots">
        <div class="td r"></div><div class="td y"></div><div class="td g"></div>
      </div>
      <div class="term-label">scraper@senpai-tv ~ bash — live output</div>
    </div>
    <div class="term-body">%%TERMINAL%%</div>
  </main>

</div>
</body>
</html>"""


def _build_html() -> bytes:
    with _lock:
        s       = dict(STATE)
        term    = list(STATE["terminal"])
        nt      = list(STATE["new_titles"])
        cycs    = list(STATE["cycles"])

    running = s["running"]
    pct = int(s["current"] / s["total"] * 100) if s["total"] else 0

    # Current item block
    st = s.get("current_status") or ""
    sc = "new" if "new" in st else ("err" if "error" in st else ("upd" if st in ("updated","upd") else "oth"))
    ic = {"new": "🆕", "err": "❌", "upd": "✏️"}.get(sc, "⚙️")
    cur_item_html = ""
    if s["current_title"] and running:
        cur_item_html = (
            f'<div class="cur-item">'
            f'<div class="cur-title">{ic} {s["current_title"]}</div>'
            f'<div class="cur-url">{s["current_url"]}</div>'
            f'<span class="cur-badge {sc}">{st or "processing"}</span>'
            f'</div>'
        )

    # Terminal
    if term:
        items = "".join(
            f'<li class="{e["k"]}">'
            f'<span class="lt">{e["t"]}</span>'
            f'<span class="lm">{_html_esc(e["txt"])}</span></li>'
            for e in reversed(term)
        )
        terminal_html = f'<ul class="tl">{items}<li class="info"><span class="lt">&nbsp;</span><span class="lm"><span class="cursor"></span></span></li></ul>'
    else:
        terminal_html = '<div class="empty-term">Waiting for scraper to start…<br><span class="cursor"></span></div>'

    # New titles
    if nt:
        items = "".join(
            f'<li><span class="tb {t["type"]}">{t["type"].upper()}</span>'
            f'<span class="tn">{_html_esc(t["title"])}</span>'
            f'<span class="te">{t["episodes"]} ep</span></li>'
            for t in nt
        )
        nt_html = f'<ul class="ntl">{items}</ul>'
    else:
        nt_html = '<div class="empty-hint">No new titles yet this cycle</div>'

    # History
    if cycs:
        rows = "".join(
            f'<tr>'
            f'<td>#{c["cycle"]}</td>'
            f'<td style="color:var(--muted)">{c["started"]}</td>'
            f'<td>{c["duration"]}</td>'
            f'<td class="ok">+{c["new"]}</td>'
            f'<td>{c["updated"]}</td>'
            f'<td>{"<span class=err2>" + str(c["errors"]) + "</span>" if c["errors"] else "<span class=ok>0</span>"}</td>'
            f'</tr>'
            for c in reversed(cycs)
        )
        hist_html = (
            '<table class="hist-tbl"><thead><tr>'
            '<th>#</th><th>Started</th><th>Duration</th><th>New</th><th>Upd</th><th>Err</th>'
            '</tr></thead><tbody>' + rows + '</tbody></table>'
        )
    else:
        hist_html = '<div class="empty-hint">No completed cycles yet</div>'

    now_str = _ts()
    return (_HTML
        .replace("%%CSS%%",        _CSS)
        .replace("%%STATUS_CLS%%", "running" if running else "idle")
        .replace("%%STATUS_TXT%%", "SCRAPING" if running else "IDLE")
        .replace("%%TIME%%",       now_str)
        .replace("%%CYCLE%%",      str(s["cycle"]))
        .replace("%%NEW%%",        str(s["titles_new"]))
        .replace("%%UPD%%",        str(s["titles_updated"]))
        .replace("%%SKIP%%",       str(s["titles_skipped"]))
        .replace("%%EP%%",         str(s["episodes_new"]))
        .replace("%%SRV%%",        str(s["servers_new"]))
        .replace("%%ERR%%",        str(s["errors"]))
        .replace("%%PHASE%%",      _html_esc(s["phase"]))
        .replace("%%PCT%%",        str(pct))
        .replace("%%CUR%%",        str(s["current"]))
        .replace("%%TOT%%",        str(s["total"]))
        .replace("%%CUR_ITEM%%",   cur_item_html)
        .replace("%%STARTED%%",    s["last_started"] or "—")
        .replace("%%DONE%%",       s["last_finished"] or "—")
        .replace("%%NEXT%%",       s["next_run"] or "—")
        .replace("%%NEW_TITLES%%", nt_html)
        .replace("%%HISTORY%%",    hist_html)
        .replace("%%TERMINAL%%",   terminal_html)
    ).encode()


def _html_esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_CONTENT_CSS = """
:root{--bg:#080b10;--s1:#0d1117;--s2:#161b22;--s3:#1c2128;--border:#30363d;
  --purple:#8b5cf6;--pink:#ec4899;--pl:#a78bfa;--green:#3fb950;--yellow:#d29922;
  --red:#f85149;--blue:#58a6ff;--text:#c9d1d9;--muted:#6e7681;--white:#f0f6fc}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
header{background:var(--s1);border-bottom:1px solid var(--border);padding:12px 24px;
  display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
.logo{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,var(--purple),var(--pink));
  display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:900;color:#fff}
.brand{font-size:16px;font-weight:700;color:var(--white)}
.nav-links{display:flex;gap:12px;margin-left:16px}
.nav-links a{font-size:13px;color:var(--muted);padding:4px 10px;border-radius:6px;border:1px solid transparent}
.nav-links a:hover,.nav-links a.active{background:var(--s2);border-color:var(--border);color:var(--white);text-decoration:none}
.hdr-r{margin-left:auto;font-size:12px;color:var(--muted)}
.filters{display:flex;gap:10px;padding:16px 24px;border-bottom:1px solid var(--border);
  background:var(--s1);flex-wrap:wrap;align-items:center}
.filters select,.filters input{background:var(--s2);border:1px solid var(--border);color:var(--text);
  padding:6px 12px;border-radius:6px;font-size:13px;outline:none}
.filters select:focus,.filters input:focus{border-color:var(--purple)}
.btn{display:inline-block;padding:6px 14px;border-radius:6px;border:1px solid var(--border);
  background:var(--s2);color:var(--text);font-size:13px;cursor:pointer;text-decoration:none}
.btn:hover{background:var(--s3);color:var(--white);text-decoration:none}
.btn.primary{background:var(--purple);border-color:var(--purple);color:#fff}
.btn.primary:hover{background:#7c3aed}
.content-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));
  gap:16px;padding:20px 24px}
.card{background:var(--s1);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.card-top{display:flex;gap:12px;padding:14px}
.poster{width:64px;height:90px;object-fit:cover;border-radius:6px;flex-shrink:0;background:var(--s3)}
.poster-ph{width:64px;height:90px;border-radius:6px;flex-shrink:0;background:var(--s3);
  display:flex;align-items:center;justify-content:center;font-size:24px}
.card-info{flex:1;min-width:0}
.card-title{font-size:14px;font-weight:700;color:var(--white);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:4px}
.card-meta{font-size:11px;color:var(--muted);margin-bottom:6px}
.badge{display:inline-block;padding:2px 7px;border-radius:10px;font-size:10px;font-weight:700;margin-right:4px}
.badge.series{background:rgba(139,92,246,.2);color:var(--pl)}
.badge.movie{background:rgba(236,72,153,.2);color:var(--pink)}
.badge.status{background:var(--s3);color:var(--muted)}
.rating{color:var(--yellow);font-size:11px}
.eps-toggle{width:100%;border:none;background:var(--s2);color:var(--text);padding:8px 14px;
  text-align:left;font-size:12px;cursor:pointer;border-top:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:center}
.eps-toggle:hover{background:var(--s3);color:var(--white)}
.eps-body{display:none;border-top:1px solid var(--border);background:var(--bg);max-height:400px;overflow-y:auto}
.eps-body.open{display:block}
.season-header{padding:8px 14px;font-size:11px;font-weight:700;color:var(--pl);
  text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--border);
  background:var(--s3)}
.ep-row{padding:8px 14px;border-bottom:1px solid rgba(48,54,61,.5);display:flex;
  align-items:center;gap:10px;font-size:12px}
.ep-row:last-child{border-bottom:none}
.ep-row:hover{background:var(--s2)}
.ep-num{color:var(--muted);flex-shrink:0;font-size:11px;width:36px}
.ep-title{flex:1;color:var(--text);min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.srv-links{display:flex;gap:6px;flex-wrap:wrap}
.srv-btn{font-size:10px;padding:3px 8px;border-radius:4px;background:rgba(63,185,80,.15);
  color:var(--green);border:1px solid rgba(63,185,80,.3);text-decoration:none;white-space:nowrap}
.srv-btn:hover{background:rgba(63,185,80,.3);text-decoration:none}
.srv-btn.no-srv{color:var(--muted);background:var(--s3);border-color:var(--border);cursor:default}
.pagination{display:flex;gap:8px;padding:20px 24px;justify-content:center;align-items:center;
  border-top:1px solid var(--border)}
.empty{text-align:center;padding:60px 24px;color:var(--muted);font-size:14px}
.stats-bar{padding:10px 24px;background:var(--s1);border-bottom:1px solid var(--border);
  font-size:12px;color:var(--muted);display:flex;gap:20px}
.stats-bar span strong{color:var(--text)}
"""


def _build_content_html(page: int, ctype: str, search: str = "") -> bytes:
    items, total = _content_page_data(page, ctype, size=24)
    total_pages = max(1, -(-total // 24))  # ceil div

    # Build content cards
    cards_html = ""
    for c in items:
        cid     = c["id"]
        title   = _html_esc(c.get("title") or "Untitled")
        ctp     = c.get("type") or "series"
        yr      = c.get("release_year") or ""
        rating  = float(c.get("rating") or 0)
        lang    = c.get("language") or ""
        status  = (c.get("status") or "").title()
        poster  = c.get("poster_url") or ""
        added   = (c.get("created_at") or "")[:10]
        icon    = "🎬" if ctp == "movie" else "📺"

        if poster and poster.startswith("http"):
            poster_html = f'<img class="poster" src="{poster}" alt="" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
            poster_html += f'<div class="poster-ph" style="display:none">{icon}</div>'
        else:
            poster_html = f'<div class="poster-ph">{icon}</div>'

        meta_parts = []
        if yr: meta_parts.append(str(yr))
        if lang: meta_parts.append(lang)
        if added: meta_parts.append(f"Added {added}")
        meta_str = " · ".join(meta_parts)

        stars = "★" * round(rating / 2) + "☆" * (5 - round(rating / 2)) if rating else ""
        rating_html = f'<div class="rating">{stars} {rating:.1f}/10</div>' if rating else ""

        cards_html += f"""
<div class="card" id="card-{cid}">
  <div class="card-top">
    {poster_html}
    <div class="card-info">
      <div class="card-title" title="{title}">{title}</div>
      <div class="card-meta">{meta_str}</div>
      <div>
        <span class="badge {ctp}">{ctp.upper()}</span>
        {"<span class='badge status'>" + status + "</span>" if status else ""}
      </div>
      {rating_html}
    </div>
  </div>
  <button class="eps-toggle" onclick="toggleEps('{cid}',this)">
    <span>{"🎞 Episodes" if ctp == "series" else "▶ Watch"}</span>
    <span class="arrow">▼</span>
  </button>
  <div class="eps-body" id="eps-{cid}">
    <div style="padding:10px 14px;color:var(--muted);font-size:12px">Loading…</div>
  </div>
</div>"""

    # Pagination
    qs_base = f"?type={ctype}"
    pag_html = ""
    if page > 1:
        pag_html += f'<a class="btn" href="/content{qs_base}&page={page-1}">◀ Prev</a>'
    pag_html += f'<span style="color:var(--muted);font-size:13px">Page {page} / {total_pages} &nbsp;·&nbsp; {total:,} titles</span>'
    if page < total_pages:
        pag_html += f'<a class="btn" href="/content{qs_base}&page={page+1}">Next ▶</a>'

    # Type filter tabs
    def tab(label, value):
        active = "active" if ctype == value else ""
        return f'<a href="/content?type={value}&page=1" class="nav-links {active}">{label}</a>'

    return (f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Senpai TV — Content Browser</title>
<style>{_CONTENT_CSS}</style>
</head><body>
<header>
  <div class="logo">S</div>
  <div class="brand">Senpai TV</div>
  <div class="nav-links">
    <a href="/" class="{'active' if False else ''}">📊 Scraper</a>
    <a href="/content?type=all" class="{'active' if True else ''}">🎌 Content</a>
  </div>
  <div class="hdr-r">{total:,} titles · {total_pages} pages</div>
</header>
<div class="filters">
  <a href="/content?type=all&page=1" class="btn {'primary' if ctype=='all' else ''}">🌐 All</a>
  <a href="/content?type=series&page=1" class="btn {'primary' if ctype=='series' else ''}">📺 Anime/Series</a>
  <a href="/content?type=movie&page=1" class="btn {'primary' if ctype=='movie' else ''}">🎬 Movies</a>
  <span style="margin-left:auto;color:var(--muted);font-size:12px">Sorted: newest first · Click any title to load episodes &amp; stream links</span>
</div>
<div class="stats-bar">
  <span>Page <strong>{page}</strong> of <strong>{total_pages}</strong></span>
  <span>Showing <strong>{len(items)}</strong> of <strong>{total:,}</strong> titles</span>
  <span>Latest added on top</span>
</div>
{"<div class='content-grid'>" + cards_html + "</div>" if items else "<div class='empty'>No content found.</div>"}
<div class="pagination">{pag_html}</div>
<script>
async function toggleEps(cid, btn) {{
  const body = document.getElementById('eps-' + cid);
  const isOpen = body.classList.contains('open');
  if (isOpen) {{ body.classList.remove('open'); btn.querySelector('.arrow').textContent='▼'; return; }}
  body.classList.add('open');
  btn.querySelector('.arrow').textContent='▲';
  if (body.dataset.loaded) return;
  body.dataset.loaded = '1';
  try {{
    const r = await fetch('/api/episodes?content_id=' + cid);
    const data = await r.json();
    if (!data.seasons || !data.seasons.length) {{
      body.innerHTML = '<div style="padding:12px 14px;color:var(--muted);font-size:12px">No episodes found.</div>';
      return;
    }}
    let html = '';
    for (const [sn, eps] of data.seasons) {{
      html += '<div class="season-header">Season ' + sn + ' — ' + eps.length + ' episodes</div>';
      for (const ep of eps) {{
        const srvHtml = ep.servers.length
          ? ep.servers.map(s => '<a class="srv-btn" href="' + s.url + '" target="_blank" rel="noopener">' +
              (s.name||'Play') + (s.quality?' · '+s.quality:'') + (s.lang?' · '+s.lang:'') + '</a>').join('')
          : '<span class="srv-btn no-srv">No links</span>';
        html += '<div class="ep-row"><span class="ep-num">E' + ep.num + '</span>' +
          '<span class="ep-title">' + (ep.title||('Episode '+ep.num)) + '</span>' +
          '<div class="srv-links">' + srvHtml + '</div></div>';
      }}
    }}
    body.innerHTML = html;
  }} catch(e) {{
    body.innerHTML = '<div style="padding:12px 14px;color:var(--red);font-size:12px">Error loading episodes.</div>';
  }}
}}
</script>
</body></html>""").encode()


def _api_episodes(content_id: str) -> bytes:
    eps = _content_episodes(content_id)
    seasons: dict = {}
    for ep in eps:
        sn = ep["season_number"]
        seasons.setdefault(sn, []).append(ep)

    result_seasons = []
    for sn in sorted(seasons.keys()):
        eps_list = []
        for ep in seasons[sn]:
            srvs = _episode_servers(ep["id"])
            eps_list.append({
                "id": ep["id"],
                "num": ep["episode_number"],
                "title": ep.get("title") or "",
                "servers": [
                    {"name": s.get("server_name",""), "url": s.get("stream_url",""),
                     "quality": s.get("quality",""), "lang": s.get("language","")}
                    for s in srvs if s.get("stream_url")
                ]
            })
        result_seasons.append([sn, eps_list])

    return json.dumps({"seasons": result_seasons}).encode()


# ─── HTTP server ─────────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/status":
            with _lock:
                body = json.dumps(
                    {k: v for k, v in STATE.items()
                     if k not in ("terminal", "new_titles", "cycles")},
                    default=str
                ).encode()
            ct = "application/json"

        elif parsed.path == "/content":
            page  = int(qs.get("page",  ["1"])[0])
            ctype = qs.get("type", ["all"])[0]
            body  = _build_content_html(page, ctype)
            ct    = "text/html; charset=utf-8"

        elif parsed.path == "/api/episodes":
            cid  = qs.get("content_id", [""])[0]
            body = _api_episodes(cid) if cid else b'{"seasons":[]}'
            ct   = "application/json"

        else:
            body = _build_html()
            ct = "text/html; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_): pass


# ─── Scraper loop ─────────────────────────────────────────────────────────────
def _scraper_loop():
    time.sleep(3)

    # Install stdout tee so every pipeline print() appears in the dashboard
    _real_stdout = sys.stdout
    sys.stdout = _Tee(_real_stdout)

    try:
        import pipeline
    except Exception as e:
        _log(f"FATAL: cannot import pipeline — {e}", "error")
        print(f"[scraper] Import error: {e}", flush=True)
        sys.stdout = _real_stdout
        return

    while True:
        with _lock:
            STATE["cycle"] += 1
            STATE["running"] = True
            STATE["phase"] = "Starting…"
            STATE["current"] = STATE["total"] = 0
            STATE["current_title"] = STATE["current_url"] = STATE["current_status"] = ""
            STATE["titles_new"] = STATE["titles_updated"] = STATE["titles_skipped"] = 0
            STATE["episodes_new"] = STATE["servers_new"] = STATE["errors"] = 0
            STATE["last_started"] = _now()
            STATE["new_titles"].clear()
            cycle = STATE["cycle"]

        pipeline.STATS = pipeline.Stats()

        # Telegram: cycle started
        tg_start(cycle, 0, 0)

        t0 = time.time()

        def progress_hook(current, total, title, url, status):
            with _lock:
                STATE["phase"] = "Scraping content" if total > 0 else "Discovering…"
                STATE["current"] = current
                STATE["total"]   = total
                STATE["current_title"]  = title
                STATE["current_url"]    = url
                STATE["current_status"] = status
                STATE["titles_new"]      = pipeline.STATS.content_new
                STATE["titles_updated"]  = pipeline.STATS.content_updated
                STATE["titles_skipped"]  = pipeline.STATS.content_skipped
                STATE["episodes_new"]    = pipeline.STATS.episodes_new
                STATE["servers_new"]     = pipeline.STATS.servers_new
                STATE["errors"]          = pipeline.STATS.errors

        def new_title_hook(title, ctype, episodes):
            with _lock:
                STATE["new_titles"].appendleft(
                    {"title": title, "type": ctype, "episodes": episodes}
                )
            tg_new(title, ctype, episodes)

        try:
            with _lock:
                STATE["phase"] = "Discovering content…"
            pipeline.run(progress_hook=progress_hook, new_title_hook=new_title_hook)
            with _lock:
                STATE["phase"] = "Cycle complete ✓"
                STATE["last_finished"] = _now()
        except SystemExit:
            with _lock:
                STATE["phase"] = "Interrupted"
                STATE["last_finished"] = _now()
        except Exception:
            err = traceback.format_exc().strip().splitlines()[-1]
            with _lock:
                STATE["phase"] = f"Error: {err[:60]}"
                STATE["last_finished"] = _now()
            tg_err(cycle, err)

        elapsed = int(time.time() - t0)
        h, m, sec = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        dur = f"{h:02d}:{m:02d}:{sec:02d}"

        wake = datetime.fromtimestamp(time.time() + INTERVAL_HRS * 3600, tz=timezone.utc)
        nxt = wake.strftime("%Y-%m-%d %H:%M UTC")

        with _lock:
            STATE["running"] = False
            STATE["next_run"] = nxt
            st = pipeline.STATS
            STATE["cycles"].append({
                "cycle": cycle,
                "started": STATE["last_started"],
                "duration": dur,
                "new": st.content_new,
                "updated": st.content_updated,
                "ep_new": st.episodes_new,
                "errors": st.errors,
            })
            if len(STATE["cycles"]) > 10:
                STATE["cycles"].pop(0)

        tg_done(cycle, st.content_new, st.content_updated, st.content_skipped,
                st.episodes_new, st.servers_new, st.errors, dur, nxt)
        print(f"[senpai-tv] Cycle #{cycle} done in {dur}. Sleeping {INTERVAL_HRS:.0f}h → next: {nxt}", flush=True)
        time.sleep(INTERVAL_HRS * 3600)


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[senpai-tv] Starting on port {PORT}", flush=True)

    # Start Telegram bot in background thread
    try:
        from bot import start_bot
        start_bot()
        print("[senpai-tv] Telegram bot started", flush=True)
    except Exception as _bot_err:
        print(f"[senpai-tv] Bot startup skipped: {_bot_err}", flush=True)

    threading.Thread(target=_scraper_loop, name="scraper", daemon=False).start()
    try:
        srv = HTTPServer(("0.0.0.0", PORT), _Handler)
        print(f"[senpai-tv] Dashboard ready → http://0.0.0.0:{PORT}", flush=True)
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
