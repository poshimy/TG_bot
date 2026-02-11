import logging
import sqlite3
from datetime import datetime, time, timedelta
from typing import Tuple, Optional
import random

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TOKEN = "8548990276:AAGrWvWG1ayEjtDC-AXNhwkfN_St4xcxGz0"

# После первого запуска можешь узнать ID командой /my_id
# и подставить сюда свои значения:
# твой Telegram ID (админ, кто ставит оценки и дарит подарки)
ADMIN_ID = 5777226021
GIRL1_ID = 802179704       # Telegram ID девушки (на её счёт будут идти баллы)
GIRL2_ID = 6894316538

DB_NAME = "pet_bot.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

PET_EMOJI = {
    "cat": "🐱",
    "dog": "🐶",
    "bunny": "🐰",
}

REWARDS = {
    "massage": ("💆 Массаж", 50),
    "dishes": ("🍽 Ты моешь посуду", 30),
    "movie": ("🎬 Вечер фильма по её выбору", 40),
}

# Штрафы за пропуск приёма пищи — по баллам
MEAL_POINT_PENALTIES = {
    "завтрак": 5,
    "обед": 7,
    "ужин": 8,
}

# Фразы для напоминаний о пропуске приёма пищи
MEAL_REMINDER_PHRASES = {
    "завтрак": [
        "Кхм... Ты ничего не забыла с утра? Кто-то ждёт завтрак 🌅",
        "Твой питомец уже гремит миской. Завтрак сам себя не съест 🙂",
        "Утро–утром, а животик питомца всё ещё пустой...",
        "Питомец подозрительно смотрит на холодильник. Может, время завтрака?"
    ],
    "обед": [
        "Кхм... Пахнет обедом, а миска всё ещё пустая 👀",
        "Питомец выглядывает из-за стола: обед сегодня будет?",
        "Кажется, кто-то рассчитывал на обед...",
        "Животик питомца тихо урчит: пообедаем?"
    ],
    "ужин": [
        "Кхм... Вечер на дворе, а ужин где? 😏",
        "Питомец уже надел воображаемый слюнявчик. Ждёт ужин!",
        "Время уютного ужина, а миска почему-то пуста...",
        "Кто-то собирался ужинать вместе с тобой 🐾"
    ],
    "default": [
        "Кхм... Ты ничего не забыла?",
        "Питомец подсказывает, что его забыли покормить.",
        "Миска грустно смотрит на тебя — пора что-то с этим сделать."
    ],
}

# Окна кормления и кулдауны
FEED_WINDOWS = [
    ("завтрак", time(7, 0), time(11, 0)),
    ("обед",    time(12, 0), time(16, 0)),
    ("ужин",    time(18, 0), time(22, 0)),
]
FEED_COOLDOWN = timedelta(hours=3)

PLAY_ALLOWED = (time(9, 0), time(23, 0))
PLAY_COOLDOWN = timedelta(hours=1)

WASH_ALLOWED = (time(8, 0), time(22, 0))
WASH_COOLDOWN = timedelta(hours=6)

SLEEP_ALLOWED_NIGHT = (time(21, 0), time(3, 0))
SLEEP_COOLDOWN = timedelta(hours=6)
SLEEP_ALLOWED_NIGHT = (time(21, 0), time(3, 0))
SLEEP_COOLDOWN = timedelta(hours=6)

# Минимальная длительность сна – в это время нельзя играть (и, если захочешь, мыть/кормить)
SLEEP_MIN_DURATION = timedelta(hours=6)
# Авто‑спад параметров в час
HUNGER_DECAY_PER_HOUR = 5       # сытость -5 за час
MOOD_DECAY_PER_HOUR = 2         # настроение -2 за час
CLEAN_DECAY_PER_HOUR = 1        # чистота -1 за час
ENERGY_DECAY_PER_HOUR = 2       # энергия -2 за час

# Слова для ежедневной игры Wordle (все 5 букв, на русском)
WORDLE_WORDS = [
    "КОТИК",
    "ПЕСИК",
    "ЗАЙКА",
    "ЛАПКА",
    "УСИКИ",
    "ХВОСТ",
]


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ВРЕМЕНИ ===

def is_time_in_range(start: time, end: time, current: time) -> bool:
    if start <= end:
        return start <= current < end
    else:
        return current >= start or current < end


