#!/usr/bin/env python3
"""
Senpai TV — Content Scraper Service
Render.com entry point: HTTP dashboard on main thread, scraper on background thread.
"""

import os
import sys
import json
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from collections import deque

PORT = int(os.environ.get("PORT", 10000))
INTERVAL_HOURS = float(os.environ.get("SCRAPE_INTERVAL_HOURS", "6"))

# ── Shared live state ──────────────────────────────────────────────────────────
STATE = {
    "cycle": 0,
    "phase": "Waiting to start…",
    "running": False,
    "last_started": None,
    "last_finished": None,
    "next_run": None,
    "last_error": None,
    # progress
    "current": 0,
    "total": 0,
    "current_title": "",
    "current_url": "",
    "current_status": "",
    # cumulative stats
    "titles_new": 0,
    "titles_updated": 0,
    "titles_skipped": 0,
    "episodes_new": 0,
    "servers_new": 0,
    "errors": 0,
    # history of recent cycles
    "cycles": [],          # list of dicts, last 10
    # recent activity feed
    "feed": deque(maxlen=50),
    # new titles this cycle
    "new_titles": deque(maxlen=100),
}
_lock = threading.Lock()


def _feed(msg: str, kind: str = "info"):
    with _lock:
        STATE["feed"].appendleft({
            "t": datetime.utcnow().strftime("%H:%M:%S"),
            "msg": msg,
            "kind": kind,
        })


