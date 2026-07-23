import datetime as dt
import logging
from typing import Optional

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.types import FSInputFile, Message

from bot import config, db, story_builder

logger = logging.getLogger(__name__)
router = Router(name="corrections")
router.message.filter(F.chat.id == config.INSTAGRAM_CHAT_ID)

_TRIGGER = "/переробити"

# Порядок важливий: перевіряємо специфічніші/заперечні фрази раніше загальніших.
_KEYWORD_TO_STATUS = [
    ("не можна", "second_no"),
    ("зйомк", "filming"),
    ("замін", "substitute"),
    ("відмін", "cancelled"),
    ("формат мк", "last_mk"),
    (" мк", "last_mk"),
    ("майстер", "last_mk"),
    ("нова", "first"),
    ("хореографі", "first"),
    ("можна", "second_ok"),
]


def _find_choreographer(text: str) -> Optional[str]:
    low = text.lower()
    matches = [
        name for name in config.CHOREOGRAPHERS
        if all(part.lower() in low for part in name.split())
    ]
    return matches[0] if len(matches) == 1 else None


def _find_status(text: str) -> Optional[str]:
    low = text.lower()
    for keyword, status_key in _KEYWORD_TO_STATUS:
        if keyword in low:
            return status_key
    return None


def _tomorrow() -> dt.date:
    return dt.datetime.now(config.TZ).date() + dt.timedelta(days=1)


@router.message(F.text.contains(_TRIGGER))
async def handle_correction(message: Message, conn: aiosqlite.Connection, bot: Bot) -> None:
    text = message.text.replace(_TRIGGER, "")

    choreographer = _find_choreographer(text)
    if choreographer is None:
        await message.reply(
            "Не розпізнав, кого саме стосується виправлення. Напишіть, будь ласка, "
            "повне ім'я та прізвище хореографа так, як у списку."
        )
        return

    status_key = _find_status(text)
    if status_key is None:
        await message.reply(
            f"Розпізнав хореографа ({choreographer}), але не зрозумів новий статус. "
            f"Напишіть словами: можна / не можна / нова хореографія / зйомка / заміна / "
            f"відміна / формат мк."
        )
        return

    lesson_date = _tomorrow()
    pollable = await db.get_pollable_groups_for_date(conn, lesson_date)
    matches = [g for g in pollable if g["choreographer"] == choreographer]

    if not matches:
        await message.reply(
            f"Не знайшов активних занять {choreographer} на {lesson_date.strftime('%d.%m')}."
        )
        return

    if len(matches) > 1:
        options = ", ".join(f"{g['time']} {g['style']}" for g in matches)
        await message.reply(
            f"У {choreographer} декілька занять цього дня ({options}). "
            f"Уточніть, будь ласка, час або стиль у повідомленні."
        )
        return

    group = matches[0]
    extra_name = None
    if status_key == "substitute":
        after = text.lower().split("замін", 1)[1] if "замін" in text.lower() else ""
        candidate = after.strip(" ,.:-нae").strip()
        extra_name = candidate.title() if candidate else None

    await db.save_response(conn, group["id"], lesson_date, status_key, extra_name=extra_name)
    await message.reply(f"Виправив: {choreographer} ({group['style']}, {group['time']}) - оновлюю макет.")

    await _send_updated_pdf(bot, conn, lesson_date)


async def _send_updated_pdf(bot: Bot, conn: aiosqlite.Connection, lesson_date: dt.date) -> None:
    pdf_path = await story_builder.build_day_pdf(conn, lesson_date, config.STORY_OUTPUT_DIR)
    if pdf_path is None:
        return
    await bot.send_document(
        config.INSTAGRAM_CHAT_ID,
        FSInputFile(pdf_path),
        caption=f"Оновлений макет на {lesson_date.strftime('%d.%m')}.",
    )
