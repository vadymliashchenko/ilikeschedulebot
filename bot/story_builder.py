import datetime as dt
import glob
import logging
import os

from typing import Optional

import aiosqlite
from PIL import Image, ImageDraw, ImageFont

from bot import config, db, story_layout

logger = logging.getLogger(__name__)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "story_templates")
FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")

# Шрифти лежать прямо в репозиторії (assets/fonts) - тому однаково працюють
# і локально на Mac, і на сервері Railway, незалежно від системних шрифтів.
_TEXT_FONT_CANDIDATES = [
    os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf"),
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
def _find_font(candidates: list[str]) -> Optional[str]:
    for pattern in candidates:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]
        if os.path.exists(pattern):
            return pattern
    return None


_TEXT_FONT_PATH = _find_font(_TEXT_FONT_CANDIDATES)
if _TEXT_FONT_PATH is None:
    logger.warning("No bold text font found for story images - falling back to PIL default")

_TEXT_COLOR = (245, 235, 220)

# Для закритих груп замінюємо ВСЮ пігулку (не тільки замок) на готове зображення,
# вирізане з дизайну оригінального макету - по одному на кожен з 5 рядків, окремо
# для кожної роздільної здатності шаблонів (720 - "ср", 1080 - решта).
# (межі пігулки в кожному рядку однакові, тому підміна виглядає безшовно).
_LOCKED_PILLS_DIR = os.path.join(ASSETS_DIR, "locked_pills")
_ROW_COUNT = 5


def _load_pill_rows(base_dir: str) -> dict:
    rows_by_width = {}
    for width in (720,):
        subdir = os.path.join(base_dir, str(width))
        if os.path.isdir(subdir):
            rows_by_width[width] = [
                Image.open(os.path.join(subdir, f"row{i}.png")).convert("RGB")
                for i in range(_ROW_COUNT)
            ]
    return rows_by_width


_LOCKED_PILL_ROWS = _load_pill_rows(_LOCKED_PILLS_DIR)

# Текст для кожного статусу - усі малюються ОДНИМ стилем (див. _draw_status_text):
# однаковий розмір шрифту, притиснуто до правого краю, по вертикальному центру
# рядка. Звірено з реальними опублікованими сторіс (еталонні скріншоти від
# користувача, 2026-09-09) - там кожен статус, включно з "новою хореографією",
# виглядає ідентично за розміром/вирівнюванням, без жодного акценту чи іншого
# розміру для якогось одного статусу.
_STORY_CONTENT = {
    "first": "НОВА\nХОРЕОГРАФІЯ",
    "open": "МОЖНА\nПРИЄДНАТИСЯ",
    "closed": "НЕ МОЖНА\nПРИЄДНАТИСЯ",
    "last_mk": "ФОРМАТ МК",
    "substitute": "ЗАМІНА",
    "cancelled": "ВІДМІНА",
}


def _pill_box(row_index: int, bounds: dict) -> tuple[int, int, int, int]:
    y0, y1 = bounds["y_bounds"][row_index]
    x0, x1 = bounds["x"]
    return (x0, y0, x1, y1)




def _ink_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int, int]:
    """Повертає (ширина, висота чорнила, зсув-верх) без зайвих полів шрифту."""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top, top


