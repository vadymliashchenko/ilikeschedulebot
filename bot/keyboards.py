from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import config


def status_keyboard(group_id: int, lesson_date_iso: str) -> InlineKeyboardMarkup:
    rows = []
    for key, info in config.STATUSES.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=info["button"],
                    callback_data=f"status:{group_id}:{lesson_date_iso}:{key}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Додати хореографа", callback_data="admin:add")],
        [InlineKeyboardButton(text="➖ Прибрати хореографа", callback_data="admin:remove")],
        [InlineKeyboardButton(text="✏️ Змінити стиль/час", callback_data="admin:edit")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def choreographer_list_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for name in config.CHOREOGRAPHERS:
        rows.append([InlineKeyboardButton(text=name, callback_data=f"{prefix}:name:{name}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def style_list_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for style in config.STYLES:
        rows.append([InlineKeyboardButton(text=style, callback_data=f"{prefix}:style:{style}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def time_slot_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t, callback_data=f"{prefix}:time:{t}")]
        for t in config.TIME_SLOTS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_pattern_keyboard(prefix: str) -> InlineKeyboardMarkup:
    labels = {
        "mon_thu": "Пн-Чт",
        "tue_fri": "Вт-Пт",
        "wed_only": "Тільки Ср",
    }
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{prefix}:pattern:{key}")]
        for key, label in labels.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def active_groups_keyboard(groups, prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for g in groups:
        text = f"{g['choreographer']} · {g['style']} · {g['time']}"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"{prefix}:group:{g['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
