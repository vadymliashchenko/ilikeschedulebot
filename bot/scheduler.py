import datetime as dt
import logging
import random

import aiosqlite
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot import config, db, keyboards, table_builder

logger = logging.getLogger(__name__)

# Дні тижня, коли ввечері запускається опитування (питаємо за наступний робочий день):
# нд -> пн, пн -> вт, вт -> ср, ср -> чт, чт -> пт
POLL_TRIGGER_DAYS = "sun,mon,tue,wed,thu"


def _tomorrow() -> dt.date:
    return dt.datetime.now(config.TZ).date() + dt.timedelta(days=1)


async def job_start_poll(bot: Bot, conn: aiosqlite.Connection) -> None:
    lesson_date = _tomorrow()
    groups = await db.get_pollable_groups_for_date(conn, lesson_date)
    for g in groups:
        existing = await db.get_response(conn, g["id"], lesson_date)
        if existing is not None:
            continue
        text = (
            f"🗓 {g['choreographer']} — {g['style']} о {g['time']}\n"
            f"Який статус завтрашнього заняття?"
        )
        kb = keyboards.status_keyboard(g["id"], lesson_date.isoformat())
        await bot.send_message(config.CHOREO_GROUP_CHAT_ID, text, reply_markup=kb)
    logger.info("Poll started for %s: %d groups", lesson_date, len(groups))


async def job_reminder(bot: Bot, conn: aiosqlite.Connection) -> None:
    lesson_date = _tomorrow()
    missing = await db.get_missing_groups(conn, lesson_date)
    if not missing:
        return
    names = sorted({g["choreographer"] for g in missing})
    cat = random.choice(config.CAT_EMOJI)
    lines = [f"не отримали ще відповідь від цих котиків {cat}"]
    lines += [f"— {name}" for name in names]
    await bot.send_message(config.CHOREO_GROUP_CHAT_ID, "\n".join(lines))


async def job_final_table(bot: Bot, conn: aiosqlite.Connection) -> None:
    lesson_date = _tomorrow()
    locked = await db.get_locked_groups_for_date(conn, lesson_date)
    pollable = await db.get_pollable_groups_for_date(conn, lesson_date)

    responses_by_group: dict[int, str] = {}
    missing_choreographers: set[str] = set()
    for g in pollable:
        resp = await db.get_response(conn, g["id"], lesson_date)
        if resp is not None:
            responses_by_group[g["id"]] = resp["status_key"]
        else:
            missing_choreographers.add(g["choreographer"])

    year_month = lesson_date.strftime("%Y-%m")
    for name in missing_choreographers:
        await db.record_missed_poll(conn, name, lesson_date)
        count = await db.count_missed_polls_this_month(conn, name, year_month)
        if count >= config.MAX_MISSED_POLLS_PER_MONTH:
            if not await db.alert_already_sent(conn, "missed_polls", name, year_month):
                await bot.send_message(
                    config.OWNER_CHAT_ID,
                    f"❗ Хореограф {name} не реагує на опитування вже {count} раз(и) "
                    f"цього місяця ({year_month}).",
                )
                await db.mark_alert_sent(conn, "missed_polls", name, year_month)

    internal_text = table_builder.build_internal_table(lesson_date, locked, pollable, responses_by_group)
    await bot.send_message(config.CHOREO_GROUP_CHAT_ID, internal_text)

    if config.CLIENT_PUBLISHING_ENABLED and config.CLIENT_GROUP_CHAT_ID:
        client_text = table_builder.build_client_table(lesson_date, locked, pollable, responses_by_group)
        await bot.send_message(config.CLIENT_GROUP_CHAT_ID, client_text)


async def job_monthly_reconciliation(bot: Bot, conn: aiosqlite.Connection) -> None:
    today = dt.datetime.now(config.TZ).date()
    year_month = today.strftime("%Y-%m")
    active_groups = await db.get_active_groups(conn)
    names = sorted({g["choreographer"] for g in active_groups})

    lines = [f"📊 Місячна звірка — {year_month}"]
    for name in names:
        entries = await db.get_month_entries_for_choreographer(conn, name, year_month)
        if not entries:
            continue
        lines.append("")
        lines.append(name)
        cancels = subs = 0
        for e in entries:
            info = config.STATUSES[e["status_key"]]
            d = dt.date.fromisoformat(e["lesson_date"])
            lines.append(f"{d.day:02d}.{d.month:02d} – {e['time']} – {info['label']}")
            if e["status_key"] == "cancelled":
                cancels += 1
            elif e["status_key"] == "substitute":
                subs += 1
        lines.append(f"❗ відмін: {cancels}, замін: {subs}")

    await bot.send_message(config.OWNER_CHAT_ID, "\n".join(lines))


def setup_scheduler(bot: Bot, conn: aiosqlite.Connection) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.TZ)

    poll_h, poll_m = config.POLL_START_TIME.split(":")
    rem_h, rem_m = config.REMINDER_TIME.split(":")
    table_h, table_m = config.TABLE_TIME.split(":")

    scheduler.add_job(
        job_start_poll, CronTrigger(day_of_week=POLL_TRIGGER_DAYS, hour=poll_h, minute=poll_m,
                                     timezone=config.TZ),
        args=(bot, conn), id="start_poll", replace_existing=True,
    )
    scheduler.add_job(
        job_reminder, CronTrigger(day_of_week=POLL_TRIGGER_DAYS, hour=rem_h, minute=rem_m,
                                   timezone=config.TZ),
        args=(bot, conn), id="reminder", replace_existing=True,
    )
    scheduler.add_job(
        job_final_table, CronTrigger(day_of_week=POLL_TRIGGER_DAYS, hour=table_h, minute=table_m,
                                      timezone=config.TZ),
        args=(bot, conn), id="final_table", replace_existing=True,
    )
    scheduler.add_job(
        job_monthly_reconciliation, CronTrigger(day="last", hour=23, minute=0, timezone=config.TZ),
        args=(bot, conn), id="monthly_reconciliation", replace_existing=True,
    )

    return scheduler
