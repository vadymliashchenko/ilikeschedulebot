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

Before touching `assets/story_templates/*`, `bot/story_layout.py`, or `bot/story_builder.py`, read [LEARNINGS.md](LEARNINGS.md) — it records the user's already-confirmed rules for the current template geometry and design (row-bound measurement method, centering, font sizing, lock-icon handling) so they aren't relitigated or accidentally reverted.

## Architecture

### Data flow
`bot/config.py` holds the static schedule (`SCHEDULE` list — one dict per class slot, with `day_pattern`/`time`/`choreographer`/`style`/`locked`/etc.) and all tunables (poll/reminder/table times, limits, the `STATUSES` dict of poll button definitions). `bot/db.py` seeds `groups` from `config.SCHEDULE` on first run (`_seed_groups_if_empty`) — the DB is the live/mutable copy (owner can add/remove/edit groups via `/admin`), `config.SCHEDULE` is only the initial seed.

**Editing `config.SCHEDULE` in a deployed environment does NOT update the live DB by itself** — the seed only runs once, when `groups` is empty, and `/admin`'s add/edit flow can't set `level`/`hall`/`locked`/`start_date` at all. Left unreconciled, this silently drifts the real roster from `config.py` over time (discovered 2026-09: stale "ghost" locked groups the owner had already removed from `config.py` kept appearing in the text schedule, while newly-added choreographers never showed up, because their `groups` rows were never inserted/updated). `db.resync_groups_from_config` (exposed as the owner-only `/syncroster` command in `bot/handlers/admin.py`) reconciles this by matching on `(choreographer, day_pattern, time)`: inserts groups present in `config.SCHEDULE` but missing from the DB, updates `style`/`level`/`hall`/`locked`/`start_date` where they differ, and soft-deactivates (`active=0`, never a hard delete — `responses` keeps its foreign key) DB groups no longer in `config.SCHEDULE`. Idempotent — safe to run after any `config.SCHEDULE` edit, and safe to run repeatedly.

**`STATUSES` has exactly 6 keys** — `first` (🆕 нова хореографія), `open` (можна приєднатися), `closed` (не можна приєднатися), `last_mk` (🎓 формат МК), `substitute` (🔀 заміна), `cancelled` (🚫 відміна). Two more keys (`second_ok`/`second_no` "друге заняття", and `filming` 🎦 "зйомка відео") existed earlier and were deliberately removed (2026-09-10, user request) — don't reintroduce them without being asked; each `STATUSES[key]` has `button` (poll keyboard label), `label` (used in admin-facing text), and `phrase` (what `table_builder.py` prints under a choreographer's name/style in the text schedule, e.g. "• 🆕 НОВА ХОРЕОГРАФІЯ, можна приєднатися"). The legend at the bottom of the text table (`table_builder._build_legend`, `config.STATUS_EMOJI_LEGEND`) only lists emoji that actually appear that day, not the full fixed set — see `bot/story_builder.py`'s `_STORY_CONTENT` for the parallel (shorter) text used inside story images, which must stay in sync with these same 6 keys.

Each class slot belongs to a `day_pattern` (`mon_thu`, `tue_fri`, or `wed_only` — groups meet twice a week except `wed_only`) crossed with `config.DAY_PATTERN_WEEKDAYS`. `bot/scheduler.py`'s cron jobs always operate on "tomorrow" (`_tomorrow()`), which is why manual admin triggers like `/tablenow` produce nothing observable on days when tomorrow has no scheduled `day_pattern` (e.g. triggering on a Friday, since Saturday matches no pattern).

### Bot structure
`bot/main.py` wires one shared `aiosqlite.Connection` into the aiogram `Dispatcher` (`dp["conn"]`) and registers routers in order: `admin` (owner-only, `F.chat.id == OWNER_CHAT_ID`), `manager` (owner + optional `MANAGER_CHAT_ID`, group-roster CRUD only), `poll` (status button callbacks, open to whoever the poll was sent to), `corrections` (Instagram/TikTok chat only, keyword-parsed `/переробити`). Role separation is enforced entirely via `Router.message.filter(...)`, not per-handler checks.

