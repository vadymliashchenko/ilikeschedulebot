import logging

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, db, scheduler

logger = logging.getLogger(__name__)
router = Router(name="admin")
router.message.filter(F.chat.id == config.OWNER_CHAT_ID)
router.callback_query.filter(F.message.chat.id == config.OWNER_CHAT_ID)


@router.message(Command("pollnow"))
async def cmd_poll_now(message: Message, conn: aiosqlite.Connection, bot: Bot) -> None:
    await scheduler.job_start_poll(bot, conn)
    await message.answer("Опитування за завтра запущено вручну.")


@router.message(Command("remindnow"))
async def cmd_remind_now(message: Message, conn: aiosqlite.Connection, bot: Bot) -> None:
    await scheduler.job_reminder(bot, conn)
    await message.answer("Нагадування надіслано вручну.")


@router.message(Command("tablenow"))
async def cmd_table_now(message: Message, conn: aiosqlite.Connection, bot: Bot) -> None:
    await scheduler.job_final_table(bot, conn)
    await message.answer("Таблицю опубліковано вручну.")


@router.message(Command("clientnow"))
async def cmd_client_now(message: Message, conn: aiosqlite.Connection, bot: Bot) -> None:
    await scheduler.job_client_repost(bot, conn)
    await message.answer("Перепост у клієнтську групу виконано вручну (якщо ввімкнено).")


@router.message(Command("links"))
async def cmd_links(message: Message, conn: aiosqlite.Connection) -> None:
    links = await db.get_all_links(conn)
    if not links:
        await message.answer("Ще нікого не прив'язано до Telegram-акаунтів.")
        return
    lines = ["Прив'язки хореограф → Telegram ID:"]
    kb_rows = []
    for row in links:
        lines.append(f"- {row['choreographer']} → {row['telegram_user_id']}")
        kb_rows.append(
            [InlineKeyboardButton(
                text=f"🔓 Скинути: {row['choreographer']}",
                callback_data=f"unlink:{row['choreographer']}",
            )]
        )
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer("\n".join(lines), reply_markup=kb)


@router.message(Command("unlinked"))
async def cmd_unlinked(message: Message, conn: aiosqlite.Connection) -> None:
    links = await db.get_all_links(conn)
    linked_names = {row["choreographer"] for row in links}
    missing = [name for name in config.CHOREOGRAPHERS if name not in linked_names]

    if not missing:
        await message.answer("Усі хореографи вже підключили приватний чат з ботом. ✅")
        return

    lines = ["Ще чекаємо на /start у приваті від:"] + [f"- {name}" for name in missing]
    await message.answer("\n".join(lines))


@router.callback_query(F.data.startswith("unlink:"))
async def cb_unlink(callback: CallbackQuery, conn: aiosqlite.Connection) -> None:
    name = callback.data.split(":", 1)[1]
    await db.unlink_choreographer(conn, name)
    await callback.answer(f"Прив'язку {name} скинуто.")
    await callback.message.edit_text(f"Прив'язку для {name} скинуто. Наступний, хто натисне її кнопку, буде прив'язаний заново.")
