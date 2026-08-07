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
_EMOJI_FONT_CANDIDATES = [
    os.path.join(FONTS_DIR, "NotoColorEmoji.ttf"),
    "/System/Library/Fonts/Apple Color Emoji.ttc",
]


def _find_font(candidates: list[str]) -> Optional[str]:
    for pattern in candidates:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]
        if os.path.exists(pattern):
            return pattern
    return None


def _find_supported_emoji_size(font_path: str) -> int:
    """CBDT/sbix-шрифти мають лише кілька "фіксованих" розмірів. Знаходимо перший робочий."""
    for size in list(range(16, 180)):
        try:
            ImageFont.truetype(font_path, size)
            return size
        except OSError:
            continue
    return 48


_TEXT_FONT_PATH = _find_font(_TEXT_FONT_CANDIDATES)
_EMOJI_FONT_PATH = _find_font(_EMOJI_FONT_CANDIDATES)
_EMOJI_SOURCE_SIZE = _find_supported_emoji_size(_EMOJI_FONT_PATH) if _EMOJI_FONT_PATH else 48
if _TEXT_FONT_PATH is None:
    logger.warning("No bold text font found for story images - falling back to PIL default")
if _EMOJI_FONT_PATH is None:
    logger.warning("No color emoji font found for story images - icons will be skipped")

_TEXT_COLOR = (245, 235, 220)

# Для закритих груп замінюємо ВСЮ пігулку (не тільки замок) на готове зображення,
# вирізане з дизайну оригінального макету - по одному на кожен з 5 рядків, окремо
# для кожної роздільної здатності шаблонів (720 - "ср", 1080 - решта).
# (межі пігулки в кожному рядку однакові, тому підміна виглядає безшовно).
_LOCKED_PILLS_DIR = os.path.join(ASSETS_DIR, "locked_pills")
_FILMING_PILLS_DIR = os.path.join(ASSETS_DIR, "filming_pills")
_ROW_COUNT = 5


def _load_pill_rows(base_dir: str) -> dict:
    rows_by_width = {}
    for width in (720, 1080):
        subdir = os.path.join(base_dir, str(width))
        if os.path.isdir(subdir):
            rows_by_width[width] = [
                Image.open(os.path.join(subdir, f"row{i}.png")).convert("RGB")
                for i in range(_ROW_COUNT)
            ]
    return rows_by_width


# Те саме для "зйомка відео" - одна й та сама іконка (без тексту), вирізана
# разом з пігулкою по кожному з 5 рядків, щоб розмір/пропорції були завжди однакові.
_LOCKED_PILL_ROWS = _load_pill_rows(_LOCKED_PILLS_DIR)
_FILMING_PILL_ROWS = _load_pill_rows(_FILMING_PILLS_DIR)

# (емодзі, текст, режим) для кожного статусу.
# "stacked" - емодзі окремим рядком зверху, текст під ним
# "large"   - просто текст, без емодзі, трохи більшим шрифтом (акцент)
# None      - просто текст, без емодзі, звичайний розмір
_STORY_CONTENT = {
    "first": (None, "НОВА\nХОРЕОГРАФІЯ", "hero"),
    "second_ok": (None, "МОЖНА\nПРИЄДНАТИСЯ", "right"),
    "open": (None, "МОЖНА\nПРИЄДНАТИСЯ", "right"),
    "closed": (None, "НЕ МОЖНА\nПРИЄДНАТИСЯ", "right"),
    "second_no": (None, "НЕ МОЖНА\nПРИЄДНАТИСЯ", "right"),
    "last_mk": (None, "ФОРМАТ МК", None),
    "filming": ("🎬", None, "icon_only"),
    "substitute": (None, "ЗАМІНА", "top_large"),
    "cancelled": (None, "ВІДМІНА", "large"),
}


def _pill_box(row_index: int, bounds: dict) -> tuple[int, int, int, int]:
    y0, y1 = bounds["y_bounds"][row_index]
    x0, x1 = bounds["x"]
    return (x0, y0, x1, y1)




