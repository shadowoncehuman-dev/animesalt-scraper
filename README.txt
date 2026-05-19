╔══════════════════════════════════════════════════════════════════╗
║      AnimeSalt Scraper — Render.com Free Deployment Guide        ║
╚══════════════════════════════════════════════════════════════════╝

HOW IT WORKS
────────────
  main.py runs two things at the same time:
    1. The AnimeSalt scraper (full scrape → sleep 6 h → repeat forever)
    2. A tiny web server on the port Render assigns

  Render's free services sleep after 15 min of no traffic.
  UptimeRobot (free) pings your scraper URL every 5 minutes
  → Render never sleeps → scraper runs 24/7 for free.

YOUR SUPABASE SECRETS
──────────────────────
  Find them in: Supabase dashboard → Project Settings → API

  SUPABASE_URL         → "Project URL"  (looks like https://abc.supabase.co)
  SUPABASE_SERVICE_KEY → "service_role" key  (NOT the anon key)

  ⚠  Never share these with anyone. They give full database access.

══════════════════════════════════════════════════════════════════
  PART 1 — DEPLOY TO RENDER  (15 minutes, free forever)
══════════════════════════════════════════════════════════════════

STEP 1 — Create a free GitHub account
  → https://github.com  (skip if you already have one)

STEP 2 — Create a new GitHub repository
  1. Click "+" (top right) → "New repository"
  2. Repository name: animesalt-scraper
  3. Set to: Public  (required for Render free tier)
  4. Click "Create repository"

STEP 3 — Upload these files to GitHub
  1. On the repository page, click "uploading an existing file"
  2. Drag and drop ALL of these files:
       main.py
       pipeline.py
       requirements.txt
       render.yaml
  3. Commit message: "Initial scraper"
  4. Click "Commit changes"

  Your repo should look like this:
    animesalt-scraper/
    ├── main.py
    ├── pipeline.py
    ├── requirements.txt
    └── render.yaml

STEP 4 — Create a free Render account
  → https://render.com  → "Get Started for Free"
  Sign up with your GitHub account (easiest)

STEP 5 — Create a new Web Service on Render
  1. From Render dashboard → click "New +" → "Web Service"
  2. Click "Connect a repository"
  3. Select your "animesalt-scraper" repository
  4. Click "Connect"

STEP 6 — Configure the service
  Fill in these fields:

    Name:          animesalt-scraper
    Region:        pick the closest to you
    Branch:        main
    Runtime:       Python 3
    Build Command: pip install -r requirements.txt
    Start Command: python main.py
    Plan:          Free  ← make sure this is selected

  Then scroll down to "Environment Variables" and add:

    Key: SUPABASE_URL
    Value: (paste your Supabase project URL)

    Key: SUPABASE_SERVICE_KEY
    Value: (paste your service_role key)

    Key: SCRAPE_INTERVAL_HOURS
    Value: 6

  Click "Create Web Service"

STEP 7 — Wait for first deploy (~3-5 minutes)
  Render will install packages and start the scraper.
  You'll see logs like:
    [health] Listening on port 10000
    ✓ Connected to Supabase
    STEP 1 — Fixing existing bad image URLs
    ...

  Your scraper URL will be something like:
    https://animesalt-scraper.onrender.com

  Open that URL in your browser — you'll see a live status page:
    AnimeSalt Scraper — alive
    Cycle:         1
    Scraping now:  True
    Last started:  2026-05-19 10:00:00 UTC
    ...

══════════════════════════════════════════════════════════════════
  PART 2 — KEEP IT AWAKE 24/7 WITH UPTIMEROBOT  (free)
══════════════════════════════════════════════════════════════════

Without this step, Render sleeps the service after 15 min of no traffic.
UptimeRobot pings it every 5 min → it never sleeps.

STEP 8 — Create a free UptimeRobot account
  → https://uptimerobot.com  → "Register for FREE"

STEP 9 — Add a monitor
  1. Click "+ Add New Monitor"
  2. Monitor Type: HTTP(s)
  3. Friendly Name: AnimeSalt Scraper
  4. URL: https://animesalt-scraper.onrender.com
     (use YOUR actual Render URL from Step 7)
  5. Monitoring Interval: Every 5 minutes
  6. Click "Create Monitor"

  UptimeRobot will now ping your scraper every 5 minutes.
  Render sees traffic → never sleeps → scraper runs forever ✓

══════════════════════════════════════════════════════════════════
  WHAT HAPPENS AFTER SETUP
══════════════════════════════════════════════════════════════════

  The scraper runs on this schedule:
    → Full scrape of all animesalt.ac content (~60-90 min)
    → Sleep 6 hours
    → Full scrape again  (picks up any new titles added to the site)
    → Sleep 6 hours
    → Repeat forever

  Everything gets saved to your Supabase database.
  Your player app reads from the same Supabase → always up to date.

  To check scraper status anytime:
    Open: https://animesalt-scraper.onrender.com
    You'll see cycle count, last run time, and any errors.

  To change scrape interval:
    Render dashboard → your service → Environment → SCRAPE_INTERVAL_HOURS
    Change the value → click Save → service restarts automatically

══════════════════════════════════════════════════════════════════
  TROUBLESHOOTING
══════════════════════════════════════════════════════════════════

  Service won't start?
    → Check Render logs (dashboard → your service → Logs tab)
    → Make sure all 4 files are uploaded to GitHub
    → Make sure SUPABASE_URL and SUPABASE_SERVICE_KEY are set correctly

  Scraper connects but finds 0 content?
    → Your Supabase URL or key might be wrong
    → Check: Supabase → Settings → API → copy service_role key (NOT anon)

  Service still sleeping?
    → Make sure UptimeRobot monitor is active (green dot)
    → Check the URL in UptimeRobot matches your Render URL exactly

  Want faster scraping?
    → Change SCRAPE_INTERVAL_HOURS to 2 or 3 in Render environment vars
    → The scraper is safe to run frequently — it skips already-scraped content
