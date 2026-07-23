import datetime as dt
from typing import Optional

import aiosqlite

from bot import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    choreographer TEXT NOT NULL,
    style TEXT NOT NULL,
    time TEXT NOT NULL,
    level INTEGER,
    hall TEXT,
    locked INTEGER NOT NULL DEFAULT 0,
    day_pattern TEXT NOT NULL,
    start_date TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    active_since TEXT
);

CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    lesson_date TEXT NOT NULL,
    status_key TEXT NOT NULL,
    extra_name TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(group_id, lesson_date)
);

CREATE TABLE IF NOT EXISTS missed_polls (
    choreographer TEXT NOT NULL,
    lesson_date TEXT NOT NULL,
    PRIMARY KEY (choreographer, lesson_date)
);

CREATE TABLE IF NOT EXISTS choreographer_links (
    choreographer TEXT PRIMARY KEY,
    telegram_user_id INTEGER UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    year_month TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(alert_type, subject, year_month)
);
"""


async def init_db(path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA)
    await conn.commit()
    await _migrate(conn)
    await _seed_groups_if_empty(conn)
    return conn


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Add columns to tables that already existed before this field was introduced."""
    cur = await conn.execute("PRAGMA table_info(responses)")
    columns = {row["name"] for row in await cur.fetchall()}
    if "extra_name" not in columns:
        await conn.execute("ALTER TABLE responses ADD COLUMN extra_name TEXT")
        await conn.commit()


