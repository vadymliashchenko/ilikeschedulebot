import datetime as dt
import logging

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, db, keyboards, states

logger = logging.getLogger(__name__)
router = Router(name="admin")
router.message.filter(F.chat.id == config.OWNER_CHAT_ID)
router.callback_query.filter(F.message.chat.id == config.OWNER_CHAT_ID)


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Керування складом хореографів:", reply_markup=keyboards.admin_menu_keyboard())


@router.callback_query(F.data == "admin:add")
async def admin_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(states.AddGroup.choosing_choreographer)
    await callback.message.edit_text(
        "Кого додаємо? Оберіть хореографа:",
        reply_markup=keyboards.choreographer_list_keyboard("add"),
    )


@router.callback_query(F.data.startswith("add:name:"), states.AddGroup.choosing_choreographer)
async def admin_add_name(callback: CallbackQuery, state: FSMContext) -> None:
    name = callback.data.split(":", 2)[2]
    await state.update_data(choreographer=name)
    await state.set_state(states.AddGroup.choosing_style)
    await callback.message.edit_text(
        f"{name}\nОберіть стиль:", reply_markup=keyboards.style_list_keyboard("add")
    )


@router.callback_query(F.data.startswith("add:style:"), states.AddGroup.choosing_style)
async def admin_add_style(callback: CallbackQuery, state: FSMContext) -> None:
    style = callback.data.split(":", 2)[2]
    await state.update_data(style=style)
    await state.set_state(states.AddGroup.choosing_time)
    await callback.message.edit_text("Оберіть час:", reply_markup=keyboards.time_slot_keyboard("add"))


@router.callback_query(F.data.startswith("add:time:"), states.AddGroup.choosing_time)
async def admin_add_time(callback: CallbackQuery, state: FSMContext) -> None:
    time_ = callback.data.split(":", 2)[2]
    await state.update_data(time=time_)
    await state.set_state(states.AddGroup.choosing_pattern)
    await callback.message.edit_text(
        "Які дні тижня діє ця група?", reply_markup=keyboards.day_pattern_keyboard("add")
    )


@router.callback_query(F.data.startswith("add:pattern:"), states.AddGroup.choosing_pattern)
async def admin_add_pattern(callback: CallbackQuery, state: FSMContext) -> None:
    pattern = callback.data.split(":", 2)[2]
    await state.update_data(pattern=pattern)
    await state.set_state(states.AddGroup.entering_date)
    await callback.message.edit_text(
        "З якої дати група активна? Напишіть у форматі ДД.ММ.РРРР, або словом 'сьогодні'."
    )


@router.message(states.AddGroup.entering_date)
async def admin_add_date(message: Message, state: FSMContext, conn: aiosqlite.Connection) -> None:
    text = message.text.strip().lower()
    if text == "сьогодні":
        active_since = dt.datetime.now(config.TZ).date()
    else:
        try:
            active_since = dt.datetime.strptime(text, "%d.%m.%Y").date()
        except ValueError:
            await message.answer(
                "Не розпізнав дату. Введіть у форматі ДД.ММ.РРРР, наприклад 01.08.2026."
            )
            return

    data = await state.get_data()
    await db.add_group(
        conn, data["choreographer"], data["style"], data["time"], data["pattern"],
        active_since.isoformat(),
    )
    await state.clear()
    await message.answer(
        f"Додано: {data['choreographer']} — {data['style']} о {data['time']}, "
        f"з {active_since.strftime('%d.%m.%Y')}."
    )


@router.callback_query(F.data == "admin:remove")
async def admin_remove_start(callback: CallbackQuery, state: FSMContext, conn: aiosqlite.Connection) -> None:
    groups = await db.get_active_groups(conn)
    if not groups:
        await callback.message.edit_text("Активних груп немає.")
        return
    await state.set_state(states.RemoveGroup.choosing_group)
    await callback.message.edit_text(
        "Кого прибираємо?", reply_markup=keyboards.active_groups_keyboard(groups, "remove")
    )


@router.callback_query(F.data.startswith("remove:group:"), states.RemoveGroup.choosing_group)
async def admin_remove_confirm(callback: CallbackQuery, state: FSMContext, conn: aiosqlite.Connection) -> None:
    group_id = int(callback.data.split(":", 2)[2])
    group = await db.get_group(conn, group_id)
    await db.deactivate_group(conn, group_id)
    await state.clear()
    if group:
        await callback.message.edit_text(
            f"Прибрано: {group['choreographer']} — {group['style']} о {group['time']}."
        )
    else:
        await callback.message.edit_text("Прибрано.")


@router.callback_query(F.data == "admin:edit")
async def admin_edit_start(callback: CallbackQuery, state: FSMContext, conn: aiosqlite.Connection) -> None:
    groups = await db.get_active_groups(conn)
    if not groups:
        await callback.message.edit_text("Активних груп немає.")
        return
    await state.set_state(states.EditGroup.choosing_group)
    await callback.message.edit_text(
        "Яку групу редагувати?", reply_markup=keyboards.active_groups_keyboard(groups, "edit")
    )


@router.callback_query(F.data.startswith("edit:group:"), states.EditGroup.choosing_group)
async def admin_edit_choose_group(callback: CallbackQuery, state: FSMContext) -> None:
    group_id = int(callback.data.split(":", 2)[2])
    await state.update_data(group_id=group_id)
    await state.set_state(states.EditGroup.choosing_field)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Стиль", callback_data="edit:field:style")],
            [InlineKeyboardButton(text="Час", callback_data="edit:field:time")],
        ]
    )
    await callback.message.edit_text("Що змінити?", reply_markup=kb)


@router.callback_query(F.data == "edit:field:style", states.EditGroup.choosing_field)
async def admin_edit_field_style(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(states.EditGroup.choosing_style)
    await callback.message.edit_text(
        "Оберіть новий стиль:", reply_markup=keyboards.style_list_keyboard("edit")
    )


@router.callback_query(F.data.startswith("edit:style:"), states.EditGroup.choosing_style)
async def admin_edit_apply_style(callback: CallbackQuery, state: FSMContext, conn: aiosqlite.Connection) -> None:
    style = callback.data.split(":", 2)[2]
    data = await state.get_data()
    await db.edit_group(conn, data["group_id"], style=style, time=None)
    await state.clear()
    await callback.message.edit_text(f"Стиль оновлено на: {style}")


@router.callback_query(F.data == "edit:field:time", states.EditGroup.choosing_field)
async def admin_edit_field_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(states.EditGroup.choosing_time)
    await callback.message.edit_text(
        "Оберіть новий час:", reply_markup=keyboards.time_slot_keyboard("edit")
    )


@router.callback_query(F.data.startswith("edit:time:"), states.EditGroup.choosing_time)
async def admin_edit_apply_time(callback: CallbackQuery, state: FSMContext, conn: aiosqlite.Connection) -> None:
    time_ = callback.data.split(":", 2)[2]
    data = await state.get_data()
    await db.edit_group(conn, data["group_id"], style=None, time=time_)
    await state.clear()
    await callback.message.edit_text(f"Час оновлено на: {time_}")
