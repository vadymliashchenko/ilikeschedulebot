"""Толік — опитування про комфорт у залах (хореографи) і на рецепціях/в роздягалках (адміністратори).

Повністю окремий модуль: якщо тут щось піде не так, опитування розкладу, таблиця й сторіс працюють як раніше.
Вмикається змінною TOLIK_ENABLED=true. Поки TOLIK_OWNER_ONLY=true (за замовчуванням), усі повідомлення
приходять лише власнику з позначкою «ТЕСТ».
"""
import datetime as dt
import logging
import os
import random
from typing import Optional

import aiohttp
import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot import config, db, tolik_db
from bot import tolik_texts as T

logger = logging.getLogger(__name__)


def _flag(name: str, default: bool) -> bool:
    return os.environ.get(name, "true" if default else "false").strip().lower() == "true"


ENABLED = _flag("TOLIK_ENABLED", False)
OWNER_ONLY = _flag("TOLIK_OWNER_ONLY", True)
SHEET_WEBHOOK = os.environ.get("TOLIK_SHEET_WEBHOOK", "").strip()
CHOREO_TIME = os.environ.get("TOLIK_CHOREO_TIME", "21:15")
CHOREO_DAYS = os.environ.get("TOLIK_CHOREO_DAYS", "mon,tue,wed,thu,fri")
CHOREO_TIME_SAT = os.environ.get("TOLIK_CHOREO_TIME_SAT", "19:15")
ADMIN_TIME = os.environ.get("TOLIK_ADMIN_TIME", "21:15")
ADJUST_TIME = os.environ.get("TOLIK_ADJUST_TIME", "22:30")  # звіт власнику + поправки (власник 27.10.2026; було 23:30)
ADMIN_DAYS = os.environ.get("TOLIK_ADMIN_DAYS", "mon,tue,wed,thu,fri")
ADMIN_TIME_SAT = os.environ.get("TOLIK_ADMIN_TIME_SAT", "18:15")

HALL_NOM = {"white": "Сірий зал", "blue": "Синій зал", "purple": "Фіолетовий зал",
            "green": "Зелений зал", "pink": "Рожевий зал"}
NUM = ["1️⃣", "2️⃣", "3️⃣"]
MAX_CLASSES_PER_MESSAGE = 3


def today() -> dt.date:
    return dt.datetime.now(config.TZ).date()


