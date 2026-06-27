#!/usr/bin/env python3
"""
One-time DB setup: creates bot_users table in Supabase.
Run: python scripts/create_tables.py
(Requires SUPABASE_URL and SUPABASE_SERVICE_KEY in environment)
"""
import os, sys, requests

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

if not SB_URL or not SB_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")
    sys.exit(1)

DDL = """
CREATE TABLE IF NOT EXISTS bot_users (
    id          UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    telegram_id TEXT        UNIQUE NOT NULL,
    username    TEXT        DEFAULT '',
    first_name  TEXT        DEFAULT '',
    allowed     BOOLEAN     DEFAULT FALSE,
    can_watch   BOOLEAN     DEFAULT FALSE,
    requested   BOOLEAN     DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    allowed_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS bot_users_telegram_id_idx ON bot_users(telegram_id);
CREATE INDEX IF NOT EXISTS bot_users_allowed_idx     ON bot_users(allowed);
"""

headers = {
    "apikey": SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type": "application/json",
}

# Try Supabase pg-meta
import re
m = re.match(r"https://([^.]+)\.supabase\.co", SB_URL)
if m:
    ref = m.group(1)
    for path in ["/pg-meta/v1/query", "/rest/v1/rpc/exec_sql"]:
        r = requests.post(f"https://{ref}.supabase.co{path}",
                          headers=headers,
                          json={"query": DDL},
                          timeout=10)
        if r.ok:
            print(f"✅ bot_users table created via {path}")
            sys.exit(0)
        else:
            print(f"  [{path}] {r.status_code}: {r.text[:100]}")

print("\n⚠️  Automatic table creation not supported for this Supabase plan.")
print("Please run the following SQL in your Supabase SQL Editor:")
print("(Dashboard → SQL Editor → New Query → paste → Run)\n")
print(DDL)