def get_feed_window_for_time(t: time) -> Optional[str]:
    for name, s, e in FEED_WINDOWS:
        if is_time_in_range(s, e, t):
            return name
    return None


def get_feed_window_for_datetime(dt: datetime) -> Optional[str]:
    return get_feed_window_for_time(dt.time())


def normalize_word(text: str) -> str:
    return text.strip().upper().replace("Ё", "Е")


# === РАБОТА С БАЗОЙ ===

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            pet_type TEXT,
            points INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS pet_state (
            user_id INTEGER PRIMARY KEY,
            hunger INTEGER DEFAULT 70,
            mood INTEGER DEFAULT 70,
            cleanliness INTEGER DEFAULT 70,
            energy INTEGER DEFAULT 70,
            last_feed TEXT,
            last_play TEXT,
            last_wash TEXT,
            last_sleep TEXT,
            last_update TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS wordle_quest (
            user_id INTEGER,
            date TEXT,
            secret TEXT,
            attempts_left INTEGER,
            status TEXT,
            PRIMARY KEY (user_id, date)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS care_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            ts TEXT
        )
    """)

    # Добавляем недостающие колонки, если база старая
    for col in ("last_feed", "last_play", "last_wash", "last_sleep", "last_update"):
        try:
            c.execute(f"ALTER TABLE pet_state ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


def ensure_user(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id, points) VALUES (?, 0)",
        (user_id,),
    )
    c.execute(
        "INSERT OR IGNORE INTO pet_state (user_id) VALUES (?)",
        (user_id,),
    )
    conn.commit()
    conn.close()


def set_pet_type(user_id: int, pet_type: str):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "UPDATE users SET pet_type = ? WHERE user_id = ?",
        (pet_type, user_id),
    )
    conn.commit()
    conn.close()


def log_care_action(user_id: int, action: str):
    """Записать факт ухода за питомцем в лог."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT INTO care_log (user_id, action, ts) VALUES (?, ?, ?)",
        (user_id, action, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def apply_auto_decay(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT hunger, mood, cleanliness, energy, last_update FROM pet_state WHERE user_id = ?",
        (user_id,),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return

    hunger, mood, cleanliness, energy, last_update_str = row
    now = datetime.now()

    if last_update_str:
        try:
            last_update = datetime.fromisoformat(last_update_str)
        except ValueError:
            last_update = now
    else:
        last_update = now

    delta_hours = (now - last_update).total_seconds() / 3600.0
    if delta_hours > 0.05:
        hunger = max(0, int(hunger - HUNGER_DECAY_PER_HOUR * delta_hours))
        mood = max(0, int(mood - MOOD_DECAY_PER_HOUR * delta_hours))
        cleanliness = max(
            0, int(cleanliness - CLEAN_DECAY_PER_HOUR * delta_hours))
        energy = max(0, int(energy - ENERGY_DECAY_PER_HOUR * delta_hours))

        c.execute(
            """
            UPDATE pet_state
            SET hunger = ?, mood = ?, cleanliness = ?, energy = ?, last_update = ?
            WHERE user_id = ?
            """,
            (hunger, mood, cleanliness, energy, now.isoformat(), user_id),
        )
    else:
        if not last_update_str:
            c.execute(
                "UPDATE pet_state SET last_update = ? WHERE user_id = ?",
                (now.isoformat(), user_id),
            )

    conn.commit()
    conn.close()


def get_full_status(user_id: int):
    apply_auto_decay(user_id)

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """
        SELECT u.pet_type, u.points,
               p.hunger, p.mood, p.cleanliness, p.energy
        FROM users u
        JOIN pet_state p ON u.user_id = p.user_id
        WHERE u.user_id = ?
        """,
        (user_id,),
    )
    row = c.fetchone()
    conn.close()
    return row


def apply_care_action(user_id: int, action: str) -> Tuple[int, int, int, int]:
    apply_auto_decay(user_id)

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT hunger, mood, cleanliness, energy FROM pet_state WHERE user_id = ?",
        (user_id,),
    )
    row = c.fetchone()
    if not row:
        hunger = mood = cleanliness = energy = 70
    else:
        hunger, mood, cleanliness, energy = row

    if action == "feed":
        base_gain = 20
        deficit = 100 - hunger
        bonus = 0
        if deficit > base_gain:
            bonus = min(10, deficit - base_gain)
        hunger = min(100, hunger + base_gain + bonus)

    elif action == "play":
        base_gain = 20
        deficit = 100 - mood
        bonus = 0
        if deficit > base_gain:
            bonus = min(5, deficit - base_gain)
        mood = min(100, mood + base_gain + bonus)
        energy = max(0, energy - 10)

    elif action == "wash":
        base_gain = 25
        deficit = 100 - cleanliness
        bonus = 0
        if deficit > base_gain:
            bonus = min(10, deficit - base_gain)
        cleanliness = min(100, cleanliness + base_gain + bonus)

    elif action == "sleep":
        base_gain = 25
        deficit = 100 - energy
        bonus = 0
        if deficit > base_gain:
            bonus = min(10, deficit - base_gain)
        energy = min(100, energy + base_gain + bonus)

    c.execute(
        """
        UPDATE pet_state
        SET hunger = ?, mood = ?, cleanliness = ?, energy = ?
        WHERE user_id = ?
        """,
        (hunger, mood, cleanliness, energy, user_id),
    )
    conn.commit()
    conn.close()
    return hunger, mood, cleanliness, energy


def add_points(user_id: int, amount: int) -> int:
    """Добавить (или снять) баллы и вернуть новый баланс. Не уходим ниже 0."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row:
        current = row[0]
    else:
        current = 0
        c.execute("INSERT INTO users (user_id, points) VALUES (?, 0)", (user_id,))

    new_balance = current + amount
    if new_balance < 0:
        new_balance = 0

    c.execute(
        "UPDATE users SET points = ? WHERE user_id = ?",
        (new_balance, user_id),
    )
    conn.commit()
    conn.close()
    return new_balance


def get_points(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def spend_points(user_id: int, amount: int) -> Tuple[bool, int]:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, 0
    balance = row[0]
    if balance < amount:
        conn.close()
        return False, balance

    new_balance = balance - amount
    c.execute(
        "UPDATE users SET points = ? WHERE user_id = ?",
        (new_balance, user_id),
    )
    conn.commit()
    conn.close()
    return True, new_balance


def get_last_action_time(user_id: int, action: str):
    column_map = {
        "feed": "last_feed",
        "play": "last_play",
        "wash": "last_wash",
        "sleep": "last_sleep",
    }
    col = column_map.get(action)
    if not col:
        return None

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(f"SELECT {col} FROM pet_state WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()

    if row and row[0]:
        try:
            return datetime.fromisoformat(row[0])
        except ValueError:
            return None
    return None


def get_care_stats(user_id: int, days: int = 7):
    """Вернуть статистику ухода за последние days дней."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """
        SELECT action, COUNT(*), MAX(ts)
        FROM care_log
        WHERE user_id = ? AND ts >= ?
        GROUP BY action
        """,
        (user_id, since),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def update_last_action(user_id: int, action: str):
    column_map = {
        "feed": "last_feed",
        "play": "last_play",
        "wash": "last_wash",
        "sleep": "last_sleep",
    }
    col = column_map.get(action)
    if not col:
        return

    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        f"UPDATE pet_state SET {col} = ? WHERE user_id = ?",
        (now, user_id),
    )
    conn.commit()
    conn.close()


def get_all_user_ids():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


# === WORDLE-QUEST В БД ===

def get_today_wordle_state(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    today = datetime.now().date().isoformat()
    c.execute(
        "SELECT secret, attempts_left, status FROM wordle_quest WHERE user_id = ? AND date = ?",
        (user_id, today),
    )
    row = c.fetchone()
    conn.close()
    return row


def save_today_wordle_state(user_id: int, secret: str, attempts_left: int, status: str):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    today = datetime.now().date().isoformat()
    c.execute(
        """
        INSERT OR REPLACE INTO wordle_quest (user_id, date, secret, attempts_left, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, today, secret, attempts_left, status),
    )
    conn.commit()
    conn.close()


