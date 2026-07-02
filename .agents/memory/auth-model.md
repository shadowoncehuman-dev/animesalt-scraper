---
name: Telegram bot auth model
description: Two-tier access control in the Senpai TV Telegram bot
---

## Access tiers
- `allowed=True` → can browse the library (/anime, /search, /new, /stats)
- `can_watch=True` → can view episode lists and stream servers (requires allowed=True too)
- Admins in `ADMIN_USER_IDS` bypass all checks

## Rules enforced (post-fix)
- `admin:block` must set BOTH `allowed=False` AND `can_watch=False`
- `admin:no` (deny request) must set `allowed=False, can_watch=False, requested=False`
- Watch callbacks (`anime:eps`, `anime:ep`, `anime:watch`) check `allowed` first, then `can_watch`

**Why:** Original code only set `allowed=False` on block, leaving `can_watch=True` residual — blocked users could still access episode streams via stale inline buttons.

**How to apply:** Any new watch-gated callback must check both `allowed` AND `can_watch`. Block/deny actions must always clear `can_watch`.
