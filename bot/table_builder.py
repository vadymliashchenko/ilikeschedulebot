import datetime as dt
from typing import Optional

from bot import config

_LEGEND_ORDER = ["✅", "❌", "🎓", "🚫", "⚠️", "🔒"]

_MONTHS_GEN = {
    1: "січня", 2: "лютого", 3: "березня", 4: "квітня", 5: "травня", 6: "червня",
    7: "липня", 8: "серпня", 9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня",
}


def _format_date(d: dt.date) -> str:
    return f"{d.day} {_MONTHS_GEN[d.month]}"


def _level_suffix(level: Optional[int]) -> str:
    if level is None:
        return ""
    return f" {'★' * level}"


def _build_rows(lesson_date: dt.date, locked_groups, pollable_groups, responses_by_group,
                client_version: bool) -> tuple[list[tuple[str, str]], set[str]]:
    entries = []
    used_emoji: set[str] = set()

    for g in locked_groups:
        head = f"<b>{g['time']} — {g['choreographer']}</b>"
        line = f"{head} — {g['style']}{_level_suffix(g['level'])} — 🔒 закрита"
        entries.append((g["time"], line))
        used_emoji.add("🔒")

    for g in pollable_groups:
        status_key = responses_by_group.get(g["id"])
        head = f"<b>{g['time']} — {g['choreographer']}</b>"
        style_part = f"{g['style']}{_level_suffix(g['level'])}"
        if status_key is None:
            used_emoji.add("⚠️")
            if client_version:
                line = f"{head} — {style_part}"
            else:
                line = f"{head} — {style_part} — ⚠️ немає відповіді"
        else:
            info = config.STATUSES[status_key]
            used_emoji.add(info["emoji"])
            line = f"{head} — {style_part} — {info['emoji']} {info['label']}"
        entries.append((g["time"], line))

    entries.sort(key=lambda x: x[0])
    return entries, used_emoji


def _join_with_time_gaps(entries: list[tuple[str, str]]) -> str:
    lines = []
    prev_time = None
    for time, line in entries:
        if prev_time is not None and time != prev_time:
            lines.append("")
        lines.append(line)
        prev_time = time
    return "\n".join(lines)


def _build_legend(used_emoji: set[str]) -> str:
    lines = ["Що означають значки:"]
    for emoji in _LEGEND_ORDER:
        if emoji in used_emoji:
            lines.append(f"{emoji} — {config.STATUS_EMOJI_LEGEND[emoji]}")
    return "\n".join(lines)


def build_internal_table(lesson_date: dt.date, locked_groups, pollable_groups,
                          responses_by_group: dict[int, str]) -> str:
    entries, used_emoji = _build_rows(lesson_date, locked_groups, pollable_groups,
                                       responses_by_group, client_version=False)
    header = f"📋 Розклад на завтра — {_format_date(lesson_date)}"
    body = _join_with_time_gaps(entries) if entries else "На завтра активних груп немає."
    legend = _build_legend(used_emoji)
    return f"{header}\n\n{body}\n\n{legend}"


def build_client_table(lesson_date: dt.date, locked_groups, pollable_groups,
                        responses_by_group: dict[int, str]) -> str:
    entries, used_emoji = _build_rows(lesson_date, locked_groups, pollable_groups,
                                       responses_by_group, client_version=True)
    header = f"📋 Розклад на завтра — {_format_date(lesson_date)}"
    body = _join_with_time_gaps(entries) if entries else "На завтра активних груп немає."
    legend = _build_legend(used_emoji)
    return f"{header}\n\n{body}\n\n{legend}"
