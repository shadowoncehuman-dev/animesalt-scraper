#!/usr/bin/env python3
"""
Senpai TV — Content Scraper Service
Single-file entry point for Render.com.
Run: python main.py
"""

import os, sys, json, threading, time, traceback, requests as _req
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone
from collections import deque

PORT             = int(os.environ.get("PORT", 10000))
INTERVAL_HOURS   = float(os.environ.get("SCRAPE_INTERVAL_HOURS", "6"))
TG_TOKEN         = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT          = os.environ.get("TELEGRAM_CHAT_ID", "")

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def _nowt() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

# ─────────────────────────────────────────────────────────────────────────────
# Telegram helpers (inline — no separate file needed)
# ─────────────────────────────────────────────────────────────────────────────
def _tg(text: str):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        _req.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception:
        pass

def tg_cycle_start(cycle, titles_db, ep_db):
    _tg(f"🟣 <b>Senpai TV Scraper</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"▶️ Cycle <b>#{cycle}</b> started\n"
        f"📚 DB: <b>{titles_db:,}</b> titles · <b>{ep_db:,}</b> episodes\n"
        f"🔍 Discovering content…")

def tg_discovery(cycle, total, series, movies):
    _tg(f"🔎 <b>Senpai TV</b> — Cycle #{cycle} discovery done\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Found <b>{total:,}</b> titles\n"
        f"📺 Series: <b>{series:,}</b>  🎬 Movies: <b>{movies:,}</b>")

def tg_progress(cycle, cur, total, title):
    pct = int(cur / total * 100) if total else 0
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    _tg(f"⚙️ <b>Senpai TV</b> — Cycle #{cycle}\n━━━━━━━━━━━━━━━━━━━━\n"
        f"<code>{bar}</code> {pct}%\n"
        f"📌 <b>{cur:,}/{total:,}</b> done\n"
        f"🎌 <b>{title}</b>")

def tg_new_title(title, ctype, episodes):
    icon = "🎬" if ctype == "movie" else "📺"
    _tg(f"{icon} <b>New title added!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🎌 <b>{title}</b>\n"
        f"📂 {ctype.capitalize()} · 🎞️ {episodes} ep")

def tg_done(cycle, new, upd, skip, ep_new, srv_new, errors, elapsed, nxt):
    ok = "✅ All good" if errors == 0 else f"⚠️ {errors} error(s)"
    _tg(f"✅ <b>Senpai TV</b> — Cycle #{cycle} complete\n━━━━━━━━━━━━━━━━━━━━\n"
        f"➕ New:       <b>{new:,}</b>\n"
        f"✏️  Updated:  <b>{upd:,}</b>\n"
        f"⏭️  Skipped:  <b>{skip:,}</b>\n"
        f"🎞️  Episodes: <b>{ep_new:,}</b>\n"
        f"🖥️  Servers:  <b>{srv_new:,}</b>\n"
        f"⏱️  Time:     <b>{elapsed}</b>\n"
        f"❗ Errors:   <b>{errors}</b> — {ok}\n"
        f"⏰ Next run: {nxt}")

def tg_error(cycle, ctx, err):
    _tg(f"❌ <b>Senpai TV</b> — Error in Cycle #{cycle}\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 {ctx}\n<code>{str(err)[:300]}</code>")

# ─────────────────────────────────────────────────────────────────────────────
# Shared state
# ─────────────────────────────────────────────────────────────────────────────
STATE = {
    "cycle": 0, "phase": "Waiting to start…", "running": False,
    "last_started": None, "last_finished": None, "next_run": None, "last_error": None,
    "current": 0, "total": 0, "current_title": "", "current_url": "", "current_status": "",
    "titles_new": 0, "titles_updated": 0, "titles_skipped": 0,
    "episodes_new": 0, "servers_new": 0, "errors": 0,
    "cycles": [], "feed": deque(maxlen=60), "new_titles": deque(maxlen=100),
}
_lock = threading.Lock()

def _feed(msg: str, kind: str = "info"):
    with _lock:
        STATE["feed"].appendleft({"t": _nowt(), "msg": msg, "kind": kind})

