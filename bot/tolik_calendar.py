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
SKIP_WORDS = ("інд", "для себе", "оренд")
# 7.11.2026 (власник): опитувати тих, хто проводив індиви або займався «для себе» на 3 поверсі
PERSONAL_HALLS = ("white", "blue")
PERSONAL_STYLES = ("Індивідуальне", "Для себе")
PERSONAL_ALIASES = {
    "поліна": "Відар Поліна",
    "вікуля": "Сковира Віка",
    "віка": "Сковира Віка",
    "остап": "Федишин Остап",
    "женя": "Мурачева Женя",
    "нетлюх": "Нетлюх Юля",
    "юля н": "Нетлюх Юля",
    "ліля": "Нікітченко Ліля",
    "лера": "Ярцева Лера",
    "аня": "Мисько Аня",
    "саша": "Скороділкін Саша",
    "богдан": "Медвідь Богдан",
    "діма": "Кулик Дмитро",
    "дмитро": "Кулик Дмитро",
}
PERSONAL_SKIP_PEOPLE = ("настя",)  # адміністратори, опитування їм не йде


def classify(title: str) -> Optional[str]:
    t = (title or "").lower()
    if any(w in t for w in SKIP_WORDS) or re.search(r"\bбез\s", t):
        return None
    if "команд" in t:
        return "Команда"
    if "закрит" in t:
        return "Закрита група"
    if "відпрацюв" in t:  # 27.10.2026 (власник): група відпрацьовує заняття з хореографом
        return "Відпрацювання"
    return None


def classify_personal(title: str, individual: bool = False) -> Optional[str]:
    t = (title or "").lower()
    if "оренд" in t or re.search(r"\bбез\s", t):
        return None
    if "для себе" in t:
        return "Для себе"
    if "інд" in t or individual:
        return "Індивідуальне"
    return None


def resolve_choreographer(title: str, personal: bool = False) -> Optional[str]:
    words = re.findall(r"[а-яіїєґa-z']+", (title or "").lower())
    aliases = {**ALIASES, **PERSONAL_ALIASES} if personal else ALIASES
    if len(words) >= 2 and f"{words[0]} {words[1][:1]}" in aliases:
        return aliases[f"{words[0]} {words[1][:1]}"]
    return aliases.get(words[0]) if words else None


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


async def extra_classes(conn: aiosqlite.Connection, d: dt.date,
                        groups: bool = True, personal: bool = False) -> tuple[dict, list]:
    """({хореограф: [groups rows]}, [назви, для яких не знайшли хореографа]).

    groups — команди/закриті/відпрацювання; personal — індиви і «для себе» на 3 поверсі (лише для опитування).
    """
    raw = await tolik_db.get_state(conn, f"extra:{d.isoformat()}")
    if not raw:
        return {}, []
    out: dict = {}
    unknown = []
    for e in json.loads(raw):
        title = e.get("title", "")
        hall = CAL_HALL.get(e.get("hall"))
        style = classify(title) if groups else None
        is_personal = False
        if not style and personal and hall in PERSONAL_HALLS:
            style = classify_personal(title, bool(e.get("individual")))
            is_personal = bool(style)
        if not style:
            continue
        if is_personal and title.lower().startswith(PERSONAL_SKIP_PEOPLE):
            continue
        name = resolve_choreographer(title, personal=is_personal)
        time = (e.get("start") or "")[:5]
        if not name or not hall or not time:
            unknown.append(e.get("title", "?"))
            continue
        gid = await _group_id(conn, name, style, time, hall)
        cur = await conn.execute("SELECT * FROM groups WHERE id = ?", (gid,))
        out.setdefault(name, []).append(await cur.fetchone())
    return out, unknown