`bot/scheduler.py` cron jobs (APScheduler, `misfire_grace_time=3600` so a Railway redeploy mid-trigger doesn't silently drop a job): `job_start_poll` → `job_reminder` → `job_final_table` (posts the text table, then triggers `send_story_images`) → `job_client_repost` (separate client-facing channel, independent of the internal table). Corresponding owner-only manual triggers live in `bot/handlers/admin.py` (`/pollnow`, `/remindnow`, `/tablenow`, `/clientnow`).

### Poll delivery is per-choreographer, not group-wide
`job_start_poll` and `job_reminder` both send to each choreographer's own private chat with the bot (`db.get_telegram_id_by_choreographer`), not to the shared group — so a choreographer only ever sees polls/reminders for their own classes, and the reminder re-sends the same `status_keyboard` so they can answer straight from it. This requires the choreographer to have already DM'd the bot at least once (a Telegram Bot API restriction: bots can't initiate a private chat). `bot/handlers/poll.py`'s `/start` handler (private chats only) is the self-service onboarding: it shows `keyboards.choreographer_list_keyboard`, and the pick writes to the same `choreographer_links` table used elsewhere for identity matching. A choreographer who hasn't linked yet is silently un-pollable — both jobs collect these into one warning message to `OWNER_CHAT_ID` instead of failing loudly per-slot; `bot/handlers/admin.py`'s owner-only `/unlinked` command lists exactly who's still missing, on demand.

### Telegram forum topics
The choreographer-facing group is a forum (Telegram "topics") with a "Schedule" topic, where the text table and story images post. `CHOREO_GROUP_CHAT_ID` and `INSTAGRAM_CHAT_ID` point at the same physical chat id; `SCHEDULE_TOPIC_ID` (optional — `None` for a plain non-forum group) selects the topic via `message_thread_id`. `bot/handlers/corrections.py`'s router filters on both `chat.id` and `message_thread_id` together, since chat-id alone doesn't distinguish topics within one forum. Polls and reminders don't touch this group at all (see below) — there was a "Team Chat" topic for shared reminders earlier, but that was replaced by private per-choreographer reminders and the `TEAM_CHAT_TOPIC_ID` config var was removed as dead code.

### Story image generation (`bot/story_builder.py`)
Composites a status label (text and/or icon, drawn live — no background box in the current design) onto hand-designed JPG background templates, one template per `(day_pattern, time)` slot (`bot/story_layout.py`'s `STORY_LAYOUTS`, mapping to `assets/story_templates/*.jpg`). Each template has up to 5 fixed row slots; `STORY_LAYOUTS[...]["rows"]` lists which choreographer occupies each row **in the order drawn in that specific template's artwork** — this is independent of `config.SCHEDULE` ordering and must be kept in sync by hand whenever a template changes. A row can also be purely decorative (e.g. `mon_thu 17:00`'s second row shows the `16:30`-only group as a preview) — `db.get_locked_group_by_name_pattern` is the fallback lookup (ignores time) for those, used when the exact `(name, day_pattern, time)` lookup misses.

Two template generations are in play, looked up via `story_layout.bounds_for(day_pattern, time, image_width)`:
- **Current design** (since 2026-09-09, 1275×2268, all 8 `tue_fri`/`mon_thu` × `17:00`-`20:00` slots): no visible pill/box — status text is drawn straight over the empty band on the right. Row placement isn't pixel-identical across the 8 exported files (each has its own ~20-30px jitter), so bounds are keyed **per slot** in `story_layout._SLOT_BOUNDS` (measured by locating each row's white name-text via a brightness scan, not by eye) rather than shared by resolution.
- **Legacy design** (`wed_only 18:00`, 720×1280 — currently dormant since Wednesday has no classes): pill bounds are shared across templates of that width, keyed in `story_layout._PILL_BOUNDS_BY_WIDTH`, and locked groups paste a **pre-baked whole-pill PNG** (`assets/story_templates/locked_pills/720/row<N>.png`) instead of drawing live, to avoid icon-sizing/fringing inconsistencies against that design's visible pill box. Regenerating those assets requires cropping the pill rectangle from a source frame that already has the mark baked in, or alpha-diffing a marked frame against a pixel-identical blank frame from the *same unmodified export batch*.

`bounds_for` falls back to the width-keyed lookup (and raises `story_layout.pill_bounds`'s `ValueError`, caught per-slot in `build_day_images`) for any width with no per-slot entry — that's the path a future legacy-style template would take. For the current design, a locked group's row is left untouched (`_draw_locked_pill` no-ops whenever `pill_width` isn't in `_LOCKED_PILL_ROWS`) since the designer already draws a lock icon directly on that row's photo — see [LEARNINGS.md](LEARNINGS.md).

A group whose `start_date` hasn't arrived yet (not locked, never polled) is rendered as "СТАРТ ДД.ММ" using the same single text style as every other status (`_draw_status_text`) — matched against real published stories, see LEARNINGS.md.

`_draw_status_text` takes an optional `y_shift` (canvas-pixel offset applied after vertical centering) for per-status positional tweaks that shouldn't move the whole row's `_SLOT_BOUNDS` box (other statuses can land in that same row on other days). Currently only `substitute` ("ЗАМІНА") uses this, via `_SUBSTITUTE_Y_SHIFT = -20` in `bot/story_builder.py` (2026-09-17, user explicitly rejected shifting the whole row/box — see LEARNINGS.md). Any future "move this one status" request should follow the same pattern: a new `_STATUS_KEY_Y_SHIFT` constant applied only when that `status_key` matches, never an edit to `_SLOT_BOUNDS`.

`db.get_locked_group_by_name_pattern` is a fallback lookup (ignores time) for rows that are shown decoratively in a frame whose time doesn't exactly match the group's real scheduled time — without it those rows silently render blank.

### Fonts
`assets/fonts/` bundles `DejaVuSans-Bold.ttf` directly in the repo (not a system font) so rendering is pixel-identical on macOS (dev) and Railway's Linux container. There's no color emoji font anymore — nothing in the current story-image pipeline draws a live emoji (see above).
