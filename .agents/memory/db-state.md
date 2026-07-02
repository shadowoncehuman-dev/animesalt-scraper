---
name: Supabase DB state
description: Current state of all Supabase tables for the Senpai TV project
---

## Tables and approximate counts (as of 2026-07-02)
- `content`: ~538 titles
- `episodes`: ~17,267 episodes
- `video_servers`: ~96,433 servers
- `genres`: ~106 genres
- `content_genres`: ~3,235 links
- `bot_users`: 1 (admin only)

All tables exist — no schema migration is needed.

**Why:** Previous session reported PGRST205 errors suggesting missing tables, but they were actually present. The errors were caused by missing pip packages preventing DB connection.
