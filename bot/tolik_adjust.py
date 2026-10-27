"""Толік: підлаштування температури за відповідями опитувань (погоджено власником 23.10.2026).

Поправка (°C) до цілі прогріву ведеться окремо:
  • для кожного СТИЛЮ занять (не хореографа) — за відповідями хореографів;
  • для кожної рецепції / роздягальні (поверх) — за відповідями фей.
Правила (раз на день, за більшістю відповідей дня):
  🥵 жарко → −2, ще раз жарко → ще −2; 🥶 холодно → +2;
  розворот (холодно після жарко або навпаки) → крок 1 °C назавжди;
  🙂 нормально / немає відповіді → без змін.
Інтенсивні стилі (хореографія, хіп-хоп, джаз-фанк) стартують з −2 (= кондиціонер на час заняття вимкнено,
поки ціль < 17 °C). Межі поправки: −6…+4.
Home Assistant забирає план дня через GET /tolik/plan?key=… (див. run_web).
"""
import datetime as dt
import json
import logging
import os
import re
import secrets
from typing import Optional

import aiosqlite
from aiogram import Bot
from aiohttp import web

from bot import config, db, tolik_db

logger = logging.getLogger(__name__)

INTENSE = ("choreography", "hip hop", "jazz funk")
START_INTENSE = -2.0
STEP0 = 2.0
OFF_MIN, OFF_MAX = -6.0, 4.0
DIR = {"cold": 1, "hot": -1, "normal": 0}
ROOM_UA = {"reception": "рецепція", "dressing": "роздягалка"}


def norm_style(style: str) -> str:
    s = re.sub(r"\(.*?\)", " ", style or "").lower()
    s = s.replace("-", " ").replace("juniors", " ")
    return re.sub(r"\s+", " ", s).strip()


def is_intense(nstyle: str) -> bool:
    return any(w in nstyle for w in INTENSE)


def style_key(style: str) -> str:
    return "style:" + norm_style(style)


def room_key(floor: int, room: str) -> str:
    return f"room:{floor}:{room}"


def key_label(key: str) -> str:
    if key.startswith("style:"):
        return key[6:].title()
    _, floor, room = key.split(":")
    return f"{floor} пов. — {ROOM_UA.get(room, room)}"


async def get_adj(conn: aiosqlite.Connection, key: str) -> dict:
    cur = await conn.execute("SELECT * FROM tolik_adjust WHERE key = ?", (key,))
    r = await cur.fetchone()
    if r:
        return dict(r)
    start = START_INTENSE if key.startswith("style:") and is_intense(key[6:]) else 0.0
    return dict(key=key, offset=start, step=STEP0, last_dir=0, updated_at=None)


async def _save_adj(conn, a: dict) -> None:
    await conn.execute(
        """INSERT INTO tolik_adjust (key, offset, step, last_dir, updated_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET offset=excluded.offset, step=excluded.step,
               last_dir=excluded.last_dir, updated_at=excluded.updated_at""",
        (a["key"], a["offset"], a["step"], a["last_dir"], tolik_db.now_iso()))


def apply_rule(a: dict, d: int) -> dict:
    """Чиста функція правила. d: +1 холодно, −1 жарко, 0 нормально."""
    a = dict(a)
    if d == 0:
        return a
    if a["last_dir"] and d == -a["last_dir"]:
        a["step"] = 1.0
    a["offset"] = max(OFF_MIN, min(OFF_MAX, a["offset"] + d * a["step"]))
    a["last_dir"] = d
    return a


async def process_new_answers(conn: aiosqlite.Connection) -> list[str]:
    """Обробляє нові (не тестові) відповіді, повертає рядки звіту про зміни."""
    last_id = int(await tolik_db.get_state(conn, "adjust_last_id") or 0)
    cur = await conn.execute(
        "SELECT * FROM comfort_responses WHERE id > ? AND is_test = 0 AND answer IS NOT NULL ORDER BY id",
        (last_id,))
    rows = await cur.fetchall()
    if not rows:
        return []
    votes: dict = {}
    for r in rows:
        if r["role"] == "choreographer":
            if not r["style"]:
                continue
            k = style_key(r["style"])
        else:
            if not r["floor"] or r["room"] not in ROOM_UA:
                continue
            k = room_key(r["floor"], r["room"])
        votes.setdefault((k, r["survey_date"]), []).append(DIR.get(r["answer"], 0))
    report = []
    for (k, day) in sorted(votes, key=lambda x: (x[1], x[0])):
        s = sum(votes[(k, day)])
        d = 1 if s > 0 else -1 if s < 0 else 0
        before = await get_adj(conn, k)
        after = apply_rule(before, d)
        await _save_adj(conn, after)
        mark = {1: "🥶 холодно", -1: "🥵 жарко", 0: "🙂 нормально"}[d]
        if after["offset"] != before["offset"]:
            report.append(f"{key_label(k)}: {mark} → поправка {before['offset']:+g} → {after['offset']:+g} °C")
        else:
            report.append(f"{key_label(k)}: {mark} → без змін ({after['offset']:+g} °C)")
    await conn.commit()
    await tolik_db.set_state(conn, "adjust_last_id", str(max(r["id"] for r in rows)))
    return report


async def job_adjust(bot: Bot, conn: aiosqlite.Connection) -> None:
    report = await process_new_answers(conn)
    if report:
        await bot.send_message(config.OWNER_CHAT_ID,
                               "🎛 Толік: поправки за сьогоднішніми відповідями\n\n" + "\n".join(report),
                               parse_mode=None)