async def _seed_groups_if_empty(conn: aiosqlite.Connection) -> None:
    cur = await conn.execute("SELECT COUNT(*) AS c FROM groups")
    row = await cur.fetchone()
    if row["c"]:
        return
    for entry in config.SCHEDULE:
        await conn.execute(
            """INSERT INTO groups
               (choreographer, style, time, level, hall, locked, day_pattern, start_date, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                entry["choreographer"],
                entry["style"],
                entry["time"],
                entry.get("level"),
                entry.get("hall"),
                int(entry.get("locked", False)),
                entry["day_pattern"],
                entry.get("start_date"),
            ),
        )
    await conn.commit()


def _weekday_patterns(weekday: int) -> list[str]:
    return [p for p, days in config.DAY_PATTERN_WEEKDAYS.items() if weekday in days]


async def get_scheduled_groups_for_date(
    conn: aiosqlite.Connection, lesson_date: dt.date
) -> list[aiosqlite.Row]:
    """All active groups (locked, upcoming or pollable) whose day_pattern covers this date."""
    patterns = _weekday_patterns(lesson_date.weekday())
    if not patterns:
        return []
    placeholders = ",".join("?" for _ in patterns)
    date_str = lesson_date.isoformat()
    cur = await conn.execute(
        f"""SELECT * FROM groups
            WHERE active = 1
              AND day_pattern IN ({placeholders})
              AND (active_since IS NULL OR active_since <= ?)
            ORDER BY time, choreographer""",
        (*patterns, date_str),
    )
    return await cur.fetchall()


async def get_pollable_groups_for_date(
    conn: aiosqlite.Connection, lesson_date: dt.date
) -> list[aiosqlite.Row]:
    """Scheduled groups that are not locked and already started - asked in the daily poll."""
    date_str = lesson_date.isoformat()
    rows = await get_scheduled_groups_for_date(conn, lesson_date)
    return [
        r for r in rows
        if not r["locked"] and (r["start_date"] is None or r["start_date"] <= date_str)
    ]


async def get_locked_groups_for_date(
    conn: aiosqlite.Connection, lesson_date: dt.date
) -> list[aiosqlite.Row]:
    rows = await get_scheduled_groups_for_date(conn, lesson_date)
    return [r for r in rows if r["locked"]]


async def get_upcoming_groups_for_date(
    conn: aiosqlite.Connection, lesson_date: dt.date
) -> list[aiosqlite.Row]:
    """Groups shown for info but not yet started (not polled)."""
    date_str = lesson_date.isoformat()
    rows = await get_scheduled_groups_for_date(conn, lesson_date)
    return [
        r for r in rows
        if not r["locked"] and r["start_date"] is not None and r["start_date"] > date_str
    ]


async def save_response(
    conn: aiosqlite.Connection,
    group_id: int,
    lesson_date: dt.date,
    status_key: str,
    extra_name: Optional[str] = None,
) -> None:
    await conn.execute(
        """INSERT INTO responses (group_id, lesson_date, status_key, extra_name, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(group_id, lesson_date) DO UPDATE SET
               status_key = excluded.status_key,
               extra_name = excluded.extra_name""",
        (
            group_id, lesson_date.isoformat(), status_key, extra_name,
            dt.datetime.now(config.TZ).isoformat(),
        ),
    )
    await conn.commit()


async def get_response(
    conn: aiosqlite.Connection, group_id: int, lesson_date: dt.date
) -> Optional[aiosqlite.Row]:
    cur = await conn.execute(
        "SELECT * FROM responses WHERE group_id = ? AND lesson_date = ?",
        (group_id, lesson_date.isoformat()),
    )
    return await cur.fetchone()


async def get_missing_groups(
    conn: aiosqlite.Connection, lesson_date: dt.date
) -> list[aiosqlite.Row]:
    pollable = await get_pollable_groups_for_date(conn, lesson_date)
    missing = []
    for g in pollable:
        resp = await get_response(conn, g["id"], lesson_date)
        if resp is None:
            missing.append(g)
    return missing


async def record_missed_poll(
    conn: aiosqlite.Connection, choreographer: str, lesson_date: dt.date
) -> None:
    await conn.execute(
        """INSERT OR IGNORE INTO missed_polls (choreographer, lesson_date) VALUES (?, ?)""",
        (choreographer, lesson_date.isoformat()),
    )
    await conn.commit()


async def count_missed_polls_this_month(
    conn: aiosqlite.Connection, choreographer: str, year_month: str
) -> int:
    cur = await conn.execute(
        """SELECT COUNT(*) AS c FROM missed_polls
           WHERE choreographer = ? AND lesson_date LIKE ?""",
        (choreographer, f"{year_month}%"),
    )
    row = await cur.fetchone()
    return row["c"]


async def count_status_for_group_month(
    conn: aiosqlite.Connection, group_id: int, status_key: str, year_month: str
) -> int:
    cur = await conn.execute(
        """SELECT COUNT(*) AS c FROM responses
           WHERE group_id = ? AND status_key = ? AND lesson_date LIKE ?""",
        (group_id, status_key, f"{year_month}%"),
    )
    row = await cur.fetchone()
    return row["c"]


async def get_month_entries_for_choreographer(
    conn: aiosqlite.Connection, choreographer: str, year_month: str
) -> list[aiosqlite.Row]:
    cur = await conn.execute(
        """SELECT r.lesson_date, r.status_key, r.extra_name, g.time, g.style
           FROM responses r JOIN groups g ON g.id = r.group_id
           WHERE g.choreographer = ? AND r.lesson_date LIKE ?
           ORDER BY r.lesson_date, g.time""",
        (choreographer, f"{year_month}%"),
    )
    return await cur.fetchall()


async def alert_already_sent(
    conn: aiosqlite.Connection, alert_type: str, subject: str, year_month: str
) -> bool:
    cur = await conn.execute(
        """SELECT 1 FROM alerts_sent WHERE alert_type = ? AND subject = ? AND year_month = ?""",
        (alert_type, subject, year_month),
    )
    return (await cur.fetchone()) is not None


async def mark_alert_sent(
    conn: aiosqlite.Connection, alert_type: str, subject: str, year_month: str
) -> None:
    await conn.execute(
        """INSERT OR IGNORE INTO alerts_sent (alert_type, subject, year_month, created_at)
           VALUES (?, ?, ?, ?)""",
        (alert_type, subject, year_month, dt.datetime.now(config.TZ).isoformat()),
    )
    await conn.commit()


async def register_choreographer_link(
    conn: aiosqlite.Connection, choreographer: str, telegram_user_id: int
) -> None:
    await conn.execute(
        """INSERT INTO choreographer_links (choreographer, telegram_user_id)
           VALUES (?, ?)
           ON CONFLICT(choreographer) DO UPDATE SET telegram_user_id = excluded.telegram_user_id""",
        (choreographer, telegram_user_id),
    )
    await conn.commit()


async def get_choreographer_by_telegram_id(
    conn: aiosqlite.Connection, telegram_user_id: int
) -> Optional[str]:
    cur = await conn.execute(
        "SELECT choreographer FROM choreographer_links WHERE telegram_user_id = ?",
        (telegram_user_id,),
    )
    row = await cur.fetchone()
    return row["choreographer"] if row else None


async def is_choreographer_linked(conn: aiosqlite.Connection, choreographer: str) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM choreographer_links WHERE choreographer = ?", (choreographer,)
    )
    return (await cur.fetchone()) is not None


async def get_all_links(conn: aiosqlite.Connection) -> list[aiosqlite.Row]:
    cur = await conn.execute(
        "SELECT choreographer, telegram_user_id FROM choreographer_links ORDER BY choreographer"
    )
    return await cur.fetchall()


async def unlink_choreographer(conn: aiosqlite.Connection, choreographer: str) -> None:
    await conn.execute(
        "DELETE FROM choreographer_links WHERE choreographer = ?", (choreographer,)
    )
    await conn.commit()


async def count_active_groups_at_slot(
    conn: aiosqlite.Connection, day_pattern: str, time: str
) -> int:
    cur = await conn.execute(
        """SELECT COUNT(*) AS c FROM groups
           WHERE active = 1 AND day_pattern = ? AND time = ?""",
        (day_pattern, time),
    )
    row = await cur.fetchone()
    return row["c"]


async def add_group(
    conn: aiosqlite.Connection,
    choreographer: str,
    style: str,
    time: str,
    day_pattern: str,
    active_since: str,
) -> None:
    await conn.execute(
        """INSERT INTO groups (choreographer, style, time, day_pattern, active_since, active)
           VALUES (?, ?, ?, ?, ?, 1)""",
        (choreographer, style, time, day_pattern, active_since),
    )
    await conn.commit()


async def deactivate_group(conn: aiosqlite.Connection, group_id: int) -> None:
    await conn.execute("UPDATE groups SET active = 0 WHERE id = ?", (group_id,))
    await conn.commit()


async def edit_group(
    conn: aiosqlite.Connection, group_id: int, style: Optional[str], time: Optional[str]
) -> None:
    if style is not None:
        await conn.execute("UPDATE groups SET style = ? WHERE id = ?", (style, group_id))
    if time is not None:
        await conn.execute("UPDATE groups SET time = ? WHERE id = ?", (time, group_id))
    await conn.commit()


async def get_active_groups(conn: aiosqlite.Connection) -> list[aiosqlite.Row]:
    cur = await conn.execute(
        "SELECT * FROM groups WHERE active = 1 ORDER BY choreographer, time"
    )
    return await cur.fetchall()


async def get_group(conn: aiosqlite.Connection, group_id: int) -> Optional[aiosqlite.Row]:
    cur = await conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,))
    return await cur.fetchone()


async def get_group_by_name_time_pattern(
    conn: aiosqlite.Connection, choreographer: str, day_pattern: str, time: str
) -> Optional[aiosqlite.Row]:
    cur = await conn.execute(
        """SELECT * FROM groups
           WHERE choreographer = ? AND day_pattern = ? AND time = ? AND active = 1""",
        (choreographer, day_pattern, time),
    )
    return await cur.fetchone()