def start_new_wordle_quest(user_id: int) -> Tuple[str, int]:
    secret = random.choice(WORDLE_WORDS)
    attempts_left = 6
    save_today_wordle_state(user_id, secret, attempts_left, "in_progress")
    return secret, attempts_left


# === ЛОГИКА ОГРАНИЧЕНИЙ ДЕЙСТВИЙ ===

def check_action_allowed(user_id: int, action: str) -> Tuple[bool, str]:
    """Проверяем, можно ли сейчас выполнить действие. Возвращаем (разрешено, причина_если_нет)."""
    now = datetime.now()
    now_t = now.time()

    # КОРМЛЕНИЕ
    if action == "feed":
        window_name = get_feed_window_for_time(now_t)
        if window_name is None:
            windows_str = "\n".join(
                f"• {name}: {s.strftime('%H:%M')}–{e.strftime('%H:%M')}"
                for name, s, e in FEED_WINDOWS
            )
            return False, (
                "Сейчас не время кормления.\n\n"
                "Кормить питомца можно только в такие периоды:\n"
                f"{windows_str}"
            )

        last = get_last_action_time(user_id, "feed")
        if last:
            delta = now - last
            if delta < FEED_COOLDOWN:
                mins = int((FEED_COOLDOWN - delta).total_seconds() // 60)
                return False, (
                    "Питомец недавно уже ел. Дай ему переварить еду 😊\n"
                    f"Покормить можно примерно через {mins} минут."
                )

            if last.date() == now.date():
                last_window = get_feed_window_for_datetime(last)
                if last_window == window_name:
                    return False, (
                        f"Сегодня ты уже кормила питомца на «{window_name}».\n"
                        "Следующее кормление — в другой приём пищи."
                    )

        return True, ""

    # ИГРЫ
    if action == "play":
        # Нельзя играть, пока питомец спит
        last_sleep = get_last_action_time(user_id, "sleep")
        if last_sleep:
            since_sleep = now - last_sleep
            if since_sleep < SLEEP_MIN_DURATION:
                mins = int(
                    (SLEEP_MIN_DURATION - since_sleep).total_seconds() // 60)
                extra = f"\nПодожди ещё примерно {mins} минут." if mins > 0 else ""
                return False, (
                    "Питомец сейчас спит 😴\n"
                    "Не будем его будить играми." + extra
                )

        start, end = PLAY_ALLOWED
        if not is_time_in_range(start, end, now_t):
            return False, (
                "Сейчас не лучшее время для игр.\n"
                f"Играть можно с {start.strftime('%H:%M')} до {end.strftime('%H:%M')}."
            )

        last = get_last_action_time(user_id, "play")
        if last:
            delta = now - last
            if delta < PLAY_COOLDOWN:
                mins = int((PLAY_COOLDOWN - delta).total_seconds() // 60)
                return False, (
                    "Питомец немного устал от игр.\n"
                    f"Попробуй поиграть ещё через {mins} минут."
                )

        return True, ""

    # МЫТЬЁ
    if action == "wash":
        start, end = WASH_ALLOWED
        if not is_time_in_range(start, end, now_t):
            return False, (
                "Сейчас не время для водных процедур.\n"
                f"Купать питомца можно с {start.strftime('%H:%M')} до {end.strftime('%H:%M')}."
            )

        last = get_last_action_time(user_id, "wash")
        if last:
            delta = now - last
            if delta < WASH_COOLDOWN:
                hours = int((WASH_COOLDOWN - delta).total_seconds() // 3600)
                return False, (
                    "Питомец и так уже чистый.\n"
                    f"Следующее купание можно примерно через {hours} часов."
                )

        return True, ""

    # СОН
    if action == "sleep":
        start, end = SLEEP_ALLOWED_NIGHT
        if not is_time_in_range(start, end, now_t):
            status = get_full_status(user_id)
            energy = status[5] if status else None
            base = (
                "Сейчас не лучшее время укладывать питомца спать.\n"
                "Спать он должен вечером и ночью, а днём лучше поиграть или поесть."
            )
            if energy is not None and energy > 70:
                return False, (
                    base + "\n\n"
                    "Питомец ещё бодрый, у него много энергии 🙂"
                )
            return False, base

        last = get_last_action_time(user_id, "sleep")
        if last:
            delta = now - last
            if delta < SLEEP_COOLDOWN:
                hours = int((SLEEP_COOLDOWN - delta).total_seconds() // 3600)
                return False, (
                    "Питомец ещё не успел как следует выспаться и проснуться.\n"
                    f"Попробуй уложить его спать через {hours} часов."
                )

        return True, ""

    return True, ""


# === ШТРАФЫ ЗА ПРОПУСК КОРМЛЕНИЯ ===

async def check_missed_meal(context: ContextTypes.DEFAULT_TYPE):
    """Проверяем, кто не покормил питомца в нужное окно, штрафуем по баллам и пишем рандомную фразу."""
    job = context.job
    meal_name = job.data["meal_name"]
    now = datetime.now()
    today = now.date()

    user_ids = get_all_user_ids()
    if not user_ids:
        return

    for user_id in user_ids:
        status_before = get_full_status(user_id)
        if not status_before or not status_before[0]:
            continue

        last = get_last_action_time(user_id, "feed")
        fed_ok = False
        if last and last.date() == today:
            last_window = get_feed_window_for_datetime(last)
            if last_window == meal_name:
                fed_ok = True

        if fed_ok:
            continue

        penalty = MEAL_POINT_PENALTIES.get(meal_name, 5)
        new_points = add_points(user_id, -penalty)

        status = get_full_status(user_id)
        if not status:
            continue
        pet_type, points, hunger, mood, cleanliness, energy = status

        phrases = MEAL_REMINDER_PHRASES.get(
            meal_name) or MEAL_REMINDER_PHRASES["default"]
        phrase = random.choice(phrases)

        text = (
            f"{phrase}\n\n"
            f"Питомец так и не получил {meal_name} и немного расстроился 😢\n"
            f"Штраф: -{penalty} баллов. Твой новый баланс: {new_points}.\n\n"
            f"Текущий статус:\n"
            f"Сытость:      {hunger}/100\n"
            f"Настроение:   {mood}/100\n"
            f"Чистота:      {cleanliness}/100\n"
            f"Энергия:      {energy}/100"
        )

        try:
            await context.bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            logger.warning(
                "Не удалось отправить сообщение про штраф пользователю %s: %s", user_id, e)


async def daily_rate_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Напоминание админу поставить оценку за день."""
    if ADMIN_ID == 0:
        return

    text = (
        "Кхм-кхм, уже пора подвести итоги дня 🌙\n\n"
        "Поставь оценку за сегодняшнюю заботу о питомце:\n"
        "например, /rate 1 5 или /rate 2 4"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text)
    except Exception as e:
        logger.warning("Не удалось отправить напоминание админу: %s", e)


# === КОМАНДЫ ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id)

    keyboard = [
        [InlineKeyboardButton("🐱 Котик", callback_data="choose_pet_cat")],
        [InlineKeyboardButton("🐶 Пёсик", callback_data="choose_pet_dog")],
        [InlineKeyboardButton("🐰 Зайчик", callback_data="choose_pet_bunny")],
    ]
    text = (
        "Привет! Я ваш личный питомец-бот 🐾\n\n"
        "Выбери себе питомца, за которым ты будешь ухаживать.\n"
        "За заботу ты будешь получать баллы, а потом менять их на подарки 💝"
    )
    await update.effective_message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return  # только админ

    if not context.args:
        await update.effective_message.reply_text(
            "Использование: /stats <номер_девушки>\n"
            "Пример: /stats 1 или /stats 2"
        )
        return

    try:
        girl_num = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Номер девушки должен быть 1 или 2.")
        return

    if girl_num not in (1, 2):
        await update.effective_message.reply_text("Номер девушки должен быть 1 или 2.")
        return

    girl_id = GIRL1_ID if girl_num == 1 else GIRL2_ID
    if girl_id == 0:
        await update.effective_message.reply_text(
            f"GIRL{girl_num}_ID ещё не настроен."
        )
        return

    days = 7  # за сколько дней смотреть
    rows = get_care_stats(girl_id, days=days)
    if not rows:
        await update.effective_message.reply_text(
            f"За последние {days} дней у девушки №{girl_num} нет действий ухода."
        )
        return

    lines = [
        f"Статистика ухода за последние {days} дней для девушки №{girl_num}:"]
    action_names = {
        "feed": "кормить",
        "play": "играть",
        "wash": "мыть",
        "sleep": "укладывать спать",
    }

    for action, count, last_ts in rows:
        last_time = ""
        if last_ts:
            dt = datetime.fromisoformat(last_ts)
            last_time = dt.strftime("%d.%m %H:%M")
        name = action_names.get(action, action)
        lines.append(f"- {name}: {count} раз(а), последний раз: {last_time}")

    await update.effective_message.reply_text("\n".join(lines))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Список команд бота:\n\n"
        "/start – выбрать/сменить питомца\n"
        "/help – показать этот список команд\n"
        "/pet – показать статус питомца\n"
        "/care – ухаживать за питомцем (кормить, играть и т.д.)\n"
        "/balance – посмотреть, сколько у тебя баллов\n"
        "/shop – магазин подарков (тратить баллы)\n"
        "/quest – ежедневный квест (мини-игра Wordle)\n"
        "/my_id – показать твой Telegram ID\n"
        "/rate <номер_девушки> <оценка_1..5> – только для админа\n"
        "   пример: /rate 1 5 – оценить девушку №1 на 5\n"
    )
    await update.effective_message.reply_text(text)


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.effective_message.reply_text(
        f"Твой Telegram ID: `{user.id}`",
        parse_mode="Markdown",
    )


async def pet_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    status = get_full_status(user_id)
    if not status or not status[0]:
        await update.effective_message.reply_text(
            "Сначала выбери питомца командой /start."
        )
        return

    pet_type, points, hunger, mood, cleanliness, energy = status
    emoji = PET_EMOJI.get(pet_type, "🐾")

    text = (
        f"{emoji} Статус твоего питомца:\n\n"
        f"Сытость:      {hunger}/100\n"
        f"Настроение:   {mood}/100\n"
        f"Чистота:      {cleanliness}/100\n"
        f"Энергия:      {energy}/100\n\n"
        f"Твои баллы:   {points}"
    )
    await update.effective_message.reply_text(text)


async def care(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    keyboard = [
        [InlineKeyboardButton("🥣 Покормить", callback_data="care_feed")],
        [InlineKeyboardButton("🎾 Поиграть", callback_data="care_play")],
        [InlineKeyboardButton("🧼 Помыть", callback_data="care_wash")],
        [InlineKeyboardButton("😴 Уложить спать", callback_data="care_sleep")],
    ]
    await update.effective_message.reply_text(
        "Выбери, как ты позаботишься о питомце:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    pts = get_points(user_id)
    await update.effective_message.reply_text(f"У тебя сейчас {pts} баллов 💖")


async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    allowed_ids = {i for i in (GIRL1_ID, GIRL2_ID, ADMIN_ID) if i != 0}
    if allowed_ids and user.id not in allowed_ids:
        await update.effective_message.reply_text(
            "Магазин подарков доступен только для выбранных пользователей 💕"
        )
        return

    ensure_user(user.id)
    keyboard = []
    for key, (name, price) in REWARDS.items():
        keyboard.append(
            [InlineKeyboardButton(
                f"{name} — {price} баллов", callback_data=f"buy_{key}")]
        )

    keyboard.append(
        [InlineKeyboardButton("Проверить баланс",
                              callback_data="shop_balance")]
    )

    await update.effective_message.reply_text(
        "🎁 Магазин подарков. Выбери, что хочешь получить:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if ADMIN_ID == 0:
        await update.effective_message.reply_text(
            "Сначала настрой ADMIN_ID в коде (см. /my_id)."
        )
        return

    if user.id != ADMIN_ID:
        await update.effective_message.reply_text(
            "Эта команда только для админа."
        )
        return

    if GIRL1_ID == 0 and GIRL2_ID == 0:
        await update.effective_message.reply_text(
            "Сначала настрой GIRL1_ID и/или GIRL2_ID в коде (см. /my_id)."
        )
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Использование: `/rate <номер_девушки> <оценка_1..5>`\n"
            "Например: `/rate 1 5` или `/rate 2 4`",
            parse_mode="Markdown",
        )
        return

    try:
        girl_num = int(context.args[0])
        value = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text(
            "Номер девушки и оценка должны быть числами.\n"
            "Пример: `/rate 1 5`",
            parse_mode="Markdown",
        )
        return

    if girl_num not in (1, 2):
        await update.effective_message.reply_text(
            "Номер девушки должен быть 1 или 2."
        )
        return

    if not 1 <= value <= 5:
        await update.effective_message.reply_text(
            "Оценка должна быть от 1 до 5."
        )
        return

    girl_id = GIRL1_ID if girl_num == 1 else GIRL2_ID
    if girl_id == 0:
        await update.effective_message.reply_text(
            f"Для девушки №{girl_num} ещё не настроен ID (GIRL{girl_num}_ID)."
        )
        return

    mapping = {1: 5, 2: 10, 3: 15, 4: 20, 5: 30}
    points_to_add = mapping[value]

    ensure_user(girl_id)
    new_balance = add_points(girl_id, points_to_add)

    await update.effective_message.reply_text(
        f"Начислил девушке №{girl_num} {points_to_add} баллов за сегодняшний день. "
        f"Теперь у неё {new_balance} баллов 💖"
    )

    try:
        await context.bot.send_message(
            chat_id=girl_id,
            text=(
                f"Сегодня ты получила {points_to_add} баллов "
                f"за заботу о питомце 🐾\n"
                f"Новый баланс: {new_balance} баллов 💕"
            ),
        )
    except Exception as e:
        logger.warning(
            "Не удалось отправить сообщение девушке №%s: %s", girl_num, e)


async def quest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт/продолжение ежедневного Wordle-квеста."""
    user = update.effective_user
    ensure_user(user.id)

    allowed_ids = {i for i in (GIRL1_ID, GIRL2_ID, ADMIN_ID) if i != 0}
    if allowed_ids and user.id not in allowed_ids:
        await update.effective_message.reply_text(
            "Ежедневный квест доступен только для выбранных пользователей 💕"
        )
        return

    state = get_today_wordle_state(user.id)
    if not state:
        secret, attempts_left = start_new_wordle_quest(user.id)
        word_len = len(secret)
        await update.effective_message.reply_text(
            f"Ежедневный квест: мини-игра Wordle 🎮\n\n"
            f"Я загадал слово из {word_len} букв.\n"
            "У тебя есть 6 попыток, чтобы его угадать.\n"
            "Просто отправляй мне слова в этом чате.\n\n"
            "🟩 — буква на своём месте\n"
            "🟨 — буква есть в слове, но в другом месте\n"
            "⬜ — такой буквы нет в слове\n\n"
            "За сегодняшнюю победу ты получишь бесплатное поглаживание спины 🤍"
        )
        return

    secret, attempts_left, status = state
    word_len = len(secret)

    if status == "won":
        await update.effective_message.reply_text(
            "Сегодня ты уже выиграла ежедневный квест 🎉\n"
            "Завтра будет новое слово!"
        )
    elif status == "lost":
        await update.effective_message.reply_text(
            "Сегодня попытки в квесте уже закончились 😿\n"
            f"Слово на сегодня было: {secret.upper()}.\n"
            "Загляни завтра за новым словом."
        )
    else:
        await update.effective_message.reply_text(
            f"Квест уже идёт! Я всё ещё жду слово из {word_len} букв.\n"
            f"Осталось попыток: {attempts_left}.\n"
            "Просто отправь мне слово сообщением."
        )


# === CALLBACK-КНОПКИ ===

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    await query.answer()

    if data.startswith("choose_pet_"):
        pet_type = data.split("_", 2)[2]
        ensure_user(user_id)
        set_pet_type(user_id, pet_type)
        emoji = PET_EMOJI.get(pet_type, "🐾")
        await query.message.reply_text(
            f"Отлично! Теперь у тебя {emoji} питомец.\n"
            f"Я буду твоим питомцем, надеюсь ты будешь меня любить!\n"
            f"↙️нажми сюда чтобы посмотреть на мои возможности:)"
        )
        return

    if data.startswith("care_"):
        action = data.split("_", 1)[1]
        ensure_user(user_id)

        allowed, reason = check_action_allowed(user_id, action)
        if not allowed:
            await query.message.reply_text(reason)
            return

        hunger, mood, cleanliness, energy = apply_care_action(user_id, action)
        update_last_action(user_id, action)
        log_care_action(user_id, action)

        action_text = {
            "feed": "Ты покормила питомца 🥣",
            "play": "Ты поиграла с питомцем 🎾",
            "wash": "Ты помыла питомца 🧼",
            "sleep": "Ты уложила питомца спать 😴",
        }.get(action, "Ты позаботилась о питомце 💕")

        text = (
            f"{action_text}\n\n"
            f"Текущий статус:\n"
            f"Сытость:      {hunger}/100\n"
            f"Настроение:   {mood}/100\n"
            f"Чистота:      {cleanliness}/100\n"
            f"Энергия:      {energy}/100"
        )
        await query.message.reply_text(text)
        return

    if data == "shop_balance":
        ensure_user(user_id)
        pts = get_points(user_id)
        await query.message.reply_text(f"У тебя сейчас {pts} баллов 💖")
        return

    if data.startswith("buy_"):
        key = data.split("_", 1)[1]
        if key not in REWARDS:
            await query.message.reply_text("Такого подарка нет.")
            return

        name, price = REWARDS[key]
        ensure_user(user_id)
        success, new_balance = spend_points(user_id, price)
        if not success:
            await query.message.reply_text(
                f"Пока не хватает баллов на: {name}.\n"
                f"Нужно {price}, а у тебя {new_balance}."
            )
            return

        await query.message.reply_text(
            f"Ты купила: {name} 🎁\n"
            f"С тебя {price} баллов, осталось {new_balance}."
        )

        if ADMIN_ID != 0:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"Напоминание: ты должен выполнить подарок — {name} 💝",
                )
            except Exception as e:
                logger.warning("Не удалось отправить сообщение админу: %s", e)

        return


# === ОБРАБОТКА ТЕКСТА (Wordle-попытки) ===

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    user_id = update.effective_user.id
    text = update.effective_message.text
    if not text:
        return

    allowed_ids = {i for i in (GIRL1_ID, GIRL2_ID, ADMIN_ID) if i != 0}
    if allowed_ids and user_id not in allowed_ids:
        return

    state = get_today_wordle_state(user_id)
    if not state:
        return

    secret, attempts_left, status = state
    if status != "in_progress" or attempts_left <= 0:
        return

    guess = normalize_word(text)
    if not guess.isalpha():
        await update.effective_message.reply_text(
            "Нужно отправить именно слово, без цифр и символов 🙂"
        )
        return

    if len(guess) != len(secret):
        await update.effective_message.reply_text(
            f"Я жду слово из {len(secret)} букв, а не из {len(guess)}."
        )
        return

    secret_upper = secret.upper()
    result = [""] * len(secret_upper)
    used = [False] * len(secret_upper)

    for i, ch in enumerate(guess):
        if ch == secret_upper[i]:
            result[i] = "🟩"
            used[i] = True

    for i, ch in enumerate(guess):
        if result[i]:
            continue
        found = False
        for j, sch in enumerate(secret_upper):
            if not used[j] and ch == sch:
                found = True
                used[j] = True
                break
        result[i] = "🟨" if found else "⬜"

    attempts_left -= 1

    if guess == secret_upper:
        save_today_wordle_state(user_id, secret, attempts_left, "won")

        await update.effective_message.reply_text(
            f"{guess}\n{''.join(result)}\n\n"
            "Умница! Ты угадала слово 🤍\n"
            "Ежедневный квест выполнен — получаешь бесплатное поглаживание спины!"
        )

        if ADMIN_ID != 0:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="Напоминание: она выиграла ежедневный квест Wordle и заслужила бесплатное поглаживание спины 🤍",
                )
            except Exception as e:
                logger.warning(
                    "Не удалось отправить сообщение админу про квест: %s", e)

        return

    if attempts_left <= 0:
        save_today_wordle_state(user_id, secret, attempts_left, "lost")
        await update.effective_message.reply_text(
            f"{guess}\n{''.join(result)}\n\n"
            f"Попытки на сегодня закончились 😿\n"
            f"Слово было: {secret_upper}.\n"
            "Загляни завтра за новым квестом!"
        )
        return

    save_today_wordle_state(user_id, secret, attempts_left, "in_progress")
    await update.effective_message.reply_text(
        f"{guess}\n{''.join(result)}\n\n"
        f"Осталось попыток: {attempts_left}.\n"
        "Продолжай угадывать!"
    )


# === ЗАПУСК ===

def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("my_id", my_id))
    app.add_handler(CommandHandler("pet", pet_status))
    app.add_handler(CommandHandler("care", care))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("shop", shop))
    app.add_handler(CommandHandler("rate", rate))
    app.add_handler(CommandHandler("quest", quest_command))
    app.add_handler(CommandHandler("stats", stats_command))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, text_message_handler))

    job_queue = app.job_queue

    job_queue.run_daily(
        check_missed_meal,
        time=time(11, 5),
        data={"meal_name": "завтрак"},
        name="breakfast_penalty",
    )

    job_queue.run_daily(
        check_missed_meal,
        time=time(16, 5),
        data={"meal_name": "обед"},
        name="lunch_penalty",
    )

    job_queue.run_daily(
        check_missed_meal,
        time=time(22, 5),
        data={"meal_name": "ужин"},
        name="dinner_penalty",
    )

    job_queue.run_daily(
        daily_rate_reminder,
        time=time(0, 0),
        name="rate_reminder",
    )

    print("Бот запущен. Нажми Ctrl+C для остановки.")
    app.run_polling()


if __name__ == "__main__":
    main()
