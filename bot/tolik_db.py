"""Таблиці Толіка. Окремо від основної схеми — лише CREATE IF NOT EXISTS, старі таблиці не чіпаємо."""
import datetime as dt
from typing import Optional

import aiosqlite

from bot import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS comfort_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_date TEXT NOT NULL,
    role TEXT NOT NULL,              -- choreographer | admin
    person TEXT NOT NULL,            -- ім'я хореографа / феї
    telegram_user_id INTEGER,
    group_id INTEGER,                -- для хореографів
    hall TEXT,                       -- white/blue/... для хореографів
    floor INTEGER,                   -- для адміністраторів
    room TEXT,                       -- reception | dressing (адміністратори) / hall
    class_time TEXT,
    style TEXT,
    answer TEXT,                     -- cold | normal | hot
    thermometer REAL,
    is_test INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(survey_date, role, person, group_id, floor, room, is_test)
);

CREATE TABLE IF NOT EXISTS tolik_admins (
    telegram_user_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    full_name TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tolik_adjust (
    key TEXT PRIMARY KEY,            -- style:<стиль> | room:<поверх>:<reception|dressing>
    offset REAL NOT NULL,
    step REAL NOT NULL,
    last_dir INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS tolik_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return dt.datetime.now(config.TZ).isoformat(timespec="seconds")


async def init(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA)
    await conn.commit()


async def get_state(conn: aiosqlite.Connection, key: str) -> Optional[str]:
    cur = await conn.execute("SELECT value FROM tolik_state WHERE key = ?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else None


async def set_state(conn: aiosqlite.Connection, key: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO tolik_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await conn.commit()


async def save_comfort(conn: aiosqlite.Connection, **f) -> None:
    """Upsert відповіді (повторне натискання змінює відповідь)."""
    keys = ["survey_date", "role", "person", "telegram_user_id", "group_id", "hall", "floor",
            "room", "class_time", "style", "answer", "thermometer", "is_test"]
    vals = [f.get(k) for k in keys]
    vals[keys.index("is_test")] = int(bool(f.get("is_test")))
    # SQLite UNIQUE не вважає NULL рівними, тому NULL у ключі замінюємо на 0/''
    for k, default in (("group_id", 0), ("floor", 0), ("room", "")):
        if vals[keys.index(k)] is None:
            vals[keys.index(k)] = default
    update = ", ".join(
        f"{c} = COALESCE(excluded.{c}, comfort_responses.{c})" for c in ("answer", "thermometer")
    )
    await conn.execute(
        f"""INSERT INTO comfort_responses ({", ".join(keys)}, updated_at)
            VALUES ({", ".join("?" for _ in keys)}, ?)
            ON CONFLICT(survey_date, role, person, group_id, floor, room, is_test)
            DO UPDATE SET {update}, updated_at = excluded.updated_at""",
        (*vals, now_iso()),
    )
    await conn.commit()


async def get_comfort_for(conn, survey_date: str, role: str, person: str, is_test: bool):
    cur = await conn.execute(
        "SELECT * FROM comfort_responses WHERE survey_date = ? AND role = ? AND person = ? AND is_test = ?",
        (survey_date, role, person, int(is_test)),
    )
    return await cur.fetchall()


async def stats_since(conn, since: dt.date):
    cur = await conn.execute(
        """SELECT role, hall, floor, room, answer, COUNT(*) AS n FROM comfort_responses
           WHERE survey_date >= ? AND is_test = 0 AND answer IS NOT NULL
           GROUP BY role, hall, floor, room, answer ORDER BY role, hall, floor, room""",
        (since.isoformat(),),
    )
    return await cur.fetchall()


# --- адміністратори ---
async def upsert_admin_request(conn, telegram_user_id: int, name: str, full_name: str) -> None:
    await conn.execute(
        """INSERT INTO tolik_admins (telegram_user_id, name, full_name, approved, created_at)
           VALUES (?, ?, ?, 0, ?)
           ON CONFLICT(telegram_user_id) DO UPDATE SET name = excluded.name,
               full_name = excluded.full_name""",
        (telegram_user_id, name, full_name, now_iso()),
    )
    await conn.commit()


async def set_admin_approved(conn, telegram_user_id: int, approved: bool) -> None:
    if approved:
        await conn.execute("UPDATE tolik_admins SET approved = 1 WHERE telegram_user_id = ?",
                           (telegram_user_id,))
    else:
        await conn.execute("DELETE FROM tolik_admins WHERE telegram_user_id = ?", (telegram_user_id,))
    await conn.commit()


async def get_admin(conn, telegram_user_id: int):
    cur = await conn.execute("SELECT * FROM tolik_admins WHERE telegram_user_id = ?", (telegram_user_id,))
    return await cur.fetchone()


async def get_approved_admins(conn):
    cur = await conn.execute("SELECT * FROM tolik_admins WHERE approved = 1 ORDER BY name")
    return await cur.fetchall()


async def get_all_admins(conn):
    cur = await conn.execute("SELECT * FROM tolik_admins ORDER BY approved DESC, name")
    return await cur.fetchall()