def d2s(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def s2d(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d").date()


async def pick_phrase(conn: aiosqlite.Connection, key: str, n: int) -> int:
    """Випадковий індекс 0..n-1, не той самий, що минулого разу."""
    last = await tolik_db.get_state(conn, f"last:{key}")
    choices = [i for i in range(n) if str(i) != last] or list(range(n))
    i = random.choice(choices)
    await tolik_db.set_state(conn, f"last:{key}", str(i))
    return i


async def push_to_sheet(row: dict) -> None:
    """Копія відповіді в Google-таблицю через Apps Script. Помилки лише логуються."""
    if not SHEET_WEBHOOK:
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(SHEET_WEBHOOK, json=row) as r:
                if r.status >= 400:
                    logger.warning("Tolik sheet push HTTP %s", r.status)
    except Exception as e:  # noqa: BLE001
        logger.warning("Tolik sheet push failed: %s", e)


# ---------------- клавіатури ----------------
def choreo_keyboard(classes: list, d: str, bset: int, chosen: dict) -> InlineKeyboardMarkup:
    icons = T.BUTTON_SETS[bset % len(T.BUTTON_SETS)]
    rows = []
    multi = len(classes) > 1
    for i, g in enumerate(classes):
        row = []
        for k in ("c", "n", "h"):
            mark = "✅ " if chosen.get(g["id"]) == k else ""
            prefix = f"{i + 1} " if multi else ""
            row.append(InlineKeyboardButton(
                text=f"{mark}{prefix}{icons[k]}",
                callback_data=f"tk:c:{g['id']}:{d}:{k}:{bset}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def floors_keyboard(d: str, chosen: set) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text=("✅ " if f in chosen else "") + f"{f} поверх",
                                callback_data=f"tk:f:{d}:{f}") for f in (1, 2, 3)]
    return InlineKeyboardMarkup(inline_keyboard=[
        row, [InlineKeyboardButton(text=T.ADMIN_NOT_WORKING_BTN, callback_data=f"tk:f:{d}:0")]])


def room_keyboard(d: str, floor: int, room: str, bset: int, chosen: Optional[str] = None) -> InlineKeyboardMarkup:
    icons = T.BUTTON_SETS[bset % len(T.BUTTON_SETS)]
    row = [InlineKeyboardButton(
        text=("✅ " if chosen == k else "") + icons[k],
        callback_data=f"tk:a:{d}:{floor}:{room}:{k}:{bset}") for k in ("c", "n", "h")]
    return InlineKeyboardMarkup(inline_keyboard=[
        row, [InlineKeyboardButton(text=T.THERMO_BTN, callback_data=f"tk:t:{d}:{floor}:{room}")]])


# ---------------- хореографи ----------------
async def classes_today(conn: aiosqlite.Connection, d: dt.date) -> dict:
    """{хореограф: [групи]} — сьогоднішні заняття без відмін і замін."""
    rows = await db.get_scheduled_groups_for_date(conn, d)
    ds = d.isoformat()
    out: dict = {}
    for g in rows:
        if g["start_date"] and g["start_date"] > ds:
            continue
        resp = await db.get_response(conn, g["id"], d)
        if resp is not None and resp["status_key"] in ("cancelled", "substitute"):
            continue
        out.setdefault(g["choreographer"], []).append(g)
    return out


def choreo_text(qi: int, classes: list) -> str:
    if len(classes) == 1:
        g = classes[0]
        place = f"{T.HALL_NAMES.get(g['hall'], 'залі')} о {g['time']}"
        return T.CHOREO_QUESTIONS[qi].format(place=place) + "\n\n" + T.LEGEND
    lines = [T.CHOREO_QUESTIONS[qi].format(place="залах"), ""]
    for i, g in enumerate(classes):
        lines.append(f"{NUM[i]} {HALL_NOM.get(g['hall'], g['hall'])}, {g['time']} — {g['style']}")
    lines += ["", T.LEGEND]
    return "\n".join(lines)


async def job_choreo(bot: Bot, conn: aiosqlite.Connection, force_test: bool = False) -> int:
    if not (ENABLED or force_test):
        return 0
    test = force_test or OWNER_ONLY
    d = today()
    by_choreo = await classes_today(conn, d)
    from bot import tolik_calendar
    extra, unknown = await tolik_calendar.extra_classes(conn, d)
    for name, gs in extra.items():
        have = {(g["time"], g["hall"]) for g in by_choreo.get(name, [])}
        by_choreo.setdefault(name, []).extend(g for g in gs if (g["time"], g["hall"]) not in have)
    if unknown and not force_test:
        try:
            from bot.tolik_adjust import send_report
            await send_report(bot, "🤔 Толік: не знайшов хореографа для занять з календаря: " + "; ".join(unknown))
        except TelegramAPIError:
            pass
    if not by_choreo:
        return 0
    qi = await pick_phrase(conn, "choreo_q", len(T.CHOREO_QUESTIONS))
    bset = random.randrange(len(T.BUTTON_SETS))
    sent = 0
    for name, classes in sorted(by_choreo.items()):
        classes = sorted(classes, key=lambda g: g["time"])[-MAX_CLASSES_PER_MESSAGE:]
        text = choreo_text(qi, classes)
        kb = choreo_keyboard(classes, d2s(d), bset, {})
        if test:
            chat_id = config.OWNER_CHAT_ID
            text = f"🧪 ТЕСТ Толіка — так побачить {name}:\n\n" + text
        else:
            chat_id = await db.get_telegram_id_by_choreographer(conn, name)
            if chat_id is None:
                continue
        try:
            await bot.send_message(chat_id, text, reply_markup=kb, parse_mode=None)
            sent += 1
        except TelegramAPIError as e:
            logger.warning("Tolik choreo send to %s failed: %s", name, e)
        if test and sent >= 3:
            break  # у тесті власнику достатньо 3 прикладів
    logger.info("Tolik choreo survey %s: %d sent (test=%s)", d, sent, test)
    if not test:
        await tolik_db.set_state(conn, f"sent:choreo:{d.isoformat()}", "1")
    return sent


# ---------------- адміністратори ----------------
async def admin_floor_text(conn: aiosqlite.Connection, name: Optional[str]) -> str:
    gi = random.randrange(len(T.ADMIN_GREETINGS))
    greet = T.ADMIN_GREETINGS[gi]
    if "{name}" in greet and not name:
        greet = T.ADMIN_GREETINGS[-1]
    qi = await pick_phrase(conn, "admin_q", len(T.ADMIN_FLOOR_QUESTIONS))
    return f"{greet.format(name=name or '')} {T.ADMIN_FLOOR_QUESTIONS[qi]}\n{T.ADMIN_FLOOR_HINT}"


async def job_admin(bot: Bot, conn: aiosqlite.Connection, force_test: bool = False) -> int:
    if not (ENABLED or force_test):
        return 0
    test = force_test or OWNER_ONLY
    d = today()
    admins = await tolik_db.get_approved_admins(conn)
    sent = 0
    if test:
        sample = admins[0]["name"] if admins else random.choice(T.ADMIN_NAMES)
        text = f"🧪 ТЕСТ Толіка — так побачать феї (приклад: {sample}):\n\n" + await admin_floor_text(conn, sample)
        await bot.send_message(config.OWNER_CHAT_ID, text, reply_markup=floors_keyboard(d2s(d), set()), parse_mode=None)
        return 1
    for a in admins:
        text = await admin_floor_text(conn, a["name"])
        try:
            await bot.send_message(a["telegram_user_id"], text, reply_markup=floors_keyboard(d2s(d), set()), parse_mode=None)
            sent += 1
        except TelegramAPIError as e:
            logger.warning("Tolik admin send to %s failed: %s", a["name"], e)
    logger.info("Tolik admin survey %s: %d sent", d, sent)
    await tolik_db.set_state(conn, f"sent:admin:{d.isoformat()}", "1")
    return sent


async def send_room_questions(bot: Bot, chat_id: int, d: str, floor: int) -> None:
    bset = random.randrange(len(T.BUTTON_SETS))
    rq = random.choice(T.ADMIN_RECEPTION_QUESTIONS).format(floor=floor) + "\n" + T.LEGEND
    dq = random.choice(T.ADMIN_DRESSING_QUESTIONS).format(floor=floor) + "\n" + T.LEGEND
    await bot.send_message(chat_id, rq, reply_markup=room_keyboard(d, floor, "r", bset), parse_mode=None)
    await bot.send_message(chat_id, dq, reply_markup=room_keyboard(d, floor, "d", bset), parse_mode=None)


# ---------------- розклад ----------------
def _safe(job):
    async def wrapper(bot: Bot, conn: aiosqlite.Connection):
        try:
            await job(bot, conn)
        except Exception:  # noqa: BLE001 — Толік ніколи не має валити бота
            logger.exception("Tolik job %s failed", job.__name__)
    wrapper.__name__ = f"safe_{job.__name__}"
    return wrapper


async def job_catch_up(bot: Bot, conn: aiosqlite.Connection) -> None:
    """Після перезапуску (Railway-деплой) — надолужити опитування, якщо його час уже минув, а сьогодні ще не надсилали.
    27.10.2026: деплой близько 21:15 «з'їв» опитування — планувальник у пам'яті не запускає пропущені задачі."""
    if not ENABLED or OWNER_ONLY:
        return
    now = dt.datetime.now(config.TZ)
    d, hm = now.date(), now.strftime("%H:%M")
    wd = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][d.weekday()]
    plans = [("choreo", CHOREO_TIME_SAT if wd == "sat" else CHOREO_TIME,
              wd == "sat" or wd in CHOREO_DAYS.split(","), job_choreo),
             ("admin", ADMIN_TIME_SAT if wd == "sat" else ADMIN_TIME,
              wd == "sat" or wd in ADMIN_DAYS.split(","), job_admin)]
    for kind, at, day_ok, job in plans:
        if day_ok and at <= hm < "23:00" and not await tolik_db.get_state(conn, f"sent:{kind}:{d.isoformat()}"):
            logger.info("Tolik catch-up: running missed %s survey for %s", kind, d)
            await job(bot, conn)


def add_jobs(scheduler: AsyncIOScheduler, bot: Bot, conn: aiosqlite.Connection) -> None:
    ch_h, ch_m = CHOREO_TIME.split(":")
    ad_h, ad_m = ADMIN_TIME.split(":")
    sa_h, sa_m = ADMIN_TIME_SAT.split(":")
    scheduler.add_job(_safe(job_choreo), CronTrigger(day_of_week=CHOREO_DAYS, hour=ch_h, minute=ch_m, timezone=config.TZ),
                      args=(bot, conn), id="tolik_choreo", replace_existing=True, misfire_grace_time=1800)
    cs_h, cs_m = CHOREO_TIME_SAT.split(":")
    scheduler.add_job(_safe(job_choreo), CronTrigger(day_of_week="sat", hour=cs_h, minute=cs_m, timezone=config.TZ),
                      args=(bot, conn), id="tolik_choreo_sat", replace_existing=True, misfire_grace_time=1800)
    scheduler.add_job(_safe(job_admin), CronTrigger(day_of_week=ADMIN_DAYS, hour=ad_h, minute=ad_m, timezone=config.TZ),
                      args=(bot, conn), id="tolik_admin", replace_existing=True, misfire_grace_time=1800)
    scheduler.add_job(_safe(job_admin), CronTrigger(day_of_week="sat", hour=sa_h, minute=sa_m, timezone=config.TZ),
                      args=(bot, conn), id="tolik_admin_sat", replace_existing=True, misfire_grace_time=1800)
    from bot import tolik_adjust
    aj_h, aj_m = ADJUST_TIME.split(":")
    scheduler.add_job(_safe(tolik_adjust.job_adjust), CronTrigger(hour=aj_h, minute=aj_m, timezone=config.TZ),
                      args=(bot, conn), id="tolik_adjust", replace_existing=True, misfire_grace_time=3600)
    from apscheduler.triggers.date import DateTrigger
    scheduler.add_job(_safe(job_catch_up), DateTrigger(run_date=dt.datetime.now(config.TZ) + dt.timedelta(seconds=90)),
                      args=(bot, conn), id="tolik_catch_up", replace_existing=True)
    logger.info("Tolik jobs added (enabled=%s, owner_only=%s)", ENABLED, OWNER_ONLY)
