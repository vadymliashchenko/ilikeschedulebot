"""Толік: команди й закриті групи з календаря студії (СР, СБ та інші дні) — погоджено власником 24.10.2026.

Home Assistant (бачить календар) кілька разів на день надсилає POST /tolik/extra?key=… з подіями дня.
Тут лишаємо лише «команда» / «закрита група», без індивідуальних, «для себе», оренди і без «без <хореограф>».
Хореографа знаходимо за першим словом назви (ALIASES). Кожна така подія — рядок у groups
з day_pattern='cal' і active=0 (не потрапляє в розклад/сторіс, але має id для кнопок і статистики).
"""
import datetime as dt
import json
import logging
import re
from typing import Optional

import aiosqlite

from bot import tolik_db

logger = logging.getLogger(__name__)

# перше слово (або два) з назви в календарі → ім'я хореографа в боті
ALIASES = {
    "сковира": "Сковира Віка",
    "нана": "Острянко Нана",
    "рося": "Росінська Віка",
    "катя": "Умрихін Катя",
    "надя": "Горудько Надя",
    "юля к": "Копитко Юля",
}
CAL_HALL = {"grey": "white", "white": "white", "blue": "blue", "green": "green", "pink": "pink", "purple": "purple"}
SKIP_WORDS = ("інд", "для себе", "оренд", "відпрацюв")


def classify(title: str) -> Optional[str]:
    t = (title or "").lower()
    if any(w in t for w in SKIP_WORDS) or re.search(r"\bбез\s", t):
        return None
    if "команд" in t:
        return "Команда"
    if "закрит" in t:
        return "Закрита група"
    return None


def resolve_choreographer(title: str) -> Optional[str]:
    words = re.findall(r"[а-яіїєґa-z']+", (title or "").lower())
    if len(words) >= 2 and f"{words[0]} {words[1][:1]}" in ALIASES:
        return ALIASES[f"{words[0]} {words[1][:1]}"]
    return ALIASES.get(words[0]) if words else None


async def save_extra(conn: aiosqlite.Connection, date: str, events: list) -> int:
    await tolik_db.set_state(conn, f"extra:{date}", json.dumps(events, ensure_ascii=False))
    return len(events)


async def _group_id(conn, choreographer: str, style: str, time: str, hall: str) -> int:
    cur = await conn.execute(
        "SELECT id FROM groups WHERE day_pattern='cal' AND choreographer=? AND style=? AND time=? AND hall=?",
        (choreographer, style, time, hall))
    r = await cur.fetchone()
    if r:
        return r["id"]
    cur = await conn.execute(
        "INSERT INTO groups (choreographer, style, time, level, hall, locked, day_pattern, active) "
        "VALUES (?, ?, ?, NULL, ?, 0, 'cal', 0)", (choreographer, style, time, hall))
    await conn.commit()
    return cur.lastrowid


async def extra_classes(conn: aiosqlite.Connection, d: dt.date) -> tuple[dict, list]:
    """({хореограф: [groups rows]}, [назви, для яких не знайшли хореографа])."""
    raw = await tolik_db.get_state(conn, f"extra:{d.isoformat()}")
    if not raw:
        return {}, []
    out: dict = {}
    unknown = []
    for e in json.loads(raw):
        style = classify(e.get("title", ""))
        if not style:
            continue
        name = resolve_choreographer(e["title"])
        hall = CAL_HALL.get(e.get("hall"))
        time = (e.get("start") or "")[:5]
        if not name or not hall or not time:
            unknown.append(e.get("title", "?"))
            continue
        gid = await _group_id(conn, name, style, time, hall)
        cur = await conn.execute("SELECT * FROM groups WHERE id = ?", (gid,))
        out.setdefault(name, []).append(await cur.fetchone())
    return out, unknown
