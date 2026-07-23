import os
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHOREO_GROUP_CHAT_ID = int(os.environ["CHOREO_GROUP_CHAT_ID"])
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])
CLIENT_GROUP_CHAT_ID = os.environ.get("CLIENT_GROUP_CHAT_ID")
if CLIENT_GROUP_CHAT_ID:
    CLIENT_GROUP_CHAT_ID = int(CLIENT_GROUP_CHAT_ID)

INSTAGRAM_CHAT_ID = os.environ.get("INSTAGRAM_CHAT_ID")
if INSTAGRAM_CHAT_ID:
    INSTAGRAM_CHAT_ID = int(INSTAGRAM_CHAT_ID)

STORY_OUTPUT_DIR = os.environ.get("STORY_OUTPUT_DIR", "generated_stories")

CLIENT_PUBLISHING_ENABLED = os.environ.get("CLIENT_PUBLISHING_ENABLED", "false").lower() == "true"

TZ = ZoneInfo("Europe/Kyiv")
DB_PATH = os.environ.get("DB_PATH", "bot.db")

POLL_START_TIME = "21:00"
REMINDER_TIME = "22:00"
TABLE_TIME = "22:30"

MAX_CANCELS_PER_MONTH = 2
MAX_SUBSTITUTES_PER_MONTH = 2
MAX_MISSED_POLLS_PER_MONTH = 3

CHOREOGRAPHERS = [
    "Бучковська Юлія",
    "Відар Поліна",
    "Горудько Надя",
    "Каур Єлизавета",
    "Копитко Юля",
    "Соня Криця",
    "Кулик Дмитро",
    "Мінкевич Марія",
    "Медвідь Богдан",
    "Мисько Аня",
    "Мурачева Женя",
    "Нікітченко Ліля",
    "Нетлюх Юля",
    "Острянко Нана",
    "Росінська Віка",
    "Сковира Віка",
    "Умрихін Катя",
    "Тучапець Оля",
]

# Дозволені часові слоти занять
TIME_SLOTS = ["17:00", "18:00", "19:00", "20:00"]

STYLES = [
    "Kids group (6-8) Street Dance",
    "Kids group (9-11) Street Dance",
    "Juniors (12-14) Choreography",
    "K-Pop / Hip-Hop",
    "Hip-Hop",
    "Choreography",
    "Jazz-Funk",
    "High-Heels",
    "Contemporary",
    "Twerk",
    "Waacking",
]

HALLS = ["white", "blue", "purple", "green", "pink"]
MAX_HALLS = len(HALLS)

HALL_EMOJI = {
    "white": "⬜️",
    "blue": "🟦",
    "purple": "🟪",
    "green": "🟩",
    "pink": "💖",
}

DAY_ABBR = {0: "ПН", 1: "ВТ", 2: "СР", 3: "ЧТ", 4: "ПТ", 5: "СБ", 6: "НД"}

# Статуси опитування: key -> текст кнопки, підпис (короткий, для звірки), фраза для рядка
# розкладу, і чи можна доєднатись: True / False / "mk" (лише МК) / "other_group" (відміна)
STATUSES = {
    "first": {
        "button": "1️⃣ Перше заняття (можна)",
        "label": "перше заняття",
        "phrase": "🆕 НОВА ХОРЕОГРАФІЯ, можна приєднатися",
        "can_join": True,
    },
    "second_ok": {
        "button": "2️⃣ Друге заняття - можна",
        "label": "друге заняття",
        "phrase": "друге заняття по хореографії, можна приєднатися",
        "can_join": True,
    },
    "second_no": {
        "button": "2️⃣ Друге заняття - не можна",
        "label": "друге заняття",
        "phrase": "друге заняття по хореографії, не можна приєднатися",
        "can_join": False,
    },
    "last_mk": {
        "button": "🎓 Останнє заняття (лише МК)",
        "label": "останнє заняття - в форматі МК",
        "phrase": "🎓 останнє заняття, лише формат МК",
        "can_join": "mk",
    },
    "filming": {
        "button": "🎥 Зйомка відео",
        "label": "зйомка відео",
        "phrase": "🎦 ЗЙОМКА ВІДЕО, не можна приєднатися",
        "can_join": False,
    },
    "substitute": {
        "button": "🔁 Заміна тренера",
        "label": "заміна тренера",
        "phrase": "🔀 ЗАМІНА, можна приєднатися",
        "can_join": True,
    },
    "cancelled": {
        "button": "🚫 Відміна заняття",
        "label": "відміна заняття",
        "phrase": "🚫 ВІДМІНА, обери іншу групу на цей день",
        "can_join": "other_group",
    },
}

