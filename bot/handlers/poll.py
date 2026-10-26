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


def _table_already_published(lesson_date: dt.date) -> bool:
    """Чи вже вийшла таблиця (о TABLE_TIME напередодні заняття)."""
    h, m = map(int, config.TABLE_TIME.split(":"))
    deadline = dt.datetime.combine(
        lesson_date - dt.timedelta(days=1), dt.time(h, m), tzinfo=config.TZ
    )
    return dt.datetime.now(config.TZ) >= deadline


def _lesson_passed(lesson_date: dt.date) -> bool:
    return lesson_date < dt.datetime.now(config.TZ).date()


async def _confirm(
    bot: Bot, conn: aiosqlite.Connection, group: aiosqlite.Row,
    lesson_date: dt.date, choice: str, can_change: bool, reply,
) -> None:
    """Підтвердження хореографу: що саме обрано, на що ще треба відповісти,
    і (якщо таблиця вже вийшла) - пізня відповідь: знімаємо 'не відповів' і повідомляємо власника."""
    choreographer = group["choreographer"]
    day = config.day_label(lesson_date)
    text = (
        "✅ Голос зараховано!\n"
        f"🗓 {day}, {group['style']} о {group['time']}\n"
        f"Ваш вибір: {choice}"
    )

    pollable = await db.get_pollable_groups_for_date(conn, lesson_date)
    own = [g for g in pollable if g["choreographer"] == choreographer]
    remaining = [g for g in own if await db.get_response(conn, g["id"], lesson_date) is None]
    if remaining:
        left = "\n".join(f"• {g['style']} о {g['time']}" for g in remaining)
        text += f"\n\n⏳ Ще чекаю вашу відповідь на {day}:\n{left}"
    elif len(own) > 1:
        text += f"\n\n🎉 Усі ваші заняття на {day} відмічено!"

    late = _table_already_published(lesson_date)
    if late:
        text += (
            f"\n\n⏰ Таблиця розкладу вже вийшла о {config.TABLE_TIME}, "
            "тож вашу відповідь я окремо передав адміністрації."
        )
        if not remaining:
            await db.delete_missed_poll(conn, choreographer, lesson_date)
        try:
            await bot.send_message(
                config.OWNER_CHAT_ID,
                f"🕒 Пізня відповідь (після {config.TABLE_TIME}): {choreographer} - "
                f"{day}, {group['style']} о {group['time']}: {choice}",
            )
        except Exception:
            logger.exception("Не вдалось повідомити власника про пізню відповідь")

    if can_change:
        text += "\n\nЯкщо помилились - просто натисніть іншу кнопку вище."
    await reply(text)


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    text = f"ID цього чату: {message.chat.id}"
    if message.message_thread_id is not None:
        text += f"\nID цієї гілки (теми): {message.message_thread_id}"
    await message.answer(text)


@router.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: Message, conn: aiosqlite.Connection) -> None:
    linked = await db.get_choreographer_by_telegram_id(conn, message.from_user.id)
    if linked is not None:
        await message.answer(
            f"Ви вже підтверджені як {linked}. Опитування щовечора приходитимуть сюди, "
            f"у цей чат."
        )
        return
    await message.answer(
        "Вітаю! Щоб опитування про завтрашні заняття приходили вам особисто в цей чат, "
        "оберіть, будь ласка, своє ім'я зі списку:",
        reply_markup=keyboards.choreographer_list_keyboard("linkme"),
    )


@router.callback_query(F.data.startswith("linkme:name:"))
async def handle_linkme(callback: CallbackQuery, conn: aiosqlite.Connection) -> None:
    choreographer = callback.data.split(":", 2)[2]

    already_linked_to = await db.get_choreographer_by_telegram_id(conn, callback.from_user.id)
    if already_linked_to is not None:
        await callback.answer(f"Цей чат вже підтверджений як {already_linked_to}.", show_alert=True)
        return

    if await db.is_choreographer_linked(conn, choreographer):
        await callback.answer(
            "Цей хореограф вже підтверджений іншим чатом. Зверніться до адміністратора.",
            show_alert=True,
        )
        return

    await db.register_choreographer_link(conn, choreographer, callback.from_user.id)
    await callback.answer(f"Готово, ви - {choreographer}!")
    await callback.message.edit_text(
        f"✅ Підтверджено: {choreographer}. Опитування про завтрашні заняття тепер "
        f"приходитимуть сюди, у цей чат."
    )


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

    if _lesson_passed(lesson_date):
        await callback.answer("Це опитування вже неактуальне - заняття минуло.", show_alert=True)
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
    await _confirm(bot, conn, group, lesson_date, info["button"], True, callback.message.answer)

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
    await _confirm(bot, conn, group, lesson_date,
                   f"{config.STATUSES['substitute']['button']} - {name}", False, callback.message.answer)

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
    await _confirm(bot, conn, group, lesson_date,
                   f"{config.STATUSES['substitute']['button']} - {name}", False, message.answer)

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