async def all_adjustments_text(conn: aiosqlite.Connection) -> str:
    cur = await conn.execute("SELECT * FROM tolik_adjust ORDER BY key")
    rows = [dict(r) for r in await cur.fetchall()]
    seen = {r["key"] for r in rows}
    # інтенсивні стилі показуємо навіть без відповідей (стартова поправка)
    for g in config.SCHEDULE:
        k = style_key(g["style"])
        if k not in seen and is_intense(k[6:]):
            rows.append(await get_adj(conn, k))
            seen.add(k)
    if not rows:
        return "Поправок ще немає 🙂"
    lines = ["🎛 Поправки Толіка до цілі прогріву:"]
    for r in sorted(rows, key=lambda x: x["key"]):
        lines.append(f"{key_label(r['key'])}: {r['offset']:+g} °C (крок {r['step']:g})")
    lines.append("\nЯкщо ціль < 17 °C — кондиціонер на час заняття вимикається.")
    return "\n".join(lines)


# ---------------- план для Home Assistant ----------------
async def plan_for(conn: aiosqlite.Connection, d: dt.date) -> dict:
    classes = []
    for g in await db.get_scheduled_groups_for_date(conn, d):
        if g["start_date"] and g["start_date"] > d.isoformat():
            continue
        resp = await db.get_response(conn, g["id"], d)
        if resp is not None and resp["status_key"] == "cancelled":
            continue
        k = style_key(g["style"])
        a = await get_adj(conn, k)
        classes.append(dict(hall=g["hall"], time=g["time"], style=g["style"], choreographer=g["choreographer"],
                            key=k, offset=a["offset"], intense=is_intense(k[6:])))
    from bot import tolik_calendar
    extra, _ = await tolik_calendar.extra_classes(conn, d)
    have = {(c["hall"], c["time"]) for c in classes}
    for gs in extra.values():
        for g in gs:
            if (g["hall"], g["time"]) in have:
                continue
            k = style_key(g["style"])
            a = await get_adj(conn, k)
            classes.append(dict(hall=g["hall"], time=g["time"], style=g["style"], choreographer=g["choreographer"],
                                key=k, offset=a["offset"], intense=is_intense(k[6:])))
    rooms = {}
    for floor in (1, 2, 3):
        for room in ("reception", "dressing"):
            a = await get_adj(conn, room_key(floor, room))
            rooms[f"{floor}_{room}"] = a["offset"]
    return dict(date=d.isoformat(), generated=tolik_db.now_iso(), classes=classes, rooms=rooms)


async def api_key(conn: aiosqlite.Connection) -> str:
    k = await tolik_db.get_state(conn, "api_key")
    if not k:
        k = secrets.token_urlsafe(24)
        await tolik_db.set_state(conn, "api_key", k)
    return k


async def run_web(conn: aiosqlite.Connection) -> Optional[web.AppRunner]:
    """Маленький HTTP-сервер для HA. Запускається лише якщо Railway дав PORT."""
    port = os.environ.get("PORT")
    if not port:
        logger.info("Tolik web: PORT not set, plan endpoint disabled")
        return None

    async def plan(request: web.Request) -> web.Response:
        if request.query.get("key") != await api_key(conn):
            return web.Response(status=403, text="forbidden")
        try:
            d = dt.date.fromisoformat(request.query["date"]) if "date" in request.query else \
                dt.datetime.now(config.TZ).date()
        except ValueError:
            return web.Response(status=400, text="bad date")
        return web.Response(text=json.dumps(await plan_for(conn, d), ensure_ascii=False),
                            content_type="application/json")

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def extra(request: web.Request) -> web.Response:
        if request.query.get("key") != await api_key(conn):
            return web.Response(status=403, text="forbidden")
        try:
            body = await request.json()
            date = dt.date.fromisoformat(body["date"]).isoformat()
            events = [dict(hall=str(e.get("hall", "")), start=str(e.get("start", ""))[:5], title=str(e.get("title", ""))[:120])
                      for e in body.get("events", [])][:60]
        except Exception:  # noqa: BLE001
            return web.Response(status=400, text="bad json")
        from bot import tolik_calendar
        n = await tolik_calendar.save_extra(conn, date, events)
        return web.Response(text=json.dumps({"ok": True, "saved": n}), content_type="application/json")

    async def answers(request: web.Request) -> web.Response:
        """CSV усіх реальних відповідей за N днів (для аналізу в HA / Claude)."""
        if request.query.get("key") != await api_key(conn):
            return web.Response(status=403, text="forbidden")
        try:
            days = max(1, min(366, int(request.query.get("days", "60"))))
        except ValueError:
            days = 60
        since = (dt.datetime.now(config.TZ).date() - dt.timedelta(days=days)).isoformat()
        cur = await conn.execute(
            "SELECT survey_date, role, person, hall, floor, room, class_time, style, answer, thermometer, updated_at "
            "FROM comfort_responses WHERE is_test = 0 AND survey_date >= ? ORDER BY survey_date, role, person, class_time",
            (since,))
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["date", "role", "person", "hall", "floor", "room", "class_time", "style", "answer", "thermometer", "updated_at"])
        for r in await cur.fetchall():
            w.writerow([r[k] if r[k] is not None else "" for k in r.keys()])
        return web.Response(text=buf.getvalue(), content_type="text/csv", charset="utf-8")

    app = web.Application()
    app.router.add_get("/tolik/plan", plan)
    app.router.add_get("/tolik/answers.csv", answers)
    app.router.add_post("/tolik/extra", extra)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    logger.info("Tolik web started on port %s", port)
    return runner
