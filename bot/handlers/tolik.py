"""Обробники кнопок і команд Толіка. Усі callback починаються з «tk:» — не перетинаються з іншими."""
import datetime as dt
import logging
import random

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, db, tolik, tolik_db
from bot import tolik_texts as T

logger = logging.getLogger(__name__)
router = Router(name="tolik")


class TolikInput(StatesGroup):
    thermo = State()
    admin_name = State()


def _is_owner(user_id: int) -> bool:
    return user_id == config.OWNER_CHAT_ID


async def _edit_safe(callback: CallbackQuery, **kw) -> None:
    try:
        await callback.message.edit_text(**kw)
    except TelegramAPIError:
        pass  # «message is not modified» тощо — не критично


# ================= хореографи =================
@router.callback_query(F.data.startswith("tk:c:"))
async def on_choreo_answer(callback: CallbackQuery, conn: aiosqlite.Connection) -> None:
    try:
        _, _, gid_s, d, k, bset_s = callback.data.split(":")
        gid, bset = int(gid_s), int(bset_s)
        group = await db.get_group(conn, gid)
        if group is None:
            await callback.answer("Заняття не знайдено 🙈", show_alert=True)
            return
        uid = callback.from_user.id
        is_test = _is_owner(uid) and (tolik.OWNER_ONLY or (callback.message.text or "").startswith("🧪"))
        if not is_test:
            linked = await db.get_choreographer_by_telegram_id(conn, uid)
            if linked != group["choreographer"]:
                await callback.answer("Це питання для іншого хореографа 🙂", show_alert=True)
                return
        survey_date = tolik.s2d(d).isoformat()
        row = dict(survey_date=survey_date, role="choreographer", person=group["choreographer"],
                   telegram_user_id=uid, group_id=gid, hall=group["hall"], room="hall",
                   class_time=group["time"], style=group["style"], answer=T.ANSWER_KEYS[k], is_test=is_test)
        await tolik_db.save_comfort(conn, **row)
        await tolik.push_to_sheet(row)

        # перебудувати клавіатуру з ✅ для всіх занять цього повідомлення
        kb_rows = callback.message.reply_markup.inline_keyboard if callback.message.reply_markup else []
        gids = []
        for r in kb_rows:
            parts = r[0].callback_data.split(":")
            if parts[1] == "c":
                gids.append(int(parts[2]))
        saved = await tolik_db.get_comfort_for(conn, survey_date, "choreographer", group["choreographer"], is_test)
        inv = {v: kk for kk, v in T.ANSWER_KEYS.items()}
        chosen = {r["group_id"]: inv.get(r["answer"]) for r in saved}
        classes = [await db.get_group(conn, g) for g in gids]
        classes = [c for c in classes if c is not None]
        await callback.answer(T.CHOREO_REPLIES[k])
        all_done = all(chosen.get(c["id"]) for c in classes)
        if all_done:
            text = callback.message.text + "\n\n" + T.CHOREO_REPLIES[k]
            await _edit_safe(callback, text=text, reply_markup=tolik.choreo_keyboard(classes, d, bset, chosen))
        else:
            await callback.message.edit_reply_markup(reply_markup=tolik.choreo_keyboard(classes, d, bset, chosen))
    except Exception:  # noqa: BLE001
        logger.exception("Tolik choreo callback failed")
        await callback.answer("Ой, щось пішло не так 🙈 Спробуйте ще раз", show_alert=True)


# ================= феї-адміністратори =================
async def _admin_person(conn, uid: int):
    a = await tolik_db.get_admin(conn, uid)
    if a is not None and a["approved"]:
        return a["name"], False
    if _is_owner(uid):
        return "ТЕСТ", True
    return None, False


@router.callback_query(F.data.startswith("tk:f:"))
async def on_floor(callback: CallbackQuery, conn: aiosqlite.Connection, bot: Bot) -> None:
    _, _, d, floor_s = callback.data.split(":")
    floor = int(floor_s)
    uid = callback.from_user.id
    person, is_test = await _admin_person(conn, uid)
    if person is None:
        await callback.answer("Спершу зареєструйся командою /feya 🧚‍♀️", show_alert=True)
        return
    survey_date = tolik.s2d(d).isoformat()
    if floor == 0:
        row = dict(survey_date=survey_date, role="admin", person=person, telegram_user_id=uid,
                   floor=0, room="off", answer=None, is_test=is_test)
        await tolik_db.save_comfort(conn, **row)
        await callback.answer(T.ADMIN_NOT_WORKING_REPLY)
        await _edit_safe(callback, text=T.ADMIN_NOT_WORKING_REPLY)
        return
    key = f"floors:{uid}:{d}"
    chosen = set(filter(None, (await tolik_db.get_state(conn, key) or "").split(",")))
    if str(floor) in chosen:
        await callback.answer("Цей поверх уже обрано 💫")
        return
    chosen.add(str(floor))
    await tolik_db.set_state(conn, key, ",".join(sorted(chosen)))
    await callback.answer(f"{floor} поверх ✨")
    try:
        await callback.message.edit_reply_markup(reply_markup=tolik.floors_keyboard(d, {int(x) for x in chosen}))
    except TelegramAPIError:
        pass
    await tolik.send_room_questions(bot, callback.message.chat.id, d, floor)


