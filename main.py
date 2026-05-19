#!/usr/bin/env python3
"""
AnimeSalt Scraper — Render.com entry point
Runs the scraper daemon in a background thread.
Also starts a tiny HTTP server so Render's health check passes
and UptimeRobot can ping it to prevent sleep.
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

PORT = int(os.environ.get("PORT", 10000))

# ── tiny status tracker shared between threads ────────────────
STATUS = {
    "cycle": 0,
    "last_started": None,
    "last_finished": None,
    "last_error": None,
    "running": False,
}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            f"AnimeSalt Scraper — alive\n"
            f"Cycle:         {STATUS['cycle']}\n"
            f"Scraping now:  {STATUS['running']}\n"
            f"Last started:  {STATUS['last_started'] or 'not yet'}\n"
            f"Last finished: {STATUS['last_finished'] or 'not yet'}\n"
            f"Last error:    {STATUS['last_error'] or 'none'}\n"
            f"Server time:   {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # silence request logs


def run_http():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[health] Listening on port {PORT}", flush=True)
    server.serve_forever()


def run_scraper_loop():
    """Import and run the scraper pipeline in an infinite loop."""
    # Import here so HTTP server starts before any scraper output
    import pipeline

    INTERVAL_HOURS = float(os.environ.get("SCRAPE_INTERVAL_HOURS", "6"))

    while True:
        STATUS["cycle"] += 1
        STATUS["running"] = True
        STATUS["last_started"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        STATUS["last_error"] = None

        print(
            f"\n{'='*60}\n  SCRAPER CYCLE #{STATUS['cycle']} — {STATUS['last_started']}\n{'='*60}",
            flush=True,
        )

        try:
            pipeline.STATS = pipeline.Stats()   # reset stats each cycle
            pipeline.run()
            STATUS["last_finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception as e:
            STATUS["last_error"] = str(e)
            print(f"[scraper] ERROR in cycle #{STATUS['cycle']}: {e}", flush=True)

        STATUS["running"] = False

        sleep_secs = INTERVAL_HOURS * 3600
        wake_at = datetime.utcfromtimestamp(time.time() + sleep_secs)
        print(
            f"[scraper] Cycle #{STATUS['cycle']} done. "
            f"Next run at {wake_at.strftime('%Y-%m-%d %H:%M:%S UTC')} "
            f"({INTERVAL_HOURS:.0f} h)",
            flush=True,
        )
        time.sleep(sleep_secs)


if __name__ == "__main__":
    # Start HTTP health server in background
    t_http = threading.Thread(target=run_http, daemon=True)
    t_http.start()

    # Run scraper loop in main thread
    run_scraper_loop()
