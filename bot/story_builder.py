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

# (емодзі, текст, режим) для кожного статусу.
# "combined" - емодзі і перший рядок тексту в одному рядку, разом відцентровано
# "stacked"  - емодзі окремим рядком зверху, текст (одним рядком) під ним
# None       - просто текст, без емодзі
_STORY_CONTENT = {
    "first": ("🆕", "НОВА\nХОРЕОГРАФІЯ", "combined"),
    "second_ok": (None, "МОЖНА\nПРИЄДНАТИСЯ", None),
    "second_no": (None, "НЕ МОЖНА\nПРИЄДНАТИСЯ", None),
    "last_mk": (None, "ФОРМАТ МК", None),
    "filming": ("🎦", "ЗЙОМКА ВІДЕО", "stacked"),
    "substitute": ("🔀", "ЗАМІНА", "combined"),
    "cancelled": ("❌", "ВІДМІНА", "combined"),
}


def _pill_box(row_index: int) -> tuple[int, int, int, int]:
    y0, y1 = story_layout.STORY_PILL_Y_BOUNDS[row_index]
    x0, x1 = story_layout.STORY_PILL_X
    return (x0, y0, x1, y1)


def _draw_centered_text(draw: ImageDraw.ImageDraw, box, text: str, font) -> None:
    x0, y0, x1, y1 = box
    lines = text.split("\n")
    gap = max(2, font.size // 6)
    sizes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    heights = [b[3] - b[1] for b in sizes]
    total_h = sum(heights) + gap * (len(lines) - 1)
    y = y0 + ((y1 - y0) - total_h) / 2
    for i, line in enumerate(lines):
        left, top, right, bottom = sizes[i]
        w = right - left
        x = x0 + ((x1 - x0) - w) / 2
        draw.text((x, y - top), line, font=font, fill=_TEXT_COLOR)
        y += heights[i] + gap



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


def _fit_font(draw: ImageDraw.ImageDraw, lines: list[str], base_font, extra_row_h: int, box_h: int):
    """Підбирає розмір шрифту так, щоб усі рядки (+ додатковий рядок під іконку) влізли у box_h."""
    for size in (base_font.size, base_font.size - 2, base_font.size - 4, base_font.size - 6):
        font = base_font.font_variant(size=size)
        gap = max(2, size // 6)
        heights = [_ink_size(draw, ln, font)[1] for ln in lines]
        total = extra_row_h + gap * len(lines) + sum(heights)
        if total <= box_h or size == base_font.size - 6:
            return font, gap
    return base_font, 4


def _draw_pill(im: Image.Image, draw: ImageDraw.ImageDraw, box, emoji_char, text, mode, text_font) -> None:
    x0, y0, x1, y1 = box
    center_x = x0 + (x1 - x0) / 2
    box_h = y1 - y0

    if emoji_char and text and mode == "combined":
        lines = text.split("\n")
        first_line, rest_lines = lines[0], lines[1:]
        emoji_size = 26 if len(lines) < 3 else 20

        font, gap = _fit_font(draw, lines, text_font, 0, box_h)
        heights = [_ink_size(draw, ln, font) for ln in lines]
        row_heights = [max(h, emoji_size) if i == 0 else h for i, (_, h, _) in enumerate(heights)]
        total_h = sum(row_heights) + gap * (len(lines) - 1)
        y = y0 + (box_h - total_h) / 2

        w0, h0, top0 = heights[0]
        row0_h = row_heights[0]
        emoji_y = y + (row0_h - emoji_size) / 2
        row_w = emoji_size + gap + w0
        row_x = center_x - row_w / 2
        _paste_emoji(im, emoji_char, int(row_x + emoji_size / 2), int(emoji_y), emoji_size)
        draw.text((row_x + emoji_size + gap, y - top0 + (row0_h - h0) / 2), first_line, font=font, fill=_TEXT_COLOR)
        y += row0_h + gap

        for i, line in enumerate(rest_lines, start=1):
            w, h, top = heights[i]
            draw.text((center_x - w / 2, y - top), line, font=font, fill=_TEXT_COLOR)
            y += h + gap

    elif emoji_char and text and mode == "stacked":
        emoji_size = 28
        lines = text.split("\n")
        font, gap = _fit_font(draw, lines, text_font, emoji_size, box_h)
        heights = [_ink_size(draw, ln, font) for ln in lines]
        total_h = emoji_size + gap * len(lines) + sum(h for _, h, _ in heights)
        y = y0 + (box_h - total_h) / 2
        _paste_emoji(im, emoji_char, int(center_x), int(y), emoji_size)
        y += emoji_size + gap
        for i, (w, h, top) in enumerate(heights):
            draw.text((center_x - w / 2, y - top), lines[i], font=font, fill=_TEXT_COLOR)
            y += h + gap

    elif emoji_char:
        emoji_size = 40
        _paste_emoji(im, emoji_char, int(center_x), int(y0 + (box_h - emoji_size) / 2), emoji_size)
    elif text:
        _draw_centered_text(draw, box, text, text_font)


async def build_story_image(
    conn: aiosqlite.Connection, day_pattern: str, time: str, lesson_date: dt.date, out_path: str
) -> str:
    layout = story_layout.STORY_LAYOUTS[(day_pattern, time)]
    im = Image.open(os.path.join(ASSETS_DIR, layout["file"])).convert("RGB")
    draw = ImageDraw.Draw(im)

    if _TEXT_FONT_PATH:
        text_font = ImageFont.truetype(_TEXT_FONT_PATH, 22)
    else:
        text_font = ImageFont.load_default(size=22)

    for i, name in enumerate(layout["rows"]):
        box = _pill_box(i)
        group = await db.get_group_by_name_time_pattern(conn, name, day_pattern, time)
        if group is None:
            continue

        if group["locked"]:
            _draw_pill(im, draw, box, "🔒", None, None, text_font)
            continue

        resp = await db.get_response(conn, group["id"], lesson_date)
        if resp is None:
            continue  # немає відповіді - пігулку лишаємо порожньою

        status_key = resp["status_key"]
        emoji_char, text, mode = _STORY_CONTENT.get(status_key, (None, None, None))
        if status_key == "substitute" and resp["extra_name"]:
            text = f"ЗАМІНА\n{resp['extra_name']}"
        _draw_pill(im, draw, box, emoji_char, text, mode, text_font)

    im.save(out_path, quality=95)
    return out_path


def _patterns_for_date(lesson_date: dt.date) -> list[str]:
    weekday = lesson_date.weekday()
    return [p for p, days in config.DAY_PATTERN_WEEKDAYS.items() if weekday in days]


async def build_day_pdf(
    conn: aiosqlite.Connection, lesson_date: dt.date, out_dir: str
) -> Optional[str]:
    """Генерує картинку на кожну годину, яка є в макетах для цього дня, і збирає в один PDF."""
    os.makedirs(out_dir, exist_ok=True)
    patterns = _patterns_for_date(lesson_date)

    slots = [
        (pattern, time) for pattern in patterns
        for (p, time) in story_layout.STORY_LAYOUTS
        if p == pattern
    ]
    slots.sort(key=lambda pt: pt[1])
    if not slots:
        return None

    images = []
    for pattern, time in slots:
        safe_time = time.replace(":", "_")
        img_path = os.path.join(out_dir, f"{lesson_date.isoformat()}_{pattern}_{safe_time}.jpg")
        await build_story_image(conn, pattern, time, lesson_date, img_path)
        images.append(Image.open(img_path).convert("RGB"))

    pdf_path = os.path.join(out_dir, f"{lesson_date.isoformat()}.pdf")
    images[0].save(pdf_path, save_all=True, append_images=images[1:])
    return pdf_path
