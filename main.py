#!/usr/bin/env python3
"""
Senpai TV — Content Scraper Service
Single-file entry point for Render.com. No external imports beyond pipeline.py.
Run: python main.py
"""

import os, sys, io, re, json, threading, time, traceback
import requests as _req
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone
from collections import deque

PORT           = int(os.environ.get("PORT", 10000))
INTERVAL_HRS   = float(os.environ.get("SCRAPE_INTERVAL_HOURS", "6"))
TG_TOKEN       = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT        = os.environ.get("TELEGRAM_CHAT_ID", "")

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

def _strip(s: str) -> str:
    return _ANSI.sub("", s)

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def _nowt() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# ─── Telegram ────────────────────────────────────────────────────────────────
def _tg(text: str):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        _req.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                  json={"chat_id": TG_CHAT, "text": text,
                        "parse_mode": "HTML", "disable_web_page_preview": True},
                  timeout=10)
    except Exception:
        pass

def tg_start(cycle, t, e):
    _tg(f"🟣 <b>Senpai TV</b> — Cycle <b>#{cycle}</b> started\n"
        f"📚 DB: <b>{t:,}</b> titles · <b>{e:,}</b> episodes\n🔍 Discovering…")

def tg_done(cycle, new, upd, skip, ep, srv, err, dur, nxt):
    ok = "✅ Clean" if err == 0 else f"⚠️ {err} error(s)"
    _tg(f"✅ <b>Senpai TV</b> — Cycle <b>#{cycle}</b> complete\n"
        f"➕ New: <b>{new}</b>  ✏️ Updated: <b>{upd}</b>  ⏭️ Skipped: <b>{skip}</b>\n"
        f"🎞️ Episodes: <b>{ep}</b>  🖥️ Servers: <b>{srv}</b>\n"
        f"⏱️ {dur}  ❗ {ok}\n⏰ Next: {nxt}")

def tg_new(title, ctype, eps):
    _tg(f"{'🎬' if ctype=='movie' else '📺'} <b>New!</b> {title} ({ctype}, {eps} ep)")

def tg_err(cycle, err):
    _tg(f"❌ <b>Senpai TV</b> Cycle #{cycle} error\n<code>{str(err)[:300]}</code>")

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


# ─── HTTP server ─────────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
            with _lock:
                body = json.dumps(
                    {k: v for k, v in STATE.items()
                     if k not in ("terminal", "new_titles", "cycles")},
                    default=str
                ).encode()
            ct = "application/json"
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

        # Telegram: start
        try:
            from supabase import create_client as _cc
            _db = _cc(os.environ.get("SUPABASE_URL", ""),
                       os.environ.get("SUPABASE_SERVICE_KEY", ""))
            _c = _db.table("content").select("id", count="exact", head=True).execute()
            _e = _db.table("episodes").select("id", count="exact", head=True).execute()
            tg_start(cycle, _c.count or 0, _e.count or 0)
        except Exception:
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
    threading.Thread(target=_scraper_loop, name="scraper", daemon=False).start()
    try:
        srv = HTTPServer(("0.0.0.0", PORT), _Handler)
        print(f"[senpai-tv] Dashboard ready → http://0.0.0.0:{PORT}", flush=True)
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