def _render_emoji(char: str, target_size: int) -> Image.Image:
    font = ImageFont.truetype(_EMOJI_FONT_PATH, _EMOJI_SOURCE_SIZE)
    tmp = Image.new("RGBA", (_EMOJI_SOURCE_SIZE * 2, _EMOJI_SOURCE_SIZE * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.text((0, 0), char, font=font, embedded_color=True)
    bbox = tmp.getbbox()
    if bbox:
        tmp = tmp.crop(bbox)
    return tmp.resize((target_size, target_size), Image.LANCZOS)


def _paste_emoji(im: Image.Image, char: str, center_x: int, top_y: int, size: int) -> None:
    if _EMOJI_FONT_PATH is None:
        return  # немає кольорового emoji-шрифту на цьому сервері - пропускаємо іконку
    glyph = _render_emoji(char, size)
    im.paste(glyph, (int(center_x - size / 2), int(top_y)), glyph)


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


def _draw_pill(im: Image.Image, draw: ImageDraw.ImageDraw, box, emoji_char, text, mode, text_font,
               row_index: Optional[int] = None, scale: float = 1.0, pill_width: int = 720) -> None:
    def s(px: float) -> int:
        return round(px * scale)

    x0, y0, x1, y1 = box
    center_x = x0 + (x1 - x0) / 2
    box_h = y1 - y0

    if emoji_char == "🔒":
        # Замінюємо всю пігулку картинкою з готовим замком (вирізаною з дизайну
        # макету), а не малюємо іконку поверх - так межі пігулки завжди чіткі.
        pill = _LOCKED_PILL_ROWS[pill_width][row_index]
        im.paste(pill, (x0, y0))
        return

    box_w = (x1 - x0) - s(36)

    if emoji_char == "🎬" and mode == "icon_only":
        # Так само, як і замок - вставляємо всю пігулку одним готовим зображенням
        # з однією й тією ж іконкою, а не рендеримо емодзі щоразу наново.
        pill = _FILMING_PILL_ROWS[pill_width][row_index]
        im.paste(pill, (x0, y0))
        return

    if emoji_char and text and mode == "stacked":
        emoji_size = s(28)
        lines = text.split("\n")
        font, gap = _fit_font(draw, lines, text_font, emoji_size, box_h, box_w, min_size=s(12))
        heights = [_ink_size(draw, ln, font) for ln in lines]
        total_h = emoji_size + gap + gap * (len(lines) - 1) + sum(h for _, h, _ in heights)
        y = y0 + (box_h - total_h) / 2
        _paste_emoji(im, emoji_char, int(center_x), int(y), emoji_size)
        y += emoji_size + gap
        _draw_lines_aligned(draw, box, lines, heights, font, y, gap)
        return

    if mode == "hero" and text:
        big_line, small_line = text.split("\n")
        gap = max(s(3), text_font.size // 4)
        min_big, min_small = s(14), s(10)

        big_font = text_font.font_variant(size=text_font.size + s(14))
        while True:
            w0, h0, top0 = _ink_size(draw, big_line, big_font)
            if w0 <= box_w or big_font.size <= min_big:
                break
            big_font = big_font.font_variant(size=big_font.size - 2)

        small_font = text_font.font_variant(size=text_font.size - s(2))
        while True:
            w1, h1, top1 = _ink_size(draw, small_line, small_font)
            if w1 <= box_w or small_font.size <= min_small:
                break
            small_font = small_font.font_variant(size=small_font.size - 2)

        # окремо перевіряємо, що обидва рядки разом влазять і по висоті
        # (стискаємо тільки великий рядок - малий вже підібраний під свою ширину)
        while True:
            w0, h0, top0 = _ink_size(draw, big_line, big_font)
            w1, h1, top1 = _ink_size(draw, small_line, small_font)
            total_h = h0 + gap + h1
            if total_h <= box_h or big_font.size <= min_big:
                break
            big_font = big_font.font_variant(size=big_font.size - 2)

        w0, h0, top0 = _ink_size(draw, big_line, big_font)
        w1, h1, top1 = _ink_size(draw, small_line, small_font)
        total_h = h0 + gap + h1
        y = y0 + (box_h - total_h) / 2
        draw.text((center_x - w0 / 2, y - top0), big_line, font=big_font, fill=_TEXT_COLOR)
        y += h0 + gap
        draw.text((center_x - w1 / 2, y - top1), small_line, font=small_font, fill=_TEXT_COLOR)
        return

    if mode == "top_large" and text:
        base_font = text_font.font_variant(size=text_font.size + s(10))
        lines = text.split("\n")
        font, gap = _fit_font(draw, lines, base_font, 0, box_h, box_w, min_size=s(12))
        heights = [_ink_size(draw, ln, font) for ln in lines]
        top_margin = s(14)
        _draw_lines_aligned(draw, box, lines, heights, font, y0 + top_margin, gap, align="center")
        return

    if text:
        right_margin = s(18)
        center_offset = 0
        vertical_offset = 0
        if mode == "large":
            base_font = text_font.font_variant(size=text_font.size + s(6))
            align = "center"
            vertical_offset = s(3)
        elif mode == "right":
            base_font = text_font.font_variant(size=text_font.size + s(18))
            align = "right"
            right_margin = s(8)
            box_w = (x1 - x0) - 2 * right_margin
        else:
            base_font = text_font
            align = "center"
        lines = text.split("\n")
        font, gap = _fit_font(draw, lines, base_font, 0, box_h, box_w, min_size=s(12))
        heights = [_ink_size(draw, ln, font) for ln in lines]
        total_h = sum(h for _, h, _ in heights) + gap * (len(lines) - 1)
        y = y0 + (box_h - total_h) / 2 + vertical_offset
        _draw_lines_aligned(draw, box, lines, heights, font, y, gap, align=align, right_margin=right_margin,
                             center_offset=center_offset)


async def build_story_image(
    conn: aiosqlite.Connection, day_pattern: str, time: str, lesson_date: dt.date, out_path: str
) -> str:
    layout = story_layout.STORY_LAYOUTS[(day_pattern, time)]
    im = Image.open(os.path.join(ASSETS_DIR, layout["file"])).convert("RGB")
    draw = ImageDraw.Draw(im)

    bounds = story_layout.pill_bounds(im.width)
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
            _draw_pill(im, draw, box, "🔒", None, None, text_font, row_index=i, scale=scale, pill_width=im.width)
            continue

        resp = await db.get_response(conn, group["id"], lesson_date)
        if resp is None:
            continue  # немає відповіді - пігулку лишаємо порожньою

        status_key = resp["status_key"]
        emoji_char, text, mode = _STORY_CONTENT.get(status_key, (None, None, None))
        _draw_pill(im, draw, box, emoji_char, text, mode, text_font, row_index=i, scale=scale, pill_width=im.width)

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
        await build_story_image(conn, pattern, time, lesson_date, img_path)
        paths.append(img_path)
    return paths