@router.callback_query(F.data.startswith("tk:a:"))
async def on_room_answer(callback: CallbackQuery, conn: aiosqlite.Connection) -> None:
    _, _, d, floor_s, room, k, bset_s = callback.data.split(":")
    uid = callback.from_user.id
    person, is_test = await _admin_person(conn, uid)
    if person is None:
        await callback.answer("Спершу зареєструйся командою /feya 🧚‍♀️", show_alert=True)
        return
    floor = int(floor_s)
    row = dict(survey_date=tolik.s2d(d).isoformat(), role="admin", person=person, telegram_user_id=uid,
               floor=floor, room="reception" if room == "r" else "dressing",
               answer=T.ANSWER_KEYS[k], is_test=is_test)
    await tolik_db.save_comfort(conn, **row)
    await tolik.push_to_sheet(row)
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(
            reply_markup=tolik.room_keyboard(d, floor, room, int(bset_s), chosen=k))
    except TelegramAPIError:
        pass
    await callback.message.answer(T.ADMIN_REPLIES[k], parse_mode=None)


@router.callback_query(F.data.startswith("tk:t:"))
async def on_thermo(callback: CallbackQuery, state: FSMContext, conn: aiosqlite.Connection) -> None:
    _, _, d, floor_s, room = callback.data.split(":")
    person, _ = await _admin_person(conn, callback.from_user.id)
    if person is None:
        await callback.answer("Спершу зареєструйся командою /feya 🧚‍♀️", show_alert=True)
        return
    await state.set_state(TolikInput.thermo)
    await state.update_data(d=d, floor=int(floor_s), room=room)
    await callback.answer()
    await callback.message.answer(T.THERMO_ASK, parse_mode=None)


@router.message(TolikInput.thermo)
async def on_thermo_value(message: Message, state: FSMContext, conn: aiosqlite.Connection) -> None:
    try:
        value = float((message.text or "").replace(",", ".").replace("°", "").replace("C", "").strip())
        if not 0 < value < 45:
            raise ValueError
    except ValueError:
        await message.answer(T.THERMO_BAD, parse_mode=None)
        return
    data = await state.get_data()
    await state.clear()
    person, is_test = await _admin_person(conn, message.from_user.id)
    row = dict(survey_date=tolik.s2d(data["d"]).isoformat(), role="admin", person=person or "?",
               telegram_user_id=message.from_user.id, floor=data["floor"],
               room="reception" if data["room"] == "r" else "dressing", answer=None,
               thermometer=value, is_test=is_test)
    await tolik_db.save_comfort(conn, **row)
    await tolik.push_to_sheet(row)
    await message.answer(T.THERMO_OK.format(value=str(value).replace(".", ",")), parse_mode=None)


# ---- реєстрація феї ----
# Посилання для фей: https://t.me/<бот>?start=feya — «Старт» одразу веде до вибору імені
# (а не до списку хореографів). Роутер Толіка підключено першим, тому цей обробник спрацьовує раніше.
@router.message(CommandStart(deep_link=True, magic=F.args == "feya"), F.chat.type == "private")
@router.message(Command("feya"), F.chat.type == "private")
async def cmd_feya(message: Message, conn: aiosqlite.Connection) -> None:
    a = await tolik_db.get_admin(conn, message.from_user.id)
    if a is not None and a["approved"]:
        await message.answer(f"Ти вже в списку наших фей, {a['name']} 💖", parse_mode=None)
        return
    rows = [[InlineKeyboardButton(text=n, callback_data=f"tk:rn:{i}")] for i, n in enumerate(T.ADMIN_NAMES)]
    rows.append([InlineKeyboardButton(text="✍️ Інше ім'я", callback_data="tk:rn:x")])
    await message.answer("Привіт! 🧚‍♀️ Це Толік. Обери, будь ласка, своє ім'я:",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode=None)


async def _request_admin(bot: Bot, conn, user, name: str) -> None:
    full = " ".join(x for x in (user.first_name, user.last_name) if x) + (f" (@{user.username})" if user.username else "")
    await tolik_db.upsert_admin_request(conn, user.id, name, full)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Так, це наша фея", callback_data=f"tk:ap:{user.id}:1"),
        InlineKeyboardButton(text="❌ Ні", callback_data=f"tk:ap:{user.id}:0")]])
    await bot.send_message(config.OWNER_CHAT_ID,
                           f"🧚‍♀️ Запит у феї-адміністратори: «{name}» — Telegram: {full}",
                           reply_markup=kb, parse_mode=None)


