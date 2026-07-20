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
                client_version: bool) -> tuple[list[str], set[str]]:
    entries = []
    used_emoji: set[str] = set()

    for g in locked_groups:
        entries.append((g["time"], f"{g['time']} — {g['choreographer']} — {g['style']}{_level_suffix(g['level'])} — 🔒 закрита"))
        used_emoji.add("🔒")

    for g in pollable_groups:
        status_key = responses_by_group.get(g["id"])
        style_part = f"{g['style']}{_level_suffix(g['level'])}"
        if status_key is None:
            used_emoji.add("⚠️")
            if client_version:
                line = f"{g['time']} — {g['choreographer']} — {style_part}"
            else:
                line = f"{g['time']} — {g['choreographer']} — {style_part} — ⚠️ немає відповіді"
        else:
            info = config.STATUSES[status_key]
            used_emoji.add(info["emoji"])
            line = f"{g['time']} — {g['choreographer']} — {style_part} — {info['emoji']} {info['label']}"
        entries.append((g["time"], line))

    entries.sort(key=lambda x: x[0])
    return [line for _, line in entries], used_emoji


def _build_legend(used_emoji: set[str]) -> str:
    lines = ["Що означають значки:"]
    for emoji in _LEGEND_ORDER:
        if emoji in used_emoji:
            lines.append(f"{emoji} — {config.STATUS_EMOJI_LEGEND[emoji]}")
    return "\n".join(lines)


def build_internal_table(lesson_date: dt.date, locked_groups, pollable_groups,
                          responses_by_group: dict[int, str]) -> str:
    rows, used_emoji = _build_rows(lesson_date, locked_groups, pollable_groups,
                                    responses_by_group, client_version=False)
    header = f"📋 Розклад на завтра — {_format_date(lesson_date)}"
    body = "\n".join(rows) if rows else "На завтра активних груп немає."
    legend = _build_legend(used_emoji)
    return f"{header}\n\n{body}\n\n{legend}"


def build_client_table(lesson_date: dt.date, locked_groups, pollable_groups,
                        responses_by_group: dict[int, str]) -> str:
    rows, used_emoji = _build_rows(lesson_date, locked_groups, pollable_groups,
                                    responses_by_group, client_version=True)
    header = f"📋 Розклад на завтра — {_format_date(lesson_date)}"
    body = "\n".join(rows) if rows else "На завтра активних груп немає."
    legend = _build_legend(used_emoji)
    return f"{header}\n\n{body}\n\n{legend}"