STATUS_EMOJI_LEGEND = {
    "🆕": "нова хореографія, можна приєднатися",
    "🎦": "зйомка відео, не можна приєднатися",
    "🔀": "заміна тренера, можна приєднатися",
    "🎓": "лише у форматі МК (майстер-класу)",
    "🚫": "відміна, обери іншу групу на цей день",
    "🔒": "закрита група, за набір уточнюйте у адміністратора",
}

CAT_EMOJI = ["🐱", "🐈", "😺", "😸", "🙀", "😻", "🐈‍⬛"]

# Розклад-патерни. day_pattern визначає, в які дні тижня діє група
# (кожна група проводиться двічі на тиждень, крім wed_only):
#   "mon_thu" — понеділок + четвер
#   "tue_fri" — вівторок + п'ятниця
#   "wed_only" — тільки середа
# weekday(): пн=0 ... нд=6
DAY_PATTERN_WEEKDAYS = {
    "mon_thu": {0, 3},  # групи двічі на тиждень: понеділок + четвер
    "tue_fri": {1, 4},  # групи двічі на тиждень: вівторок + п'ятниця
    "wed_only": {2},    # групи раз на тиждень: середа
}

SCHEDULE = [
    # --- ПН-ЧТ ---
    {"day_pattern": "mon_thu", "time": "16:30", "choreographer": "Копитко Юля",
     "style": "Street Dance (6-11)", "level": 2, "hall": "white", "locked": True},
    {"day_pattern": "mon_thu", "time": "17:00", "choreographer": "Росінська Віка",
     "style": "Street Dance (12-14)", "level": 1, "hall": "blue", "start_date": "2026-08-01"},
    {"day_pattern": "mon_thu", "time": "18:00", "choreographer": "Мінкевич Марія",
     "style": "Jazz-Funk (16+)", "level": 1, "hall": "purple", "locked": True},
    {"day_pattern": "mon_thu", "time": "18:00", "choreographer": "Росінська Віка",
     "style": "Choreography", "level": 1, "hall": "pink", "start_date": "2026-07-27"},
    {"day_pattern": "mon_thu", "time": "18:00", "choreographer": "Мисько Аня",
     "style": "Jazz-Funk", "level": 1, "hall": "green"},
    {"day_pattern": "mon_thu", "time": "18:00", "choreographer": "Тучапець Оля",
     "style": "Contemporary", "level": 1, "hall": "white"},
    {"day_pattern": "mon_thu", "time": "18:00", "choreographer": "Умрихін Катя",
     "style": "High Heels", "level": 1, "hall": "blue", "start_date": "2026-07-27"},
    {"day_pattern": "mon_thu", "time": "19:00", "choreographer": "Бучковська Юлія",
     "style": "Twerk (16+)", "level": 1, "hall": "blue"},
    {"day_pattern": "mon_thu", "time": "19:00", "choreographer": "Нетлюх Юля",
     "style": "High Heels (16+)", "level": 1, "hall": "white"},
    {"day_pattern": "mon_thu", "time": "19:00", "choreographer": "Росінська Віка",
     "style": "Choreography", "level": 2, "hall": "green", "start_date": "2026-07-27"},
    {"day_pattern": "mon_thu", "time": "19:00", "choreographer": "Медвідь Богдан",
     "style": "Contemporary", "level": 1, "hall": "purple"},
    {"day_pattern": "mon_thu", "time": "19:00", "choreographer": "Острянко Нана",
     "style": "Jazz Funk", "level": 1, "hall": "pink"},
    {"day_pattern": "mon_thu", "time": "20:00", "choreographer": "Мінкевич Марія",
     "style": "High Heels (16+)", "level": 1, "hall": "purple", "locked": True},
    {"day_pattern": "mon_thu", "time": "20:00", "choreographer": "Копитко Юля",
     "style": "Choreography", "level": 1, "hall": "blue"},
    {"day_pattern": "mon_thu", "time": "20:00", "choreographer": "Острянко Нана",
     "style": "V.L.C.ARTIST CREW", "level": None, "hall": "white", "locked": True},
    {"day_pattern": "mon_thu", "time": "20:00", "choreographer": "Мисько Аня",
     "style": "High Heels (16+)", "level": 1, "hall": "pink"},
    {"day_pattern": "mon_thu", "time": "20:00", "choreographer": "Кулик Дмитро",
     "style": "Jazz Funk", "level": 1, "hall": "green"},

    # --- ВТ-ПТ ---
    {"day_pattern": "tue_fri", "time": "16:30", "choreographer": "Відар Поліна",
     "style": "Choreography CREW", "level": None, "hall": "green", "locked": True},
    {"day_pattern": "tue_fri", "time": "17:00", "choreographer": "Нікітченко Ліля",
     "style": "K-Pop / Hip-Hop", "level": 1, "hall": "pink"},
    {"day_pattern": "tue_fri", "time": "17:00", "choreographer": "Умрихін Катя",
     "style": "Street Dance (9-11)", "level": 1, "hall": "blue", "start_date": "2026-09-01"},
    {"day_pattern": "tue_fri", "time": "17:00", "choreographer": "Копитко Юля",
     "style": "Street Dance (6-8)", "level": None, "hall": "white", "start_date": "2026-08-01"},
    {"day_pattern": "tue_fri", "time": "18:00", "choreographer": "Росінська Віка",
     "style": "ROYS CREW", "level": None, "hall": "purple", "locked": True},
    {"day_pattern": "tue_fri", "time": "18:00", "choreographer": "Відар Поліна",
     "style": "Choreography Juniors (12-14)", "level": None, "hall": "white", "locked": True},
    {"day_pattern": "tue_fri", "time": "18:00", "choreographer": "Умрихін Катя",
     "style": "Jazz-Funk", "level": 1, "hall": "blue"},
    {"day_pattern": "tue_fri", "time": "18:00", "choreographer": "Мисько Аня",
     "style": "Jazz-Funk", "level": 2, "hall": "green"},
    {"day_pattern": "tue_fri", "time": "19:00", "choreographer": "Мурачева Женя",
     "style": "Jazz-Funk", "level": 1, "hall": "green"},
    {"day_pattern": "tue_fri", "time": "19:00", "choreographer": "Горудько Надя",
     "style": "Contemporary", "level": 1, "hall": "purple"},
    {"day_pattern": "tue_fri", "time": "19:00", "choreographer": "Умрихін Катя",
     "style": "Jazz-Funk (16+)", "level": 2, "hall": "white", "start_date": "2026-07-27"},
    {"day_pattern": "tue_fri", "time": "19:00", "choreographer": "Острянко Нана",
     "style": "Hip-Hop", "level": 1, "hall": "pink"},
    {"day_pattern": "tue_fri", "time": "19:00", "choreographer": "Відар Поліна",
     "style": "Choreography", "level": 2, "hall": "blue", "start_date": "2026-09-04"},
    {"day_pattern": "tue_fri", "time": "20:00", "choreographer": "Умрихін Катя",
     "style": "FURIAs crew", "level": 3, "hall": "purple", "locked": True},
    {"day_pattern": "tue_fri", "time": "20:00", "choreographer": "Мурачева Женя",
     "style": "Twerk", "level": 1, "hall": "white"},
    {"day_pattern": "tue_fri", "time": "20:00", "choreographer": "Копитко Юля",
     "style": "High Heels", "level": 1, "hall": "blue"},
    {"day_pattern": "tue_fri", "time": "20:00", "choreographer": "Горудько Надя",
     "style": "Stretching", "level": 1, "hall": "pink"},
    {"day_pattern": "tue_fri", "time": "20:00", "choreographer": "Соня Криця",
     "style": "Contemporary", "level": 1, "hall": "green"},

    # --- СР ---
    {"day_pattern": "wed_only", "time": "18:00", "choreographer": "Каур Єлизавета",
     "style": "Waacking", "level": 1, "hall": "pink", "start_date": "2026-08-05"},
]