def _fit_font(draw: ImageDraw.ImageDraw, lines: list[str], base_font, extra_row_h: int, box_h: int,
              box_w: Optional[int] = None, min_size: int = 12):
    """Підбирає розмір шрифту так, щоб усі рядки (+ рядок під іконку) влізли і по висоті, і по ширині."""
    for size in range(base_font.size, min_size - 1, -2):
        font = base_font.font_variant(size=size)
        gap = max(2, size // 6)
        sizes = [_ink_size(draw, ln, font) for ln in lines]
        heights = [h for _, h, _ in sizes]
        widths = [w for w, _, _ in sizes]
        total_h = extra_row_h + gap * len(lines) + sum(heights)
        fits_h = total_h <= box_h
        fits_w = box_w is None or max(widths) <= box_w
        if (fits_h and fits_w) or size <= min_size:
            return font, gap
    return base_font.font_variant(size=min_size), 4


def _draw_lines_aligned(draw: ImageDraw.ImageDraw, box, lines: list[str], heights, font, start_y: float, gap: float,
                         align: str = "center", right_margin: int = 18, center_offset: float = 0) -> None:
    """Кожен рядок або по центру пігулки, або притиснутий до правого краю (з рівним відступом).

    Пігулка має заокруглений край лише зліва (справа йде за межі кадру), тому
    геометричний центр box трохи "лівіший" за візуальний - center_offset це компенсує.
    """
    x0, y0, x1, y1 = box
    center_x = x0 + (x1 - x0) / 2 + center_offset
    y = start_y
    for i, line in enumerate(lines):
        w, h, top = heights[i]
        if align == "right":
            x = x1 - right_margin - w
        else:
            x = center_x - w / 2
        draw.text((x, y - top), line, font=font, fill=_TEXT_COLOR)
        y += h + gap


def _draw_locked_pill(im: Image.Image, box, row_index: int, pill_width: int) -> None:
    if pill_width not in _LOCKED_PILL_ROWS:
        # Новіші шаблони (без рамки під пігулку) вже мають замок намальованим
        # прямо на фото закритого рядка - додатковий не малюємо, щоб не дублювати.
        return
    # Замінюємо всю пігулку картинкою з готовим замком (вирізаною з дизайну
    # макету), а не малюємо іконку поверх - так межі пігулки завжди чіткі.
    # Це потрібно лише для старих дизайнів з видимою рамкою пігулки.
    x0, y0, _, _ = box
    im.paste(_LOCKED_PILL_ROWS[pill_width][row_index], (x0, y0))


def _draw_status_text(im: Image.Image, draw: ImageDraw.ImageDraw, box, text: str, text_font,
                       scale: float = 1.0) -> None:
    """Єдиний стиль для всіх статусів: однаковий розмір, притиснуто до правого
    краю, по вертикальному центру рядка (звірено з реальними опублікованими
    сторіс - там немає жодних відмінностей у розмірі/вирівнюванні між статусами)."""
    def s(px: float) -> int:
        return round(px * scale)

    x0, y0, x1, y1 = box
    box_h = y1 - y0
    right_margin = s(8)
    box_w = (x1 - x0) - 2 * right_margin

    base_font = text_font.font_variant(size=text_font.size + s(6))
    lines = text.split("\n")
    font, gap = _fit_font(draw, lines, base_font, 0, box_h, box_w, min_size=s(12))
    heights = [_ink_size(draw, ln, font) for ln in lines]
    total_h = sum(h for _, h, _ in heights) + gap * (len(lines) - 1)
    y = y0 + (box_h - total_h) / 2
    _draw_lines_aligned(draw, box, lines, heights, font, y, gap, align="right", right_margin=right_margin)


async def build_story_image(
    conn: aiosqlite.Connection, day_pattern: str, time: str, lesson_date: dt.date, out_path: str
) -> str:
    layout = story_layout.STORY_LAYOUTS[(day_pattern, time)]
    im = Image.open(os.path.join(ASSETS_DIR, layout["file"])).convert("RGB")
    draw = ImageDraw.Draw(im)

    bounds = story_layout.bounds_for(day_pattern, time, im.width)
    scale = bounds["scale"]

    if _TEXT_FONT_PATH:
        text_font = ImageFont.truetype(_TEXT_FONT_PATH, round(22 * scale))
    else:
        text_font = ImageFont.load_default(size=round(22 * scale))

    for i, name in enumerate(layout["rows"]):
        box = _pill_box(i, bounds)
        group = await db.get_group_by_name_time_pattern(conn, name, day_pattern, time)
        if group is None:
            # Рядок міг бути показаний у цьому кадрі суто декоративно (наприклад,
            # закрита група в іншу годину) - шукаємо її як закриту, незалежно від часу.
            group = await db.get_locked_group_by_name_pattern(conn, name, day_pattern)
            if group is None:
                continue

        if group["locked"]:
            _draw_locked_pill(im, box, i, im.width)
            continue

        if group["start_date"] and group["start_date"] > lesson_date.isoformat():
            # Група ще не стартувала - на реальних сторіс це показано як
            # "СТАРТ ДД.ММ" (той самий стиль, що й решта статусів), а не
            # порожнім рядком.
            start = dt.date.fromisoformat(group["start_date"])
            _draw_status_text(im, draw, box, f"СТАРТ\n{start.day:02d}.{start.month:02d}", text_font, scale=scale)
            continue

        resp = await db.get_response(conn, group["id"], lesson_date)
        if resp is None:
            continue  # немає відповіді - пігулку лишаємо порожньою

        text = _STORY_CONTENT.get(resp["status_key"])
        if text is not None:
            _draw_status_text(im, draw, box, text, text_font, scale=scale)

    im.save(out_path)
    return out_path


def _patterns_for_date(lesson_date: dt.date) -> list[str]:
    weekday = lesson_date.weekday()
    return [p for p, days in config.DAY_PATTERN_WEEKDAYS.items() if weekday in days]


async def build_day_images(
    conn: aiosqlite.Connection, lesson_date: dt.date, out_dir: str
) -> list:
    """Генерує окрему PNG-картинку на кожну годину, яка є в макетах для цього дня."""
    os.makedirs(out_dir, exist_ok=True)
    patterns = _patterns_for_date(lesson_date)

    slots = [
        (pattern, time) for pattern in patterns
        for (p, time) in story_layout.STORY_LAYOUTS
        if p == pattern
    ]
    slots.sort(key=lambda pt: pt[1])

    paths = []
    for pattern, time in slots:
        safe_time = time.replace(":", "_")
        img_path = os.path.join(out_dir, f"{lesson_date.isoformat()}_{pattern}_{safe_time}.png")
        try:
            await build_story_image(conn, pattern, time, lesson_date, img_path)
        except ValueError:
            # Непідтримувана роздільна здатність шаблону - не рвемо решту дня
            # через одне зображення, просто пропускаємо і йдемо далі.
            logger.exception("Не вдалося згенерувати макет %s %s на %s", pattern, time, lesson_date)
            continue
        paths.append(img_path)
    return paths
