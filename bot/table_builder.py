import datetime as dt
from typing import Optional

from bot import config

_LEGEND_ORDER = ["🆕", "🎦", "🔀", "🎓", "🚫", "🔒"]


def _format_header_date(d: dt.date) -> str:
    return f"{d.day:02d}.{d.month:02d}"


def _format_short_date(d: dt.date) -> str:
    return f"{d.day:02d}.{d.month:02d}"


def _level_suffix(level: Optional[int]) -> str:
    if level is None:
        return ""
    return f" {'★' * level}"


def _hall_emoji(hall: Optional[str]) -> str:
    return config.HALL_EMOJI.get(hall, "")


def _row(hall: Optional[str], name: str, style_line: str, status_line: Optional[str]) -> str:
    lines = [f"{_hall_emoji(hall)}<b>{name}</b>", style_line]
    if status_line is not None:
        lines.append(f"• {status_line}")
    return "\n".join(lines)


def _build_rows(lesson_date: dt.date, locked_groups, upcoming_groups, pollable_groups,
                 responses_by_group, client_version: bool) -> tuple[list[tuple[str, str]], set[str]]:
    entries = []
    used_emoji: set[str] = set()

    for g in locked_groups:
        style_line = f"{g['style']}{_level_suffix(g['level'])}"
        line = _row(g["hall"], g["choreographer"], style_line, "🔒 закрита група")
        entries.append((g["time"], line))
        used_emoji.add("🔒")

    for g in upcoming_groups:
        style_line = f"{g['style']}{_level_suffix(g['level'])}"
        start = dt.date.fromisoformat(g["start_date"])
        line = _row(g["hall"], g["choreographer"], style_line, f"старт з {_format_short_date(start)}")
        entries.append((g["time"], line))

    for g in pollable_groups:
        resp = responses_by_group.get(g["id"])
        style_line = f"{g['style']}{_level_suffix(g['level'])}"
        if resp is None:
            used_emoji.add("⚠️")
            status_line = None if client_version else "⚠️ хореограф ще не відповів"
        else:
            status_key = resp["status_key"]
            info = config.STATUSES[status_key]
            if status_key == "substitute" and resp["extra_name"]:
                status_line = f"🔀 ЗАМІНА, {resp['extra_name']}, можна приєднатися"
            else:
                status_line = info["phrase"]
            for e in _LEGEND_ORDER:
                if e in status_line:
                    used_emoji.add(e)
        entries.append((g["time"], _row(g["hall"], g["choreographer"], style_line, status_line)))

    entries.sort(key=lambda x: x[0])
    return entries, used_emoji


def _group_by_time(entries: list[tuple[str, str]]) -> str:
    blocks = []
    current_time = None
    current_rows: list[str] = []
    for time, row in entries:
        if time != current_time:
            if current_rows:
                blocks.append(f"<b>{current_time}</b>\n\n" + "\n\n".join(current_rows))
            current_time = time
            current_rows = []
        current_rows.append(row)
    if current_rows:
        blocks.append(f"<b>{current_time}</b>\n\n" + "\n\n".join(current_rows))
    return "\n\n".join(blocks)


def _build_legend(used_emoji: set[str]) -> str:
    lines = [
        "Що означають значки:",
        "★ - група початкового рівня",
        "★★ - група середнього рівня",
    ]
    for emoji in _LEGEND_ORDER:
        if emoji in used_emoji:
            lines.append(f"{emoji} - {config.STATUS_EMOJI_LEGEND[emoji]}")
    return "\n".join(lines)


def _header(lesson_date: dt.date) -> str:
    day_abbr = config.DAY_ABBR[lesson_date.weekday()]
    return f"🗓 РОЗКЛАД {day_abbr} ({_format_header_date(lesson_date)})"


def build_internal_table(lesson_date: dt.date, locked_groups, upcoming_groups, pollable_groups,
                          responses_by_group: dict[int, str]) -> str:
    entries, used_emoji = _build_rows(lesson_date, locked_groups, upcoming_groups, pollable_groups,
                                       responses_by_group, client_version=False)
    body = _group_by_time(entries) if entries else "На цей день активних груп немає."
    legend = _build_legend(used_emoji)
    return f"{_header(lesson_date)}\n\n{body}\n\n{legend}"


def build_client_table(lesson_date: dt.date, locked_groups, upcoming_groups, pollable_groups,
                        responses_by_group: dict[int, str]) -> str:
    entries, used_emoji = _build_rows(lesson_date, locked_groups, upcoming_groups, pollable_groups,
                                       responses_by_group, client_version=True)
    body = _group_by_time(entries) if entries else "На цей день активних груп немає."
    legend = _build_legend(used_emoji)
    return f"{_header(lesson_date)}\n\n{body}\n\n{legend}"
