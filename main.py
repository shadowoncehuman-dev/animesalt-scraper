#!/usr/bin/env python3
"""
AnimeSalt Scraper — Render.com entry point

Architecture:
  - Main thread  → HTTP health server (Render needs this to mark service healthy)
  - Background thread → scraper daemon loop

Render free services sleep after 15 min of no traffic.
Point UptimeRobot at your Render URL every 5 min to keep it awake 24/7.
"""

import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

PORT = int(os.environ.get("PORT", 10000))
INTERVAL_HOURS = float(os.environ.get("SCRAPE_INTERVAL_HOURS", "6"))

# ── Shared status (written by scraper thread, read by HTTP thread) ─────────────
STATUS = {
    "cycle": 0,
    "running": False,
    "last_started": "not yet",
    "last_finished": "not yet",
    "last_error": "none",
}


# ── Health-check HTTP server ───────────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            "AnimeSalt Scraper — alive\n"
            f"Cycle:           {STATUS['cycle']}\n"
            f"Scraping now:    {STATUS['running']}\n"
            f"Last started:    {STATUS['last_started']}\n"
            f"Last finished:   {STATUS['last_finished']}\n"
            f"Last error:      {STATUS['last_error']}\n"
            f"Next interval:   every {INTERVAL_HOURS:.0f} hours\n"
            f"Server time:     {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence access logs


# ── Scraper loop (runs in a background thread) ─────────────────────────────────
def scraper_loop():
    # Small delay so HTTP server is guaranteed to bind before first scrape output
    time.sleep(2)

    import pipeline  # imported here so any import error doesn't kill HTTP server

    while True:
        STATUS["cycle"] += 1
        STATUS["running"] = True
        STATUS["last_error"] = "none"
        STATUS["last_started"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        print(
            f"\n{'='*60}\n"
            f"  SCRAPER CYCLE #{STATUS['cycle']}  —  {STATUS['last_started']}\n"
            f"{'='*60}",
            flush=True,
        )

        try:
            pipeline.STATS = pipeline.Stats()  # fresh stats each cycle
            pipeline.run()
            STATUS["last_finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            STATUS["last_error"] = "none"

        except SystemExit:
            # pipeline.py calls sys.exit(0) on KeyboardInterrupt — catch it here
            # so only the scraper cycle ends, not the whole process
            STATUS["last_finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            STATUS["last_error"] = "cycle ended early (SystemExit)"

        except Exception:
            err = traceback.format_exc()
            STATUS["last_error"] = err.strip().splitlines()[-1]
            STATUS["last_finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            print(f"[scraper] ERROR in cycle #{STATUS['cycle']}:\n{err}", flush=True)

        STATUS["running"] = False

        wake_at = datetime.utcfromtimestamp(time.time() + INTERVAL_HOURS * 3600)
        print(
            f"[scraper] Cycle #{STATUS['cycle']} finished. "
            f"Next run at {wake_at.strftime('%Y-%m-%d %H:%M UTC')} "
            f"({INTERVAL_HOURS:.0f} h)",
            flush=True,
        )
        time.sleep(INTERVAL_HOURS * 3600)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[main] Starting scraper service on port {PORT}", flush=True)

    # Start scraper in background thread (non-daemon so it survives HTTP crashes)
    t = threading.Thread(target=scraper_loop, name="scraper", daemon=False)
    t.start()

    # Run HTTP server on main thread — Render marks the service healthy once this
    # starts responding, which happens immediately (before the first scrape starts)
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        print(f"[main] HTTP health server ready at http://0.0.0.0:{PORT}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        print("[main] Shutting down.", flush=True)
        sys.exit(0)