@router.callback_query(F.data.startswith("tk:rn:"))
async def on_admin_name(callback: CallbackQuery, conn: aiosqlite.Connection, bot: Bot, state: FSMContext) -> None:
    v = callback.data.split(":")[2]
    await callback.answer()
    if v == "x":
        await state.set_state(TolikInput.admin_name)
        await _edit_safe(callback, text="Напиши, будь ласка, своє ім'я 🌸")
        return
    name = T.ADMIN_NAMES[int(v)]
    await _request_admin(bot, conn, callback.from_user, name)
    await _edit_safe(callback, text=f"Дякую, {name}! 💖 Чекаємо підтвердження від Вадима ✨")


@router.message(TolikInput.admin_name)
async def on_admin_name_text(message: Message, conn: aiosqlite.Connection, bot: Bot, state: FSMContext) -> None:
    name = (message.text or "").strip()[:30]
    await state.clear()
    if not name:
        await message.answer("Не зрозумів ім'я 🙈 Надішли /feya ще раз", parse_mode=None)
        return
    await _request_admin(bot, conn, message.from_user, name)
    await message.answer(f"Дякую, {name}! 💖 Чекаємо підтвердження від Вадима ✨", parse_mode=None)


@router.callback_query(F.data.startswith("tk:ap:"))
async def on_admin_approve(callback: CallbackQuery, conn: aiosqlite.Connection, bot: Bot) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Лише для власника", show_alert=True)
        return
    _, _, uid_s, ok = callback.data.split(":")
    uid = int(uid_s)
    a = await tolik_db.get_admin(conn, uid)
    await tolik_db.set_admin_approved(conn, uid, ok == "1")
    await callback.answer("Готово")
    name = a["name"] if a else "?"
    await _edit_safe(callback, text=("✅ Додано фею: " if ok == "1" else "❌ Відхилено: ") + name)
    if ok == "1":
        try:
            await bot.send_message(uid, f"Ура, {name}, ти в списку наших фей! ✅💖\n\n" + T.ADMIN_INTRO, parse_mode=None)
        except TelegramAPIError:
            pass


# ================= команди власника =================
@router.message(Command("toliktest"))
async def cmd_toliktest(message: Message, conn: aiosqlite.Connection, bot: Bot) -> None:
    if not _is_owner(message.from_user.id):
        return
    n1 = await tolik.job_choreo(bot, conn, force_test=True)
    n2 = await tolik.job_admin(bot, conn, force_test=True)
    await message.answer(
        f"🧪 Тест Толіка: прикладів для хореографів — {n1}, для фей — {n2}.\n"
        f"Режим: {'увімкнено' if tolik.ENABLED else 'вимкнено'}, "
        f"{'лише власник' if tolik.OWNER_ONLY else 'усім'}.", parse_mode=None)


@router.message(Command("tolikstats"))
async def cmd_tolikstats(message: Message, conn: aiosqlite.Connection) -> None:
    if not _is_owner(message.from_user.id):
        return
    since = tolik.today() - dt.timedelta(days=7)
    rows = await tolik_db.stats_since(conn, since)
    if not rows:
        await message.answer("За тиждень відповідей ще немає 🙂", parse_mode=None)
        return
    agg: dict = {}
    for r in rows:
        if r["role"] == "choreographer":
            place = tolik.HALL_NOM.get(r["hall"], r["hall"])
        else:
            place = f"{r['floor']} пов. — " + ("рецепція" if r["room"] == "reception" else T.DRESSING_ROOM_NAMES.get(r["floor"], "роздягалка"))
        agg.setdefault(place, {"cold": 0, "normal": 0, "hot": 0})[r["answer"]] += r["n"]
    lines = [f"📊 Толік за 7 днів (з {since.strftime('%d.%m')}):"]
    for place, c in agg.items():
        lines.append(f"{place}: 🥶 {c['cold']} · 🙂 {c['normal']} · 🥵 {c['hot']}")
    await message.answer("\n".join(lines), parse_mode=None)


@router.message(Command("tolikpopravky"))
async def cmd_tolikpopravky(message: Message, conn: aiosqlite.Connection) -> None:
    if not _is_owner(message.from_user.id):
        return
    from bot import tolik_adjust
    await message.answer(await tolik_adjust.all_adjustments_text(conn), parse_mode=None)


