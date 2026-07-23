import datetime as dt
import logging

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import config, db, keyboards, states

logger = logging.getLogger(__name__)
router = Router(name="poll")


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    await message.answer(f"ID цього чату: {message.chat.id}")


@router.callback_query(F.data.startswith("status:"))
async def handle_status(
    callback: CallbackQuery, conn: aiosqlite.Connection, bot: Bot, state: FSMContext
) -> None:
    _, group_id_str, date_str, status_key = callback.data.split(":", 3)
    group_id = int(group_id_str)
    lesson_date = dt.date.fromisoformat(date_str)

    group = await db.get_group(conn, group_id)
    if group is None:
        await callback.answer("Групу не знайдено.", show_alert=True)
        return

    choreographer = group["choreographer"]
    linked = await db.get_choreographer_by_telegram_id(conn, callback.from_user.id)

    if linked is not None and linked != choreographer:
        await callback.answer(
            f"Це опитування для {choreographer}, а ви підтверджені як {linked}.",
            show_alert=True,
        )
        return

    if linked is None:
        if await db.is_choreographer_linked(conn, choreographer):
            await callback.answer(
                "Цей хореограф вже підтверджений іншим користувачем. "
                "Зверніться до адміністратора.",
                show_alert=True,
            )
            return
        await db.register_choreographer_link(conn, choreographer, callback.from_user.id)

    if status_key == "substitute":
        await callback.answer("Оберіть, хто заміняє")
        kb = keyboards.substitute_keyboard(group_id, date_str, choreographer)
        await callback.message.edit_reply_markup(reply_markup=kb)
        return

    await db.save_response(conn, group_id, lesson_date, status_key)

    info = config.STATUSES[status_key]
    await callback.answer(f"Записано: {info['label']}")

    await _check_limits(bot, conn, group, status_key, lesson_date)


@router.callback_query(F.data.startswith("subname:"))
async def handle_substitute_name(
    callback: CallbackQuery, conn: aiosqlite.Connection, bot: Bot
) -> None:
    _, group_id_str, date_str, idx_str = callback.data.split(":", 3)
    group_id = int(group_id_str)
    lesson_date = dt.date.fromisoformat(date_str)
    name = config.CHOREOGRAPHERS[int(idx_str)]

    group = await db.get_group(conn, group_id)
    if group is None:
        await callback.answer("Групу не знайдено.", show_alert=True)
        return

    await db.save_response(conn, group_id, lesson_date, "substitute", extra_name=name)
    await callback.answer(f"Записано: заміна - {name}")
    await callback.message.edit_text(f"🔀 {group['choreographer']} - заміна: {name}")

    await _check_limits(bot, conn, group, "substitute", lesson_date)


@router.callback_query(F.data.startswith("subother:"))
async def handle_substitute_other(callback: CallbackQuery, state: FSMContext) -> None:
    _, group_id_str, date_str = callback.data.split(":", 2)
    await state.set_state(states.SubstituteInput.waiting_name)
    await state.update_data(group_id=int(group_id_str), date_str=date_str)
    await callback.answer()
    await callback.message.edit_text("Напишіть, будь ласка, ім'я того, хто заміняє:")


@router.message(states.SubstituteInput.waiting_name)
async def handle_substitute_text(
    message: Message, state: FSMContext, conn: aiosqlite.Connection, bot: Bot
) -> None:
    data = await state.get_data()
    group_id = data["group_id"]
    lesson_date = dt.date.fromisoformat(data["date_str"])
    name = message.text.strip()
    await state.clear()

    group = await db.get_group(conn, group_id)
    if group is None:
        await message.answer("Групу не знайдено.")
        return

    await db.save_response(conn, group_id, lesson_date, "substitute", extra_name=name)
    await message.answer(f"🔀 {group['choreographer']} - заміна: {name}")

    await _check_limits(bot, conn, group, "substitute", lesson_date)


async def _check_limits(
    bot: Bot, conn: aiosqlite.Connection, group: aiosqlite.Row, status_key: str, lesson_date: dt.date
) -> None:
    year_month = lesson_date.strftime("%Y-%m")
    choreographer = group["choreographer"]
    group_id = group["id"]

    limit_map = {
        "cancelled": (config.MAX_CANCELS_PER_MONTH, "cancel_limit", "відмін"),
        "substitute": (config.MAX_SUBSTITUTES_PER_MONTH, "substitute_limit", "замін"),
    }
    if status_key not in limit_map:
        return

    max_allowed, alert_type, word = limit_map[status_key]
    count = await db.count_status_for_group_month(conn, group_id, status_key, year_month)
    if count <= max_allowed:
        return

    subject = f"group:{group_id}"
    if await db.alert_already_sent(conn, alert_type, subject, year_month):
        return

    await bot.send_message(
        config.OWNER_CHAT_ID,
        f"❗ {choreographer} ({group['style']}, {group['time']}) перевищив(ла) ліміт {word} "
        f"цього місяця: {count} за {year_month}.",
    )
    await db.mark_alert_sent(conn, alert_type, subject, year_month)