# ── Dashboard HTML ─────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta http-equiv="refresh" content="15"/>
<title>Senpai TV — Scraper Dashboard</title>
<style>
  :root{
    --bg:#0d0d14;--surface:#13131f;--card:#1a1a2e;--border:#2a2a42;
    --purple:#7c3aed;--purple-light:#a78bfa;--pink:#ec4899;
    --green:#22c55e;--yellow:#eab308;--red:#ef4444;--blue:#3b82f6;
    --text:#e2e8f0;--muted:#64748b;--white:#fff;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}

  /* header */
  header{
    background:linear-gradient(135deg,#1a0533 0%,#0d0d14 60%);
    border-bottom:1px solid var(--border);
    padding:18px 32px;display:flex;align-items:center;gap:16px;
  }
  .logo{
    width:42px;height:42px;border-radius:10px;
    background:linear-gradient(135deg,var(--purple),var(--pink));
    display:flex;align-items:center;justify-content:center;
    font-size:22px;font-weight:900;color:#fff;letter-spacing:-1px;flex-shrink:0;
  }
  .brand{font-size:22px;font-weight:800;color:var(--white)}
  .brand span{color:var(--purple-light)}
  .subtitle{font-size:12px;color:var(--muted);margin-top:2px}
  header .right{margin-left:auto;text-align:right}
  .live-badge{
    display:inline-flex;align-items:center;gap:6px;
    background:#0f2d1a;border:1px solid var(--green);
    color:var(--green);font-size:11px;font-weight:700;
    padding:4px 10px;border-radius:20px;letter-spacing:.5px;
  }
  .live-badge.idle{background:#1a1a0f;border-color:var(--yellow);color:var(--yellow)}
  .dot{width:7px;height:7px;border-radius:50%;background:currentColor;animation:pulse 1.5s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  .server-time{font-size:11px;color:var(--muted);margin-top:4px}

  /* layout */
  main{padding:24px 32px;max-width:1400px;margin:0 auto}

  /* stat cards row */
  .stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:24px}
  .stat-card{
    background:var(--card);border:1px solid var(--border);border-radius:12px;
    padding:16px 18px;position:relative;overflow:hidden;
  }
  .stat-card::before{
    content:'';position:absolute;inset:0;
    background:linear-gradient(135deg,var(--c1,var(--purple)) 0%,transparent 60%);
    opacity:.08;
  }
  .stat-card .label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}
  .stat-card .value{font-size:28px;font-weight:800;color:var(--white);line-height:1}
  .stat-card .sub{font-size:11px;color:var(--muted);margin-top:4px}

  /* progress section */
  .section{background:var(--card);border:1px solid var(--border);border-radius:12px;margin-bottom:20px;overflow:hidden}
  .section-header{
    padding:14px 20px;background:linear-gradient(90deg,rgba(124,58,237,.15),transparent);
    border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;
  }
  .section-title{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--purple-light)}
  .section-body{padding:18px 20px}

  .phase-label{font-size:13px;color:var(--muted);margin-bottom:10px}
  .phase-label strong{color:var(--purple-light)}

  .progress-wrap{background:#0a0a14;border-radius:8px;height:10px;overflow:hidden;margin:10px 0}
  .progress-bar{
    height:100%;border-radius:8px;
    background:linear-gradient(90deg,var(--purple),var(--pink));
    transition:width .4s ease;
    box-shadow:0 0 12px rgba(124,58,237,.6);
  }
  .progress-info{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-top:6px}

  .current-item{
    background:#0a0a14;border:1px solid var(--border);border-radius:8px;
    padding:10px 14px;margin-top:12px;font-size:13px;
    display:flex;align-items:center;gap:10px;
  }
  .current-item .ci-icon{font-size:18px}
  .ci-title{color:var(--white);font-weight:600}
  .ci-url{font-size:11px;color:var(--muted);margin-top:2px;word-break:break-all}
  .ci-status{margin-left:auto;font-size:11px;padding:3px 8px;border-radius:12px;white-space:nowrap}
  .ci-status.new{background:#0f2d1a;color:var(--green)}
  .ci-status.updated{background:#1a1a0f;color:var(--yellow)}
  .ci-status.error{background:#2d0f0f;color:var(--red)}
  .ci-status.other{background:#1a1a2e;color:var(--muted)}

  /* two-col layout */
  .two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}
  @media(max-width:900px){.two-col{grid-template-columns:1fr}}

  /* feed */
  .feed-list{list-style:none;max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:4px}
  .feed-list::-webkit-scrollbar{width:4px}
  .feed-list::-webkit-scrollbar-track{background:transparent}
  .feed-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
  .feed-item{
    display:flex;align-items:flex-start;gap:8px;
    font-size:12px;padding:6px 8px;border-radius:6px;background:#0a0a14;
  }
  .feed-time{color:var(--muted);white-space:nowrap;font-size:11px;padding-top:1px}
  .feed-msg{color:var(--text);flex:1}
  .feed-item.new .feed-msg{color:var(--green)}
  .feed-item.error .feed-msg{color:var(--red)}
  .feed-item.warn .feed-msg{color:var(--yellow)}

  /* new titles */
  .titles-list{list-style:none;max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
  .title-item{
    display:flex;align-items:center;gap:10px;
    background:#0a0a14;border-radius:8px;padding:8px 12px;font-size:12px;
  }
  .title-badge{
    font-size:10px;padding:2px 7px;border-radius:10px;white-space:nowrap;font-weight:700;
  }
  .title-badge.series{background:rgba(124,58,237,.25);color:var(--purple-light)}
  .title-badge.movie{background:rgba(236,72,153,.25);color:var(--pink)}
  .title-name{color:var(--white);font-weight:600;flex:1}
  .title-eps{color:var(--muted);font-size:11px;white-space:nowrap}

  /* cycle history */
  .history-table{width:100%;border-collapse:collapse;font-size:12px}
  .history-table th{
    text-align:left;padding:8px 12px;color:var(--muted);
    text-transform:uppercase;letter-spacing:.6px;font-size:10px;
    border-bottom:1px solid var(--border);
  }
  .history-table td{padding:8px 12px;border-bottom:1px solid rgba(42,42,66,.4)}
  .history-table tr:last-child td{border-bottom:none}
  .ok{color:var(--green)}.warn{color:var(--yellow)}.err{color:var(--red)}

  .empty{color:var(--muted);font-size:13px;text-align:center;padding:24px 0;opacity:.6}
  .auto-refresh{font-size:11px;color:var(--muted);text-align:center;padding:12px 0}
</style>
</head>
<body>
<header>
  <div class="logo">S</div>
  <div>
    <div class="brand">Senpai <span>TV</span></div>
    <div class="subtitle">Content Scraper Dashboard</div>
  </div>
  <div class="right">
    <div class="live-badge {BADGE_CLASS}"><span class="dot"></span>{BADGE_TEXT}</div>
    <div class="server-time">{SERVER_TIME} UTC &nbsp;·&nbsp; Auto-refresh every 15 s</div>
  </div>
</header>

<main>

<!-- Stat cards -->
<div class="stats-row">
  <div class="stat-card" style="--c1:var(--purple)">
    <div class="label">Cycle</div>
    <div class="value">{CYCLE}</div>
    <div class="sub">Total runs</div>
  </div>
  <div class="stat-card" style="--c1:var(--green)">
    <div class="label">New Titles</div>
    <div class="value">{TITLES_NEW}</div>
    <div class="sub">This cycle</div>
  </div>
  <div class="stat-card" style="--c1:var(--blue)">
    <div class="label">Updated</div>
    <div class="value">{TITLES_UPD}</div>
    <div class="sub">This cycle</div>
  </div>
  <div class="stat-card" style="--c1:var(--muted)">
    <div class="label">Skipped</div>
    <div class="value">{TITLES_SKIP}</div>
    <div class="sub">Already up to date</div>
  </div>
  <div class="stat-card" style="--c1:var(--pink)">
    <div class="label">Episodes</div>
    <div class="value">{EP_NEW}</div>
    <div class="sub">Added this cycle</div>
  </div>
  <div class="stat-card" style="--c1:var(--yellow)">
    <div class="label">Servers</div>
    <div class="value">{SRV_NEW}</div>
    <div class="sub">Stored this cycle</div>
  </div>
  <div class="stat-card" style="--c1:var(--red)">
    <div class="label">Errors</div>
    <div class="value">{ERRORS}</div>
    <div class="sub">This cycle</div>
  </div>
</div>

<!-- Current progress -->
<div class="section">
  <div class="section-header">
    <span style="font-size:16px">⚙️</span>
    <span class="section-title">Current Progress</span>
  </div>
  <div class="section-body">
    <div class="phase-label">Phase: <strong>{PHASE}</strong></div>
    <div class="progress-wrap"><div class="progress-bar" style="width:{PCT}%"></div></div>
    <div class="progress-info">
      <span>{CURRENT} / {TOTAL} titles</span>
      <span>{PCT}%</span>
    </div>
    {CURRENT_ITEM_HTML}
    <div style="margin-top:14px;display:flex;gap:24px;font-size:12px;color:var(--muted)">
      <span>🕐 Started: <strong style="color:var(--text)">{LAST_STARTED}</strong></span>
      <span>✅ Last done: <strong style="color:var(--text)">{LAST_FINISHED}</strong></span>
      <span>⏰ Next run: <strong style="color:var(--text)">{NEXT_RUN}</strong></span>
    </div>
  </div>
</div>

<div class="two-col">

  <!-- Activity feed -->
  <div class="section">
    <div class="section-header">
      <span style="font-size:16px">📡</span>
      <span class="section-title">Live Activity Feed</span>
    </div>
    <div class="section-body">
      {FEED_HTML}
    </div>
  </div>

  <!-- New titles this cycle -->
  <div class="section">
    <div class="section-header">
      <span style="font-size:16px">🆕</span>
      <span class="section-title">New Titles This Cycle</span>
    </div>
    <div class="section-body">
      {NEW_TITLES_HTML}
    </div>
  </div>

</div>

<!-- Cycle history -->
<div class="section">
  <div class="section-header">
    <span style="font-size:16px">📋</span>
    <span class="section-title">Cycle History</span>
  </div>
  <div class="section-body">
    {HISTORY_HTML}
  </div>
</div>

</main>
</body>
</html>"""


def _build_html() -> bytes:
    with _lock:
        s = dict(STATE)
        feed = list(s["feed"])
        new_titles = list(s["new_titles"])
        cycles = list(s["cycles"])

    running = s["running"]
    pct = int(s["current"] / s["total"] * 100) if s["total"] else 0

    badge_class = "live-badge" if running else "live-badge idle"
    badge_text = "SCRAPING" if running else "IDLE"

    # Current item block
    if s["current_title"] and running:
        st = s["current_status"] or "processing"
        if "new" in st:       sc = "new";    si = "🆕"
        elif "error" in st:   sc = "error";  si = "❌"
        elif "skip" in st:    sc = "other";  si = "⏭"
        else:                  sc = "updated"; si = "✏️"
        current_item_html = (
            f'<div class="current-item">'
            f'<span class="ci-icon">{si}</span>'
            f'<div><div class="ci-title">{s["current_title"]}</div>'
            f'<div class="ci-url">{s["current_url"]}</div></div>'
            f'<span class="ci-status {sc}">{st}</span>'
            f'</div>'
        )
    else:
        current_item_html = ""

    # Feed
    if feed:
        items = "".join(
            f'<li class="feed-item {e["kind"]}">'
            f'<span class="feed-time">{e["t"]}</span>'
            f'<span class="feed-msg">{e["msg"]}</span></li>'
            for e in feed
        )
        feed_html = f'<ul class="feed-list">{items}</ul>'
    else:
        feed_html = '<div class="empty">No activity yet — scraper starting soon…</div>'

    # New titles
    if new_titles:
        items = "".join(
            f'<li class="title-item">'
            f'<span class="title-badge {t["type"]}">{t["type"].upper()}</span>'
            f'<span class="title-name">{t["title"]}</span>'
            f'<span class="title-eps">{t["episodes"]} ep</span></li>'
            for t in new_titles
        )
        new_titles_html = f'<ul class="titles-list">{items}</ul>'
    else:
        new_titles_html = '<div class="empty">No new titles found yet this cycle</div>'

    # History table
    if cycles:
        rows = "".join(
            f'<tr>'
            f'<td>#{c["cycle"]}</td>'
            f'<td>{c["started"]}</td>'
            f'<td>{c["duration"]}</td>'
            f'<td class="ok">+{c["new"]}</td>'
            f'<td>{c["updated"]}</td>'
            f'<td>{c["ep_new"]}</td>'
            f'<td class="{"err" if c["errors"] else "ok"}">{c["errors"]}</td>'
            f'</tr>'
            for c in reversed(cycles)
        )
        history_html = (
            f'<table class="history-table">'
            f'<thead><tr>'
            f'<th>Cycle</th><th>Started</th><th>Duration</th>'
            f'<th>New</th><th>Updated</th><th>Episodes</th><th>Errors</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>'
        )
    else:
        history_html = '<div class="empty">No completed cycles yet</div>'

    html = HTML.replace("{BADGE_CLASS}", badge_class) \
               .replace("{BADGE_TEXT}", badge_text) \
               .replace("{SERVER_TIME}", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")) \
               .replace("{CYCLE}", str(s["cycle"])) \
               .replace("{TITLES_NEW}", str(s["titles_new"])) \
               .replace("{TITLES_UPD}", str(s["titles_updated"])) \
               .replace("{TITLES_SKIP}", str(s["titles_skipped"])) \
               .replace("{EP_NEW}", str(s["episodes_new"])) \
               .replace("{SRV_NEW}", str(s["servers_new"])) \
               .replace("{ERRORS}", str(s["errors"])) \
               .replace("{PHASE}", s["phase"]) \
               .replace("{PCT}", str(pct)) \
               .replace("{CURRENT}", str(s["current"])) \
               .replace("{TOTAL}", str(s["total"])) \
               .replace("{CURRENT_ITEM_HTML}", current_item_html) \
               .replace("{LAST_STARTED}", s["last_started"] or "—") \
               .replace("{LAST_FINISHED}", s["last_finished"] or "—") \
               .replace("{NEXT_RUN}", s["next_run"] or "—") \
               .replace("{FEED_HTML}", feed_html) \
               .replace("{NEW_TITLES_HTML}", new_titles_html) \
               .replace("{HISTORY_HTML}", history_html)

    return html.encode()


# ── HTTP handler ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
            with _lock:
                body = json.dumps({
                    "cycle": STATE["cycle"],
                    "running": STATE["running"],
                    "phase": STATE["phase"],
                    "progress": f"{STATE['current']}/{STATE['total']}",
                    "current_title": STATE["current_title"],
                    "last_started": STATE["last_started"],
                    "last_finished": STATE["last_finished"],
                    "next_run": STATE["next_run"],
                    "titles_new": STATE["titles_new"],
                    "errors": STATE["errors"],
                }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = _build_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


# ── Scraper loop ───────────────────────────────────────────────────────────────
def scraper_loop():
    time.sleep(3)

    try:
        import pipeline
        import telegram_bot as tg
    except Exception as e:
        _feed(f"Import error: {e}", "error")
        return

    while True:
        with _lock:
            STATE["cycle"] += 1
            STATE["running"] = True
            STATE["phase"] = "Starting…"
            STATE["current"] = 0
            STATE["total"] = 0
            STATE["current_title"] = ""
            STATE["titles_new"] = 0
            STATE["titles_updated"] = 0
            STATE["titles_skipped"] = 0
            STATE["episodes_new"] = 0
            STATE["servers_new"] = 0
            STATE["errors"] = 0
            STATE["last_error"] = None
            STATE["last_started"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            list(STATE["new_titles"])  # keep reference
            STATE["new_titles"].clear()
            cycle = STATE["cycle"]

        _feed(f"Cycle #{cycle} started", "info")

        # Get DB counts for Telegram
        try:
            from supabase import create_client
            _db = create_client(
                os.environ.get("SUPABASE_URL", ""),
                os.environ.get("SUPABASE_SERVICE_KEY", "")
            )
            _cnt = _db.table("content").select("id", count="exact", head=True).execute()
            _ep  = _db.table("episodes").select("id", count="exact", head=True).execute()
            tg.notify_cycle_start(cycle, _cnt.count or 0, _ep.count or 0)
        except Exception:
            tg.notify_cycle_start(cycle, 0, 0)

        cycle_start = time.time()

        # ── Progress hook ──────────────────────────────────────────────────────
        def progress_hook(current, total, title, url, status):
            with _lock:
                if total > 0:
                    STATE["phase"] = "Scraping content"
                else:
                    STATE["phase"] = "Discovering content"
                STATE["current"] = current
                STATE["total"] = total
                STATE["current_title"] = title
                STATE["current_url"] = url
                STATE["current_status"] = status
                STATE["titles_new"] = pipeline.STATS.content_new
                STATE["titles_updated"] = pipeline.STATS.content_updated
                STATE["titles_skipped"] = pipeline.STATS.content_skipped
                STATE["episodes_new"] = pipeline.STATS.episodes_new
                STATE["servers_new"] = pipeline.STATS.servers_new
                STATE["errors"] = pipeline.STATS.errors

            kind = "new" if status == "new" else ("error" if "error" in status else "info")
            if current % 50 == 0 or status == "new":
                _feed(f"[{current}/{total}] {title} — {status}", kind)

            # Telegram progress every 50 titles
            if current > 0 and current % 50 == 0:
                tg.notify_progress(cycle, current, total, title, status)

        # ── New title hook ─────────────────────────────────────────────────────
        def new_title_hook(title, content_type, episodes):
            with _lock:
                STATE["new_titles"].appendleft({
                    "title": title,
                    "type": content_type,
                    "episodes": episodes,
                })
            _feed(f"NEW: {title} ({content_type}, {episodes} ep)", "new")
            tg.notify_new_title(title, content_type, episodes)

        # ── Run ────────────────────────────────────────────────────────────────
        try:
            with _lock:
                STATE["phase"] = "Discovering content…"
            _feed("Discovering content via sitemap + categories…", "info")

            pipeline.STATS = pipeline.Stats()
            pipeline.run(
                progress_hook=progress_hook,
                new_title_hook=new_title_hook,
            )

            with _lock:
                STATE["phase"] = "Cycle complete ✓"
                STATE["last_finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

            elapsed = int(time.time() - cycle_start)
            h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
            elapsed_str = f"{h:02d}:{m:02d}:{s:02d}"

            # Save to history
            with _lock:
                STATE["cycles"].append({
                    "cycle": cycle,
                    "started": STATE["last_started"],
                    "duration": elapsed_str,
                    "new": pipeline.STATS.content_new,
                    "updated": pipeline.STATS.content_updated,
                    "ep_new": pipeline.STATS.episodes_new,
                    "errors": pipeline.STATS.errors,
                })
                if len(STATE["cycles"]) > 10:
                    STATE["cycles"].pop(0)

            _feed(f"Cycle #{cycle} complete — {elapsed_str} elapsed", "info")

        except SystemExit:
            with _lock:
                STATE["last_finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                STATE["phase"] = "Interrupted"
            _feed(f"Cycle #{cycle} interrupted (SystemExit)", "warn")
            elapsed_str = "—"

        except Exception:
            err = traceback.format_exc()
            short = err.strip().splitlines()[-1]
            with _lock:
                STATE["last_error"] = short
                STATE["last_finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                STATE["phase"] = f"Error: {short[:60]}"
            _feed(f"Cycle #{cycle} ERROR: {short}", "error")
            tg.notify_error(cycle, "scraper loop", short)
            elapsed_str = "—"

        STATE["running"] = False

        # Send Telegram summary
        wake_at = datetime.utcfromtimestamp(time.time() + INTERVAL_HOURS * 3600)
        next_run_str = wake_at.strftime("%Y-%m-%d %H:%M UTC")
        with _lock:
            STATE["next_run"] = next_run_str
            st = pipeline.STATS

        tg.notify_cycle_done(
            cycle,
            st.content_new, st.content_updated, st.content_skipped,
            st.episodes_new, st.servers_new, st.errors,
            elapsed_str, next_run_str,
        )

        _feed(f"Sleeping {INTERVAL_HOURS:.0f} h — next run {next_run_str}", "info")
        print(f"[scraper] Cycle #{cycle} done. Next: {next_run_str}", flush=True)
        time.sleep(INTERVAL_HOURS * 3600)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[senpai-tv] Starting dashboard on port {PORT}", flush=True)

    t = threading.Thread(target=scraper_loop, name="scraper", daemon=False)
    t.start()

    try:
        server = HTTPServer(("0.0.0.0", PORT), Handler)
        print(f"[senpai-tv] Dashboard ready → http://0.0.0.0:{PORT}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        print("[senpai-tv] Shutting down.", flush=True)
        sys.exit(0)