@router.message(Command("tolikapi"))
async def cmd_tolikapi(message: Message, conn: aiosqlite.Connection) -> None:
    if not _is_owner(message.from_user.id):
        return
    from bot import tolik_adjust
    k = await tolik_adjust.api_key(conn)
    await message.answer(
        "🔑 Ключ для Home Assistant (нікому не пересилайте):\n" + k +
        "\n\nАдреса: https://<домен з Railway>/tolik/plan?key=" + k, parse_mode=None)


@router.message(Command("tolikdnes"))
async def cmd_tolikdnes(message: Message, conn: aiosqlite.Connection) -> None:
    """Відповіді хореографів і фей за день: /tolikdnes або /tolikdnes 2026-10-27."""
    if not _is_owner(message.from_user.id):
        return
    arg = (message.text or "").split(maxsplit=1)
    try:
        d = dt.date.fromisoformat(arg[1].strip()) if len(arg) > 1 else tolik.today()
    except ValueError:
        await message.answer("Формат: /tolikdnes 2026-10-27", parse_mode=None)
        return
    cur = await conn.execute(
        "SELECT * FROM comfort_responses WHERE survey_date = ? AND is_test = 0 ORDER BY role, person, class_time",
        (d.isoformat(),))
    rows = await cur.fetchall()
    if not rows:
        await message.answer(f"За {d.strftime('%d.%m')} відповідей немає 🤷", parse_mode=None)
        return
    em = {"cold": "🥶", "normal": "🙂", "hot": "🥵"}
    lines = [f"📋 Відповіді за {d.strftime('%d.%m.%Y')}:"]
    for r in rows:
        if r["role"] == "choreographer":
            place = f"{tolik.HALL_NOM.get(r['hall'], r['hall'])} {r['class_time'] or ''} — {r['style'] or ''}"
        else:
            place = f"{r['floor']} пов. — " + ("рецепція" if r["room"] == "reception" else T.DRESSING_ROOM_NAMES.get(r["floor"], "роздягалка"))
        t = f" (термометр {r['thermometer']} °C)" if r["thermometer"] is not None else ""
        lines.append(f"{em.get(r['answer'], '•')} {r['person']}: {place}{t}")
    await message.answer("\n".join(lines), parse_mode=None)


@router.message(Command("tolikfei"))
async def cmd_tolikfei(message: Message, conn: aiosqlite.Connection) -> None:
    if not _is_owner(message.from_user.id):
        return
    admins = await tolik_db.get_all_admins(conn)
    if not admins:
        await message.answer("Фей ще немає. Попросіть адміністраторів написати боту /feya 🧚‍♀️", parse_mode=None)
        return
    lines = ["🧚‍♀️ Феї-адміністратори:"] + [
        f"{'✅' if a['approved'] else '⏳'} {a['name']} — {a['full_name'] or ''}" for a in admins]
    await message.answer("\n".join(lines), parse_mode=None)


@router.message(Command("tolikintro"))
async def cmd_tolikintro(message: Message) -> None:
    if not _is_owner(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📣 Розіслати", callback_data="tk:intro:go"),
        InlineKeyboardButton(text="Скасувати", callback_data="tk:intro:no")]])
    await message.answer("Так виглядатиме вступ для хореографів:\n\n" + T.CHOREO_INTRO, parse_mode=None)
    await message.answer("А так — для фей:\n\n" + T.ADMIN_INTRO, parse_mode=None)
    who = "лише вам (режим тесту)" if tolik.OWNER_ONLY else "усім підключеним хореографам і феям"
    await message.answer(f"Розіслати {who}?", reply_markup=kb, parse_mode=None)


@router.callback_query(F.data.startswith("tk:intro:"))
async def on_intro(callback: CallbackQuery, conn: aiosqlite.Connection, bot: Bot) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    if callback.data.endswith(":no"):
        await callback.answer("Скасовано")
        await _edit_safe(callback, text="Розсилку скасовано.")
        return
    await callback.answer("Розсилаю…")
    sent = 0
    if tolik.OWNER_ONLY:
        await bot.send_message(config.OWNER_CHAT_ID, "🧪 " + T.CHOREO_INTRO, parse_mode=None)
        await bot.send_message(config.OWNER_CHAT_ID, "🧪 " + T.ADMIN_INTRO, parse_mode=None)
        sent = 2
    else:
        for link in await db.get_all_links(conn):
            try:
                await bot.send_message(link["telegram_user_id"], T.CHOREO_INTRO, parse_mode=None)
                sent += 1
            except TelegramAPIError:
                pass
        for a in await tolik_db.get_approved_admins(conn):
            try:
                await bot.send_message(a["telegram_user_id"], T.ADMIN_INTRO, parse_mode=None)
                sent += 1
            except TelegramAPIError:
                pass
    await _edit_safe(callback, text=f"📣 Вступ розіслано: {sent} повідомлень.")
