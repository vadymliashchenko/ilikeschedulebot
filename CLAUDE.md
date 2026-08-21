# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Telegram bot (aiogram 3) for a dance studio that replaces manual, Instagram-based collection of daily class status updates. It polls choreographers about tomorrow's class status via inline buttons, posts a formatted schedule table, generates Instagram Story images per time slot, and tracks cancellation/substitution limits for the owner. See `README.md` for the non-technical setup walkthrough (bot creation, env vars, Railway deploy).

## Commands

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m bot.main          # starts polling + the APScheduler cron jobs
```

There is no automated test suite or linter configured in this repo. `bot/main.py` requires `BOT_TOKEN`, `CHOREO_GROUP_CHAT_ID`, and `OWNER_CHAT_ID` env vars to import (see `.env.example` for the full list) — for any throwaway script that imports `bot.*` without a real `.env`, set at minimum:

```python
import os
os.environ.setdefault("BOT_TOKEN", "dummy:token")
os.environ.setdefault("CHOREO_GROUP_CHAT_ID", "-100111")
os.environ.setdefault("OWNER_CHAT_ID", "222")
```

The de facto way to validate changes to `bot/story_builder.py` (image generation) is a standalone script: init a throwaway `aiosqlite` DB via `bot.db.init_db(path)`, look up groups with `db.get_group_by_name_time_pattern(conn, name, day_pattern, time)`, call `db.save_response(conn, group_id, date, status_key)` to fake a poll response, then `story_builder.build_story_image(...)` or `build_day_images(...)` and inspect the resulting file. There's no way to preview output without going through the DB layer.

Deploys are git-push-triggered on Railway (auto-deploys `main`). This repo has no CI — pushing to `main` is the only "build" step, and it goes live immediately.

## Architecture

### Data flow
`bot/config.py` holds the static schedule (`SCHEDULE` list — one dict per class slot, with `day_pattern`/`time`/`choreographer`/`style`/`locked`/etc.) and all tunables (poll/reminder/table times, limits, the `STATUSES` dict of poll button definitions). `bot/db.py` seeds `groups` from `config.SCHEDULE` on first run (`_seed_groups_if_empty`) — the DB is the live/mutable copy (owner can add/remove/edit groups via `/admin`), `config.SCHEDULE` is only the initial seed.

Each class slot belongs to a `day_pattern` (`mon_thu`, `tue_fri`, or `wed_only` — groups meet twice a week except `wed_only`) crossed with `config.DAY_PATTERN_WEEKDAYS`. `bot/scheduler.py`'s cron jobs always operate on "tomorrow" (`_tomorrow()`), which is why manual admin triggers like `/tablenow` produce nothing observable on days when tomorrow has no scheduled `day_pattern` (e.g. triggering on a Friday, since Saturday matches no pattern).

### Bot structure
`bot/main.py` wires one shared `aiosqlite.Connection` into the aiogram `Dispatcher` (`dp["conn"]`) and registers routers in order: `admin` (owner-only, `F.chat.id == OWNER_CHAT_ID`), `manager` (owner + optional `MANAGER_CHAT_ID`, group-roster CRUD only), `poll` (status button callbacks, open to whoever the poll was sent to), `corrections` (Instagram/TikTok chat only, keyword-parsed `/переробити`). Role separation is enforced entirely via `Router.message.filter(...)`, not per-handler checks.

`bot/scheduler.py` cron jobs (APScheduler, `misfire_grace_time=3600` so a Railway redeploy mid-trigger doesn't silently drop a job): `job_start_poll` → `job_reminder` → `job_final_table` (posts the text table, then triggers `send_story_images`) → `job_client_repost` (separate client-facing channel, independent of the internal table). Corresponding owner-only manual triggers live in `bot/handlers/admin.py` (`/pollnow`, `/remindnow`, `/tablenow`, `/clientnow`).

### Poll delivery is per-choreographer, not group-wide
`job_start_poll` and `job_reminder` both send to each choreographer's own private chat with the bot (`db.get_telegram_id_by_choreographer`), not to the shared group — so a choreographer only ever sees polls/reminders for their own classes, and the reminder re-sends the same `status_keyboard` so they can answer straight from it. This requires the choreographer to have already DM'd the bot at least once (a Telegram Bot API restriction: bots can't initiate a private chat). `bot/handlers/poll.py`'s `/start` handler (private chats only) is the self-service onboarding: it shows `keyboards.choreographer_list_keyboard`, and the pick writes to the same `choreographer_links` table used elsewhere for identity matching. A choreographer who hasn't linked yet is silently un-pollable — both jobs collect these into one warning message to `OWNER_CHAT_ID` instead of failing loudly per-slot; `bot/handlers/admin.py`'s owner-only `/unlinked` command lists exactly who's still missing, on demand.

### Telegram forum topics
The choreographer-facing group is a forum (Telegram "topics") with a "Schedule" topic, where the text table and story images post. `CHOREO_GROUP_CHAT_ID` and `INSTAGRAM_CHAT_ID` point at the same physical chat id; `SCHEDULE_TOPIC_ID` (optional — `None` for a plain non-forum group) selects the topic via `message_thread_id`. `bot/handlers/corrections.py`'s router filters on both `chat.id` and `message_thread_id` together, since chat-id alone doesn't distinguish topics within one forum. Polls and reminders don't touch this group at all (see below) — there was a "Team Chat" topic for shared reminders earlier, but that was replaced by private per-choreographer reminders and the `TEAM_CHAT_TOPIC_ID` config var was removed as dead code.

### Story image generation (`bot/story_builder.py`)
Composites status "pills" (colored rounded-rect labels — text and/or icon) onto hand-designed JPG background templates, one template per `(day_pattern, time)` slot (`bot/story_layout.py`'s `STORY_LAYOUTS`, mapping to `assets/story_templates/*.jpg`). Each template has up to 5 fixed pill row slots; `STORY_LAYOUTS[...]["rows"]` lists which choreographer occupies each row **in the order drawn in that specific template's artwork** — this is independent of `config.SCHEDULE` ordering and must be kept in sync by hand whenever a template changes.

Templates come in two resolutions (`story_layout._PILL_BOUNDS_BY_WIDTH`, keyed by pixel width): 1080×1920 for the templates the designer has re-exported at full Instagram Story quality, and legacy 720×1280 for any not yet upgraded. Pill Y/X bounds are **measured directly from each resolution's pixels**, not derived by scaling one from the other — the two designs are not proportionally identical (row height grew ~1.32× while canvas width grew 1.5×). Every font size / icon size / margin in `_draw_pill` is multiplied by a per-resolution `scale` factor (`story_layout` `"scale"` key) rather than hardcoded, so a new resolution only needs new measured bounds + a scale constant, not code changes.

Groups with `locked: True` (and the icon-only "🎬 filming" status) are rendered by pasting a **pre-baked whole-pill PNG** (`assets/story_templates/locked_pills/<width>/row<N>.png`, `filming_pills/<width>/row<N>.png` — one asset per row index per resolution) rather than drawing text/icon over the template at request time. This is deliberate: earlier attempts to detect-and-skip a designer-baked lock, or to draw a same-sized icon fresh each time, produced inconsistent icon sizing and background-fringing artifacts. Regenerating these assets (e.g. after a template refresh) requires re-running the extraction: crop the pill rectangle from a source frame that already has the mark baked in, or — where no such frame exists — alpha-diff a marked frame against a pixel-identical blank frame from the *same unmodified export batch* (mixing a freshly-recompressed clean template into that diff reintroduces JPEG noise) to get a clean icon, then composite it onto a blank pill background. See git history on `bot/story_builder.py` / `assets/story_templates/` for the actual scripts used.

`db.get_locked_group_by_name_pattern` is a fallback lookup (ignores time) for rows that are shown decoratively in a frame whose time doesn't exactly match the group's real scheduled time — without it those rows silently render blank.

### Fonts
`assets/fonts/` bundles `DejaVuSans-Bold.ttf` and `NotoColorEmoji.ttf` directly in the repo (not system fonts) so rendering is pixel-identical on macOS (dev) and Railway's Linux container. The color emoji font only supports a handful of fixed "strike" sizes (`_find_supported_emoji_size` probes for the one this specific font file supports — currently 109px) — always render at that size and resize the cropped RGBA bitmap afterward, never request an arbitrary size directly.