# ─────────────────────────────────────────────────────────────────────────────
# HTML dashboard
# ─────────────────────────────────────────────────────────────────────────────
_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta http-equiv="refresh" content="15"/>
<title>Senpai TV — Scraper Dashboard</title>
<style>
:root{--bg:#0d0d14;--surface:#13131f;--card:#1a1a2e;--border:#2a2a42;
  --purple:#7c3aed;--pl:#a78bfa;--pink:#ec4899;
  --green:#22c55e;--yellow:#eab308;--red:#ef4444;--blue:#3b82f6;
  --text:#e2e8f0;--muted:#64748b;--white:#fff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
header{background:linear-gradient(135deg,#1a0533 0%,#0d0d14 60%);
  border-bottom:1px solid var(--border);padding:18px 32px;
  display:flex;align-items:center;gap:16px}
.logo{width:42px;height:42px;border-radius:10px;
  background:linear-gradient(135deg,var(--purple),var(--pink));
  display:flex;align-items:center;justify-content:center;
  font-size:22px;font-weight:900;color:#fff;flex-shrink:0}
.brand{font-size:22px;font-weight:800;color:var(--white)}
.brand span{color:var(--pl)}
.sub{font-size:12px;color:var(--muted);margin-top:2px}
header .r{margin-left:auto;text-align:right}
.badge{display:inline-flex;align-items:center;gap:6px;
  background:#0f2d1a;border:1px solid var(--green);
  color:var(--green);font-size:11px;font-weight:700;
  padding:4px 10px;border-radius:20px;letter-spacing:.5px}
.badge.idle{background:#1a1a0f;border-color:var(--yellow);color:var(--yellow)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;animation:p 1.5s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.st{font-size:11px;color:var(--muted);margin-top:4px}
main{padding:24px 32px;max-width:1400px;margin:0 auto}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:14px;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;
  padding:16px 18px;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;inset:0;
  background:linear-gradient(135deg,var(--c,var(--purple)) 0%,transparent 60%);opacity:.08}
.card .lbl{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}
.card .val{font-size:28px;font-weight:800;color:var(--white);line-height:1}
.card .s{font-size:11px;color:var(--muted);margin-top:4px}
.sec{background:var(--card);border:1px solid var(--border);border-radius:12px;
  margin-bottom:20px;overflow:hidden}
.sh{padding:14px 20px;background:linear-gradient(90deg,rgba(124,58,237,.15),transparent);
  border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.st2{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--pl)}
.sb{padding:18px 20px}
.phase{font-size:13px;color:var(--muted);margin-bottom:10px}
.phase strong{color:var(--pl)}
.pw{background:#0a0a14;border-radius:8px;height:10px;overflow:hidden;margin:10px 0}
.pb{height:100%;border-radius:8px;
  background:linear-gradient(90deg,var(--purple),var(--pink));
  transition:width .4s;box-shadow:0 0 12px rgba(124,58,237,.6)}
.pi{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-top:6px}
.ci{background:#0a0a14;border:1px solid var(--border);border-radius:8px;
  padding:10px 14px;margin-top:12px;font-size:13px;
  display:flex;align-items:center;gap:10px}
.ci-i{font-size:18px}.ci-t{color:var(--white);font-weight:600}
.ci-u{font-size:11px;color:var(--muted);margin-top:2px;word-break:break-all}
.ci-s{margin-left:auto;font-size:11px;padding:3px 8px;border-radius:12px;white-space:nowrap}
.ci-s.new{background:#0f2d1a;color:var(--green)}
.ci-s.upd{background:#1a1a0f;color:var(--yellow)}
.ci-s.err{background:#2d0f0f;color:var(--red)}
.ci-s.oth{background:#1a1a2e;color:var(--muted)}
.meta{margin-top:14px;display:flex;gap:24px;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.meta strong{color:var(--text)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}
@media(max-width:900px){.two{grid-template-columns:1fr}}
.fl{list-style:none;max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:4px}
.fl::-webkit-scrollbar{width:4px}
.fl::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
.fi{display:flex;align-items:flex-start;gap:8px;
  font-size:12px;padding:6px 8px;border-radius:6px;background:#0a0a14}
.ft{color:var(--muted);white-space:nowrap;font-size:11px;padding-top:1px}
.fm{color:var(--text);flex:1}
.fi.new .fm{color:var(--green)}.fi.error .fm{color:var(--red)}.fi.warn .fm{color:var(--yellow)}
.tl{list-style:none;max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
.ti{display:flex;align-items:center;gap:10px;
  background:#0a0a14;border-radius:8px;padding:8px 12px;font-size:12px}
.tb{font-size:10px;padding:2px 7px;border-radius:10px;white-space:nowrap;font-weight:700}
.tb.series{background:rgba(124,58,237,.25);color:var(--pl)}
.tb.movie{background:rgba(236,72,153,.25);color:var(--pink)}
.tn{color:var(--white);font-weight:600;flex:1}
.te{color:var(--muted);font-size:11px;white-space:nowrap}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:8px 12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.6px;font-size:10px;
  border-bottom:1px solid var(--border)}
td{padding:8px 12px;border-bottom:1px solid rgba(42,42,66,.4)}
tr:last-child td{border-bottom:none}
.ok{color:var(--green)}.warn{color:var(--yellow)}.err2{color:var(--red)}
.empty{color:var(--muted);font-size:13px;text-align:center;padding:24px 0;opacity:.6}
</style></head><body>
<header>
  <div class="logo">S</div>
  <div><div class="brand">Senpai <span>TV</span></div>
    <div class="sub">Content Scraper Dashboard</div></div>
  <div class="r">
    <div class="badge %%BADGE_CLS%%"><span class="dot"></span>%%BADGE_TXT%%</div>
    <div class="st">%%TIME%% UTC &nbsp;·&nbsp; Auto-refresh every 15 s</div>
  </div>
</header>
<main>
<div class="row">
  <div class="card" style="--c:var(--purple)"><div class="lbl">Cycle</div><div class="val">%%CYCLE%%</div><div class="s">Total runs</div></div>
  <div class="card" style="--c:var(--green)"><div class="lbl">New Titles</div><div class="val">%%NEW%%</div><div class="s">This cycle</div></div>
  <div class="card" style="--c:var(--blue)"><div class="lbl">Updated</div><div class="val">%%UPD%%</div><div class="s">This cycle</div></div>
  <div class="card" style="--c:var(--muted)"><div class="lbl">Skipped</div><div class="val">%%SKIP%%</div><div class="s">Already up to date</div></div>
  <div class="card" style="--c:var(--pink)"><div class="lbl">Episodes</div><div class="val">%%EP%%</div><div class="s">Added this cycle</div></div>
  <div class="card" style="--c:var(--yellow)"><div class="lbl">Servers</div><div class="val">%%SRV%%</div><div class="s">Stored this cycle</div></div>
  <div class="card" style="--c:var(--red)"><div class="lbl">Errors</div><div class="val">%%ERR%%</div><div class="s">This cycle</div></div>
</div>
<div class="sec">
  <div class="sh"><span style="font-size:16px">⚙️</span><span class="st2">Current Progress</span></div>
  <div class="sb">
    <div class="phase">Phase: <strong>%%PHASE%%</strong></div>
    <div class="pw"><div class="pb" style="width:%%PCT%%%"></div></div>
    <div class="pi"><span>%%CUR%% / %%TOT%% titles</span><span>%%PCT%%%</span></div>
    %%CI%%
    <div class="meta">
      <span>🕐 Started: <strong>%%STARTED%%</strong></span>
      <span>✅ Last done: <strong>%%DONE%%</strong></span>
      <span>⏰ Next run: <strong>%%NEXT%%</strong></span>
    </div>
  </div>
</div>
<div class="two">
  <div class="sec">
    <div class="sh"><span style="font-size:16px">📡</span><span class="st2">Live Activity Feed</span></div>
    <div class="sb">%%FEED%%</div>
  </div>
  <div class="sec">
    <div class="sh"><span style="font-size:16px">🆕</span><span class="st2">New Titles This Cycle</span></div>
    <div class="sb">%%NTITLES%%</div>
  </div>
</div>
<div class="sec">
  <div class="sh"><span style="font-size:16px">📋</span><span class="st2">Cycle History</span></div>
  <div class="sb">%%HIST%%</div>
</div>
</main></body></html>"""


def _build_html() -> bytes:
    with _lock:
        s = dict(STATE)
        feed = list(s["feed"])
        nt   = list(s["new_titles"])
        cyc  = list(s["cycles"])

    running = s["running"]
    pct = int(s["current"] / s["total"] * 100) if s["total"] else 0

    # current item
    ci = ""
    if s["current_title"] and running:
        st = s["current_status"] or "processing"
        sc = "new" if "new" in st else ("err" if "error" in st else ("upd" if "upd" in st else "oth"))
        ic = "🆕" if sc == "new" else ("❌" if sc == "err" else ("✏️" if sc == "upd" else "⚙️"))
        ci = (f'<div class="ci"><span class="ci-i">{ic}</span>'
              f'<div><div class="ci-t">{s["current_title"]}</div>'
              f'<div class="ci-u">{s["current_url"]}</div></div>'
              f'<span class="ci-s {sc}">{st}</span></div>')

    # feed
    if feed:
        items = "".join(
            f'<li class="fi {e["kind"]}"><span class="ft">{e["t"]}</span>'
            f'<span class="fm">{e["msg"]}</span></li>' for e in feed)
        feed_html = f'<ul class="fl">{items}</ul>'
    else:
        feed_html = '<div class="empty">No activity yet — scraper starting soon…</div>'

    # new titles
    if nt:
        items = "".join(
            f'<li class="ti"><span class="tb {t["type"]}">{t["type"].upper()}</span>'
            f'<span class="tn">{t["title"]}</span>'
            f'<span class="te">{t["episodes"]} ep</span></li>' for t in nt)
        nt_html = f'<ul class="tl">{items}</ul>'
    else:
        nt_html = '<div class="empty">No new titles found yet this cycle</div>'

    # history
    if cyc:
        rows = "".join(
            f'<tr><td>#{c["cycle"]}</td><td>{c["started"]}</td><td>{c["duration"]}</td>'
            f'<td class="ok">+{c["new"]}</td><td>{c["updated"]}</td><td>{c["ep_new"]}</td>'
            f'<td class="{"err2" if c["errors"] else "ok"}">{c["errors"]}</td></tr>'
            for c in reversed(cyc))
        hist_html = (f'<table><thead><tr>'
                     f'<th>Cycle</th><th>Started</th><th>Duration</th>'
                     f'<th>New</th><th>Updated</th><th>Episodes</th><th>Errors</th>'
                     f'</tr></thead><tbody>{rows}</tbody></table>')
    else:
        hist_html = '<div class="empty">No completed cycles yet</div>'

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    html = (_HTML
        .replace("%%BADGE_CLS%%", "badge" if running else "badge idle")
        .replace("%%BADGE_TXT%%", "SCRAPING" if running else "IDLE")
        .replace("%%TIME%%", now_str)
        .replace("%%CYCLE%%", str(s["cycle"]))
        .replace("%%NEW%%",  str(s["titles_new"]))
        .replace("%%UPD%%",  str(s["titles_updated"]))
        .replace("%%SKIP%%", str(s["titles_skipped"]))
        .replace("%%EP%%",   str(s["episodes_new"]))
        .replace("%%SRV%%",  str(s["servers_new"]))
        .replace("%%ERR%%",  str(s["errors"]))
        .replace("%%PHASE%%", s["phase"])
        .replace("%%PCT%%",  str(pct))
        .replace("%%CUR%%",  str(s["current"]))
        .replace("%%TOT%%",  str(s["total"]))
        .replace("%%CI%%",   ci)
        .replace("%%STARTED%%", s["last_started"] or "—")
        .replace("%%DONE%%",    s["last_finished"] or "—")
        .replace("%%NEXT%%",    s["next_run"] or "—")
        .replace("%%FEED%%",    feed_html)
        .replace("%%NTITLES%%", nt_html)
        .replace("%%HIST%%",    hist_html))
    return html.encode()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP server
# ─────────────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
            with _lock:
                body = json.dumps({k: v for k, v in STATE.items()
                                   if k not in ("feed", "new_titles", "cycles")},
                                  default=str).encode()
            ct = "application/json"
        else:
            body = _build_html()
            ct = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass


# ─────────────────────────────────────────────────────────────────────────────
# Scraper loop (background thread)
# ─────────────────────────────────────────────────────────────────────────────
def scraper_loop():
    time.sleep(3)

    try:
        import pipeline
    except Exception as e:
        _feed(f"FATAL: cannot import pipeline.py — {e}", "error")
        print(f"[scraper] Import error: {e}", flush=True)
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
            STATE["last_error"] = None
            STATE["last_started"] = _now()
            STATE["new_titles"].clear()
            cycle = STATE["cycle"]

        _feed(f"Cycle #{cycle} started", "info")

        # Telegram: cycle start
        try:
            from supabase import create_client as _cc
            _db = _cc(os.environ.get("SUPABASE_URL",""), os.environ.get("SUPABASE_SERVICE_KEY",""))
            _c = _db.table("content").select("id", count="exact", head=True).execute()
            _e = _db.table("episodes").select("id", count="exact", head=True).execute()
            tg_cycle_start(cycle, _c.count or 0, _e.count or 0)
        except Exception:
            tg_cycle_start(cycle, 0, 0)

        t0 = time.time()
        _tg_prog_counter = [0]

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
            kind = "new" if status == "new" else ("error" if "error" in status else "info")
            if current % 25 == 0 or status == "new":
                _feed(f"[{current}/{total}] {title} — {status}", kind)
            _tg_prog_counter[0] += 1
            if _tg_prog_counter[0] % 50 == 0:
                tg_progress(cycle, current, total, title)

        def new_title_hook(title, ctype, episodes):
            with _lock:
                STATE["new_titles"].appendleft({"title": title, "type": ctype, "episodes": episodes})
            _feed(f"NEW: {title} ({ctype}, {episodes} ep)", "new")
            tg_new_title(title, ctype, episodes)

        # Run
        try:
            with _lock: STATE["phase"] = "Discovering content…"
            _feed("Discovering all content via sitemap + categories…", "info")
            pipeline.STATS = pipeline.Stats()
            pipeline.run(progress_hook=progress_hook, new_title_hook=new_title_hook)
            with _lock:
                STATE["phase"] = "Cycle complete ✓"
                STATE["last_finished"] = _now()
        except SystemExit:
            with _lock:
                STATE["phase"] = "Interrupted"
                STATE["last_finished"] = _now()
            _feed(f"Cycle #{cycle} interrupted", "warn")
        except Exception:
            err = traceback.format_exc().strip().splitlines()[-1]
            with _lock:
                STATE["last_error"] = err
                STATE["phase"] = f"Error: {err[:60]}"
                STATE["last_finished"] = _now()
            _feed(f"ERROR: {err}", "error")
            tg_error(cycle, "scraper loop", err)

        elapsed = int(time.time() - t0)
        h, m, s2 = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        elapsed_str = f"{h:02d}:{m:02d}:{s2:02d}"

        wake_at = datetime.fromtimestamp(time.time() + INTERVAL_HOURS * 3600, tz=timezone.utc)
        nxt = wake_at.strftime("%Y-%m-%d %H:%M UTC")

        with _lock:
            STATE["running"] = False
            STATE["next_run"] = nxt
            st = pipeline.STATS
            STATE["cycles"].append({
                "cycle": cycle, "started": STATE["last_started"], "duration": elapsed_str,
                "new": st.content_new, "updated": st.content_updated,
                "ep_new": st.episodes_new, "errors": st.errors,
            })
            if len(STATE["cycles"]) > 10:
                STATE["cycles"].pop(0)

        tg_done(cycle, st.content_new, st.content_updated, st.content_skipped,
                st.episodes_new, st.servers_new, st.errors, elapsed_str, nxt)
        _feed(f"Sleeping {INTERVAL_HOURS:.0f} h — next: {nxt}", "info")
        print(f"[scraper] Cycle #{cycle} done in {elapsed_str}. Next: {nxt}", flush=True)
        time.sleep(INTERVAL_HOURS * 3600)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[senpai-tv] Starting on port {PORT}", flush=True)
    threading.Thread(target=scraper_loop, name="scraper", daemon=False).start()
    try:
        srv = HTTPServer(("0.0.0.0", PORT), Handler)
        print(f"[senpai-tv] Dashboard ready → http://0.0.0.0:{PORT}", flush=True)
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
