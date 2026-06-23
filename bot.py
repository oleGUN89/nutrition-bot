import asyncio
import re
import logging
import os
import random
import json
from datetime import time as dtime, timezone, timedelta, date
import asyncpg
from pydantic import BaseModel
from google import genai
from google.genai import types
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-3.5-flash"
FALLBACK_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL_2 = "gemini-2.0-flash"

water_enabled: set[int] = set()
DB_POOL = None
MSK = timezone(timedelta(hours=3))

WATER_SCHEDULE = [
    (dtime(9, 0, tzinfo=MSK), "💧 <b>Вода</b>\nНачни день со стакана воды (250 мл). <b>Цель: 2.2 л</b>."),
    (dtime(11, 0, tzinfo=MSK), "💧 <b>Вода</b>\nЕщё стакан. К обеду должно быть 2-3 из 6."),
    (dtime(13, 0, tzinfo=MSK), "🍽 <b>Перед обедом</b>\nВыпей стакан за 30 мин до еды — снизит аппетит."),
    (dtime(15, 0, tzinfo=MSK), "💧 <b>Вода</b>\nПослеобеденный стакан. Не путай жажду с голодом."),
    (dtime(18, 0, tzinfo=MSK), "💧 <b>Вода</b>\nЕщё стакан! Уже должно быть 5 из 6."),
    (dtime(21, 0, tzinfo=MSK), "🌙 <b>Итог дня</b>\nВыпил ~2 л сегодня? Отличная работа!\nЗавтра продолжай."),
]

# --- Pydantic ---

class RecognizedProduct(BaseModel):
    name: str
    amount: str
    category: str
    calories_per_100g: int
    protein_per_100g: float
    fat_per_100g: float
    carbs_per_100g: float

class AnalysisResult(BaseModel):
    products: list[RecognizedProduct]

# --- System prompt ---

SYSTEM_PROMPT = """Нутрициолог Нутри. Цель пользователя: похудение, мужчина 30-45 лет, сидячий образ жизни, дефицит 300-500 ккал.
Правила: белок в каждом приёме (25-30г), медленные углеводы, без сахара, практичные советы.
Язык: русский. Стиль: коротко, без приветствий и вводных фраз, сразу к сути.

Формат блюда:
<b>Название</b> (~NNN ккал)

<b>Ингредиенты</b>
• ингредиент — количество

<b>Приготовление</b>
1. <b>Шаг:</b> действие.
2. <b>Шаг:</b> действие.

Между блюдами пустая строка. Только теги <b> и <i>, никаких * и **.
Секция закупок: <b>Докупить</b> + список через •"""

# --- Database ---

async def init_db(app=None):
    global DB_POOL
    db_url = os.environ["DATABASE_URL"]
    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[len("postgres://"):]
    try:
        DB_POOL = await asyncpg.create_pool(db_url)
    except Exception:
        DB_POOL = await asyncpg.create_pool(db_url, ssl='require')
    async with DB_POOL.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id BIGINT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS menu_dishes (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                dish_name TEXT NOT NULL,
                calories INT DEFAULT 0,
                protein FLOAT DEFAULT 0,
                fat FLOAT DEFAULT 0,
                carbs FLOAT DEFAULT 0,
                created_date DATE NOT NULL DEFAULT CURRENT_DATE
            );
            CREATE TABLE IF NOT EXISTS food_log (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                dish_name TEXT NOT NULL,
                calories INT DEFAULT 0,
                protein FLOAT DEFAULT 0,
                fat FLOAT DEFAULT 0,
                carbs FLOAT DEFAULT 0,
                logged_at TIMESTAMP DEFAULT NOW()
            );
        """)
    logger.info("DB initialized")

async def register_user(chat_id: int):
    if not DB_POOL:
        return
    async with DB_POOL.acquire() as conn:
        await conn.execute("INSERT INTO users (chat_id) VALUES ($1) ON CONFLICT DO NOTHING", chat_id)

async def extract_and_save_dishes(chat_id: int, menu_text: str) -> list:
    """Парсит блюда regex-ом из текста меню, сохраняет в БД (без доп. запроса к Gemini)."""
    if not DB_POOL:
        return []
    skip = {"Ингредиенты", "Приготовление", "Докупить"}
    matches = re.findall(r'<b>([^<]+)</b>\s*\(~?(\d+)\s*ккал\)', menu_text)
    dishes_raw = [
        {"dish_name": name.strip().rstrip(":"), "calories": int(cal),
         "protein": 0.0, "fat": 0.0, "carbs": 0.0}
        for name, cal in matches
        if name.strip().rstrip(":") not in skip and len(name.strip()) > 3
    ]
    if not dishes_raw:
        logger.warning(f"extract_and_save_dishes: no dishes found via regex for {chat_id}")
        return []
    today = date.today()
    saved = []
    try:
        async with DB_POOL.acquire() as conn:
            for d in dishes_raw:
                row_id = await conn.fetchval(
                    "INSERT INTO menu_dishes (chat_id, dish_name, calories, protein, fat, carbs, created_date) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                    chat_id, d["dish_name"], d["calories"],
                    d["protein"], d["fat"], d["carbs"], today
                )
                saved.append({"id": row_id, "dish_name": d["dish_name"],
                              "calories": d["calories"], "protein": 0.0,
                              "fat": 0.0, "carbs": 0.0})
    except Exception as e:
        logger.warning(f"DB save dishes failed: {e}")
    logger.info(f"Saved {len(saved)} dishes for {chat_id}")
    return saved

async def get_today_dishes(chat_id: int) -> list:
    if not DB_POOL:
        return []
    today = date.today()
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT ON (dish_name) id, dish_name, calories, protein, fat, carbs "
            "FROM menu_dishes WHERE chat_id=$1 AND created_date=$2 "
            "ORDER BY dish_name, id LIMIT 10",
            chat_id, today
        )
    return [dict(r) for r in rows]

async def log_food(chat_id: int, dishes: list):
    if not DB_POOL:
        return
    async with DB_POOL.acquire() as conn:
        for d in dishes:
            await conn.execute(
                "INSERT INTO food_log (chat_id, dish_name, calories, protein, fat, carbs) VALUES ($1,$2,$3,$4,$5,$6)",
                chat_id, d["dish_name"], int(d.get("calories", 0)),
                float(d.get("protein", 0)), float(d.get("fat", 0)), float(d.get("carbs", 0))
            )

async def get_today_log(chat_id: int) -> list:
    if not DB_POOL:
        return []
    today = date.today()
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            "SELECT dish_name, calories, protein, fat, carbs FROM food_log "
            "WHERE chat_id=$1 AND logged_at::date=$2 ORDER BY logged_at",
            chat_id, today
        )
    return [dict(r) for r in rows]

# --- Gemini ---

async def ask_gemini(prompt: str) -> str:
    full = f"{SYSTEM_PROMPT}\n\n{prompt}"
    for model in [GEMINI_MODEL, FALLBACK_MODEL, FALLBACK_MODEL_2]:
        try:
            response = await client.aio.models.generate_content(model=model, contents=full)
            return response.text
        except Exception as e:
            logger.warning(f"Model {model} failed ({type(e).__name__}): {e}")
    return "Ошибка при обращении к AI. Попробуй через минуту."

def _parse_json_response(text: str) -> AnalysisResult | None:
    try:
        text = text.strip()
        if "```" in text:
            for part in text.split("```"):
                part = part.strip().lstrip("json").strip()
                try:
                    return AnalysisResult.model_validate_json(part)
                except Exception:
                    continue
        return AnalysisResult.model_validate_json(text)
    except Exception as e:
        logger.warning(f"JSON parse error: {e}\nText: {text[:300]}")
        return None

async def recognize_products_structured(text_products: list, image_list: list) -> AnalysisResult | None:
    JSON_SCHEMA = ('{"products":[{"name":"название","amount":"количество",'
                   '"category":"белки|жиры|углеводы|овощи-зелень|молочное|прочее",'
                   '"calories_per_100g":0,"protein_per_100g":0.0,"fat_per_100g":0.0,"carbs_per_100g":0.0}]}')
    try:
        parts = []
        for img_bytes in image_list[:5]:
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))
        prompt = "Определи продукты питания"
        if image_list:
            prompt += " на фото"
        if text_products:
            prompt += f". Текстом: {'; '.join(text_products)}"
        prompt += f". Верни ТОЛЬКО валидный JSON без markdown:\n{JSON_SCHEMA}"
        parts.append(types.Part.from_text(text=prompt))
        for model in [GEMINI_MODEL, FALLBACK_MODEL, FALLBACK_MODEL_2]:
            try:
                response = await client.aio.models.generate_content(model=model, contents=parts)
                result = _parse_json_response(response.text)
                if result and result.products:
                    return result
            except Exception as e:
                logger.warning(f"Recognition {model} failed: {e}")
        return None
    except Exception as e:
        logger.warning(f"recognize_products_structured error: {e}")
        return None

def format_ingredients_display(result: AnalysisResult) -> str:
    return "\n".join(f"• {p.name}" for p in result.products)

def format_ingredients_for_menu(result: AnalysisResult) -> str:
    return "\n".join(
        f"- {p.name} ({p.amount}): {p.calories_per_100g} ккал/100г, "
        f"Б{p.protein_per_100g}г Ж{p.fat_per_100g}г У{p.carbs_per_100g}г"
        for p in result.products
    )

# --- Keyboards ---

def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("Меню из продуктов"), KeyboardButton("Напоминания 💧")],
        [KeyboardButton("Идеи для перекуса"), KeyboardButton("Мой профиль")],
        [KeyboardButton("📝 Записать приём пищи")],
    ], resize_keyboard=True)

def add_or_compose_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("Добавить продукты"), KeyboardButton("Составить меню")],
        [KeyboardButton("Отмена")],
    ], resize_keyboard=True)

def meal_type_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("На весь день"), KeyboardButton("Завтрак")],
        [KeyboardButton("Обед"), KeyboardButton("Ужин"), KeyboardButton("Перекус")],
        [KeyboardButton("Отмена")],
    ], resize_keyboard=True)

def snack_more_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Предложить ещё", callback_data="snack_more")]])

def build_eat_keyboard(dishes: list, eaten_ids: set) -> InlineKeyboardMarkup:
    buttons = []
    for d in dishes:
        if d["id"] in eaten_ids:
            text = f"✅ {d['dish_name']}"
        else:
            text = f"◻️ Съел  —  {d['dish_name']} (~{d.get('calories', 0)} ккал)"
        buttons.append([InlineKeyboardButton(text, callback_data=f"eat:{d['id']}")])
    return InlineKeyboardMarkup(buttons)

def build_snack_keyboard(dishes: list, eaten_ids: set) -> InlineKeyboardMarkup:
    buttons = []
    for d in dishes:
        if d["id"] in eaten_ids:
            text = f"✅ {d['dish_name']}"
        else:
            text = f"◻️ Съел  —  {d['dish_name']} (~{d.get('calories', 0)} ккал)"
        buttons.append([InlineKeyboardButton(text, callback_data=f"eat:{d['id']}")])
    buttons.append([InlineKeyboardButton("🔄 Предложить ещё", callback_data="snack_more")])
    return InlineKeyboardMarkup(buttons)

def build_food_keyboard(dishes: list, selected_ids: set) -> InlineKeyboardMarkup:
    buttons = []
    for d in dishes:
        mark = "✅ " if d["id"] in selected_ids else "◻️ "
        buttons.append([InlineKeyboardButton(f"{mark}{d['dish_name'][:32]}", callback_data=f"fd_t:{d['id']}")])
    bottom = [InlineKeyboardButton("✏️ Написать своё", callback_data="fd_custom")]
    if selected_ids:
        bottom.append(InlineKeyboardButton(f"💾 Записать ({len(selected_ids)})", callback_data="fd_confirm"))
    buttons.append(bottom)
    return InlineKeyboardMarkup(buttons)

# --- Jobs ---

async def water_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    msg = context.job.data
    for chat_id in list(water_enabled):
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='HTML')
        except Exception as e:
            logger.warning(f"Water reminder failed for {chat_id}: {e}")

async def food_log_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    if not DB_POOL:
        return
    today = date.today()
    async with DB_POOL.acquire() as conn:
        all_users = await conn.fetch("SELECT chat_id FROM users")
        logged_today = await conn.fetch(
            "SELECT DISTINCT chat_id FROM food_log WHERE logged_at::date=$1", today
        )
    logged_ids = {r["chat_id"] for r in logged_today}
    for row in all_users:
        chat_id = row["chat_id"]
        if chat_id not in logged_ids:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="📝 <b>Не записал питание сегодня!</b>\n\nНажми «📝 Записать приём пищи».",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.warning(f"Food reminder failed for {chat_id}: {e}")

# --- Callbacks ---

async def eat_dish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    dish_id = int(query.data.split(":")[1])
    msg_id = query.message.message_id
    chat_id = update.effective_chat.id

    dishes = context.user_data.get(f"eat_dishes_{msg_id}")
    if not dishes:
        await query.answer("Сессия устарела. Воспользуйся кнопкой «📝 Записать приём пищи»", show_alert=True)
        return

    eaten = context.user_data.get(f"eat_eaten_{msg_id}", set())
    if dish_id in eaten:
        await query.answer("Уже записано ✅")
        return

    dish = next((d for d in dishes if d["id"] == dish_id), None)
    if not dish:
        await query.answer("Блюдо не найдено")
        return

    await log_food(chat_id, [dish])
    eaten.add(dish_id)
    context.user_data[f"eat_eaten_{msg_id}"] = eaten
    await query.answer("✅ Записано!")
    await query.edit_message_reply_markup(reply_markup=build_eat_keyboard(dishes, eaten))

async def snack_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    shown = context.user_data.get("shown_snacks", [])
    avoid_hint = [f"перекусы из блока {i+1}" for i in range(len(shown))] if shown else []
    await query.message.reply_text("Ищу другие варианты...")
    response = await _generate_snacks(avoid=avoid_hint if avoid_hint else None)
    shown.append(response)
    context.user_data["shown_snacks"] = shown
    await query.message.reply_text(f"<b>Ещё варианты</b>\n\n{response}", parse_mode='HTML')
    dishes = await extract_and_save_dishes(chat_id, response)
    if dishes:
        msg = await query.message.reply_text("Отметь что съел 👇", reply_markup=build_snack_keyboard(dishes, set()))
        context.user_data[f"eat_dishes_{msg.message_id}"] = dishes
        context.user_data[f"eat_eaten_{msg.message_id}"] = set()
    else:
        await query.message.reply_text("🔄", reply_markup=snack_more_keyboard())

async def food_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    dishes = context.user_data.get("food_dishes")
    if not dishes:
        await query.answer("Начни заново — нажми «📝 Записать приём пищи»", show_alert=True)
        return
    await query.answer()
    dish_id = int(query.data.split(":")[1])
    selected = context.user_data.get("food_selected", set())
    selected.discard(dish_id) if dish_id in selected else selected.add(dish_id)
    context.user_data["food_selected"] = selected
    await query.edit_message_reply_markup(reply_markup=build_food_keyboard(list(dishes.values()), selected))

async def food_custom_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["menu_state"] = "waiting_custom_food"
    await query.message.reply_text(
        "Напиши что ты ел (через запятую или с новой строки):",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
    )

async def food_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    dishes = context.user_data.get("food_dishes")
    if not dishes:
        await query.answer("Начни заново — нажми «📝 Записать приём пищи»", show_alert=True)
        return
    selected_ids = context.user_data.get("food_selected", set())
    custom = context.user_data.get("food_custom", [])
    to_log = [dishes[did] for did in selected_ids if did in dishes] + custom
    if not to_log:
        await query.answer("Выбери хотя бы одно блюдо", show_alert=True)
        return
    await query.answer()
    await log_food(update.effective_chat.id, to_log)
    total_cal = sum(int(d.get("calories", 0)) for d in to_log)
    total_prot = sum(float(d.get("protein", 0)) for d in to_log)
    names = "\n".join(f"• {d['dish_name']}" for d in to_log)
    context.user_data.pop("food_selected", None)
    context.user_data.pop("food_dishes", None)
    context.user_data.pop("food_custom", None)
    await query.edit_message_text(
        f"<b>Записано:</b>\n{names}\n\n"
        f"<b>Итого за день:</b> ~{total_cal} ккал · белок ~{round(total_prot)}г\n"
        f"<i>Цель: 1800-2000 ккал · 120-140г белка</i>",
        parse_mode='HTML'
    )

# --- Snacks ---

async def _generate_snacks(avoid: list = None) -> str:
    avoid_part = f" Не повторяй: {', '.join(avoid)}." if avoid else ""
    return await ask_gemini(
        f"3 ПЕРЕКУСА (не полноценных блюда!) для похудения при сидячей работе.{avoid_part}\n"
        "Требования: до 200 ккал, до 3-4 ингредиентов, готовность за 2-3 мин или без готовки.\n"
        "Правильные примеры: творог с ягодами, яйца вкрутую, греческий йогурт с орехами, овощи с хумусом.\n"
        "НЕЛЬЗЯ: курица с рисом, омлет с овощами и другие полноценные блюда.\n"
        "Для каждого: <b>название</b> (~ккал)\n• ингредиент — количество\n"
        "<i>Почему подходит:</i> одно предложение."
    )

async def snack_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("menu_state", None)
    context.user_data.pop("shown_snacks", None)
    await update.message.reply_text("Подбираю перекусы...")
    response = await _generate_snacks()
    context.user_data["shown_snacks"] = [response]
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"<b>Идеи для перекуса</b>\n\n{response}", parse_mode='HTML')
    dishes = await extract_and_save_dishes(chat_id, response)
    if dishes:
        msg = await update.message.reply_text("Отметь что съел 👇", reply_markup=build_snack_keyboard(dishes, set()))
        context.user_data[f"eat_dishes_{msg.message_id}"] = dishes
        context.user_data[f"eat_eaten_{msg.message_id}"] = set()
    else:
        await update.message.reply_text("🔄", reply_markup=snack_more_keyboard())

# --- Main handlers ---

MEAL_PROMPTS = {
    "На весь день": (
        "Составь план на день: завтрак (~400 ккал), обед (~550 ккал), ужин (~380 ккал), перекус (~180 ккал). "
        "Используй шаблон. В конце: <b>Докупить</b> — 5-7 позиций через •"
    ),
    "Завтрак": "Составь 2 варианта завтрака (~400 ккал). Используй шаблон.",
    "Обед": "Составь 2 варианта обеда (~550 ккал). Используй шаблон.",
    "Ужин": "Составь 2 варианта ужина (~380 ккал, лёгкие). Используй шаблон.",
    "Перекус": "Предложи 3 варианта перекуса (~180 ккал, высокий белок). Используй шаблон (без шагов).",
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    chat_id = update.effective_chat.id
    await register_user(chat_id)
    name = update.effective_user.first_name or "друг"
    status = "💧 включены" if chat_id in water_enabled else "выключены"
    await update.message.reply_text(
        f"Привет, {name}! Я Нутри — твой нутрициолог.\n"
        f"Цель: похудение. Напоминания о воде: {status}\n\nВыбирай:",
        reply_markup=main_keyboard(),
    )

async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    chat_id = update.effective_chat.id
    status = "✅ включены" if chat_id in water_enabled else "❌ выключены"
    today_log = await get_today_log(chat_id)
    if today_log:
        total_cal = sum(int(d.get("calories", 0)) for d in today_log)
        total_prot = sum(float(d.get("protein", 0)) for d in today_log)
        log_lines = "\n".join(f"• {d['dish_name']}" for d in today_log)
        food_section = f"\n\n<b>Сегодня записано:</b>\n{log_lines}\n~{total_cal} ккал · белок ~{round(total_prot)}г"
    else:
        food_section = "\n\n<i>Сегодня ещё ничего не записано.</i>"
    await update.message.reply_text(
        "<b>Твой профиль</b>\n\n"
        "Мужчина, 30-45 лет | Цель: похудение | Активность: сидячий\n\n"
        "<b>Рекомендации:</b>\n"
        "• Калории: ~1800-2000 ккал/день\n"
        "• Белок: ~120-140 г/день\n"
        "• Вода: ~2.0-2.2 л/день\n"
        "• Приёмов пищи: 3 основных + 1-2 перекуса\n\n"
        f"<b>Напоминания о воде:</b> {status}{food_section}",
        reply_markup=main_keyboard(), parse_mode='HTML',
    )

async def menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["menu_state"] = "waiting_products"
    context.user_data["text_products"] = []
    context.user_data["image_bytes_list"] = []
    await update.message.reply_text(
        "Напиши список продуктов или отправь фото.\nМожно добавить несколько фото и текст.",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
    )

async def show_add_or_compose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texts = context.user_data.get("text_products", [])
    photos = context.user_data.get("image_bytes_list", [])
    lines = [f"• {p}" for p in texts] + [f"📷 фото #{i+1}" for i in range(len(photos))]
    context.user_data["menu_state"] = "waiting_add_or_compose"
    await update.message.reply_text(
        f"<b>Добавлено:</b>\n{chr(10).join(lines) if lines else '• пусто'}\n\nЧто дальше?",
        reply_markup=add_or_compose_keyboard(), parse_mode='HTML',
    )

async def menu_got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("menu_state")
    if state not in ("waiting_products", "waiting_add_or_compose"):
        return
    photo_sizes = update.message.photo
    photo = photo_sizes[min(1, len(photo_sizes) - 1)]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())
    context.user_data.setdefault("image_bytes_list", []).append(image_bytes)
    context.user_data.setdefault("text_products", [])
    await show_add_or_compose(update, context)

async def food_log_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("menu_state", None)
    chat_id = update.effective_chat.id
    dishes = await get_today_dishes(chat_id)
    context.user_data["food_dishes"] = {d["id"]: d for d in dishes}
    context.user_data["food_selected"] = set()
    context.user_data["food_custom"] = []
    if not dishes:
        context.user_data["menu_state"] = "waiting_custom_food"
        await update.message.reply_text(
            "Сегодня я ещё не предлагал блюд.\n\nНапиши что ты ел сегодня:",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return
    await update.message.reply_text(
        "<b>Что ты ел сегодня?</b>\n\nВыбери из предложенных блюд:",
        reply_markup=build_food_keyboard(dishes, set()), parse_mode='HTML'
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    menu_state = context.user_data.get("menu_state")
    chat_id = update.effective_chat.id

    if user_text == "Отмена":
        context.user_data.clear()
        await update.message.reply_text("Отменено.", reply_markup=main_keyboard())
        return

    if menu_state == "waiting_custom_food":
        items = [item.strip() for line in user_text.replace(",", "\n").splitlines() for item in [line.strip()] if item]
        await update.message.reply_text("Оцениваю КБЖУ...")
        schema = '{"dishes":[{"dish_name":"название","calories":0,"protein":0.0,"fat":0.0,"carbs":0.0}]}'
        prompt = f"Оцени КБЖУ для блюд: {', '.join(items)}. Верни ТОЛЬКО валидный JSON без markdown:\n{schema}"
        dishes_data = []
        for model in [GEMINI_MODEL, FALLBACK_MODEL, FALLBACK_MODEL_2]:
            try:
                response = await client.aio.models.generate_content(model=model, contents=prompt)
                text = response.text.strip()
                if "```" in text:
                    text = text.split("```")[1].lstrip("json").strip()
                dishes_data = json.loads(text).get("dishes", [])
                if dishes_data:
                    break
            except Exception as e:
                logger.warning(f"Custom food KBZHU: {e}")
        if not dishes_data:
            dishes_data = [{"dish_name": i, "calories": 0, "protein": 0, "fat": 0, "carbs": 0} for i in items]

        food_dishes = context.user_data.get("food_dishes")
        if food_dishes is not None and "food_selected" in context.user_data:
            context.user_data.setdefault("food_custom", []).extend(dishes_data)
            context.user_data.pop("menu_state", None)
            selected = context.user_data.get("food_selected", set())
            added = ", ".join(d["dish_name"] for d in dishes_data)
            await update.message.reply_text(
                f"Добавлено: {added}\n\nВыбери ещё или нажми «Записать»:",
                reply_markup=build_food_keyboard(list(food_dishes.values()), selected)
            )
        else:
            await log_food(chat_id, dishes_data)
            total_cal = sum(int(d.get("calories", 0)) for d in dishes_data)
            total_prot = sum(float(d.get("protein", 0)) for d in dishes_data)
            names = "\n".join(f"• {d['dish_name']}" for d in dishes_data)
            context.user_data.pop("menu_state", None)
            await update.message.reply_text(
                f"<b>Записано:</b>\n{names}\n\n"
                f"<b>Итого за день:</b> ~{total_cal} ккал · белок ~{round(total_prot)}г",
                reply_markup=main_keyboard(), parse_mode='HTML'
            )
        return

    if menu_state == "waiting_products":
        new_items = [item.strip() for line in user_text.splitlines() for item in line.split(',') if item.strip()]
        context.user_data.setdefault("text_products", []).extend(new_items)
        context.user_data.setdefault("image_bytes_list", [])
        await show_add_or_compose(update, context)
        return

    if menu_state == "waiting_add_or_compose":
        if user_text == "Добавить продукты":
            context.user_data["menu_state"] = "waiting_products"
            await update.message.reply_text(
                "Напиши ещё продукты или отправь фото.",
                reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
            )
        elif user_text == "Составить меню":
            text_products = context.user_data.get("text_products", [])
            image_list = context.user_data.get("image_bytes_list", [])
            if image_list:
                await update.message.reply_text("Анализирую продукты...")
                result = await recognize_products_structured(text_products, image_list)
                if result and result.products:
                    display = format_ingredients_display(result)
                    context.user_data["recognized_ingredients"] = format_ingredients_for_menu(result)
                else:
                    display = "\n".join(f"• {p}" for p in text_products) if text_products else "• не указаны"
                    context.user_data["recognized_ingredients"] = display
            else:
                display = "\n".join(f"• {p}" for p in text_products) if text_products else "• не указаны"
                context.user_data["recognized_ingredients"] = ", ".join(text_products) if text_products else "не указаны"
            context.user_data["menu_state"] = "waiting_meal_type"
            await update.message.reply_text(
                f"<b>Продукты:</b>\n{display}\n\nЧто составить?",
                reply_markup=meal_type_keyboard(), parse_mode='HTML',
            )
        else:
            await update.message.reply_text("Выбери кнопку.", reply_markup=add_or_compose_keyboard())
        return

    if menu_state == "waiting_meal_type":
        if user_text not in MEAL_PROMPTS:
            await update.message.reply_text("Выбери один из вариантов.", reply_markup=meal_type_keyboard())
            return
        recognized = context.user_data.get("recognized_ingredients", "не указаны")
        meal_prompt = MEAL_PROMPTS[user_text]
        context.user_data.clear()
        await update.message.reply_text(f"Составляю: {user_text.lower()}...")
        response = await ask_gemini(
            f"Имеющиеся продукты (КБЖУ):\n{recognized}\n\n"
            f"Используй уместно, дополняй другими. В конце — что докупить.\n\n{meal_prompt}"
        )
        dishes = await extract_and_save_dishes(chat_id, response)
        await update.message.reply_text(
            f"<b>{user_text}</b>\n\n{response}",
            reply_markup=main_keyboard(), parse_mode='HTML',
        )
        if dishes:
            msg = await update.message.reply_text(
                "Отметь что съел 👇",
                reply_markup=build_eat_keyboard(dishes, set())
            )
            context.user_data[f"eat_dishes_{msg.message_id}"] = dishes
            context.user_data[f"eat_eaten_{msg.message_id}"] = set()
        return

    if user_text == "Меню из продуктов":
        await menu_start(update, context)
    elif user_text in ("Напоминания 💧", "Водный баланс"):
        await toggle_water(update, context)
    elif user_text == "Идеи для перекуса":
        await snack_ideas(update, context)
    elif user_text == "Мой профиль":
        await my_profile(update, context)
    elif user_text == "📝 Записать приём пищи":
        await food_log_start(update, context)
    else:
        await update.message.reply_text("Думаю...")
        response = await ask_gemini(
            f"Пользователь: '{user_text}'. Ответь как нутрициолог. Если не о питании — мягко верни к теме."
        )
        await update.message.reply_text(response, reply_markup=main_keyboard(), parse_mode='HTML')

async def toggle_water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("menu_state", None)
    chat_id = update.effective_chat.id
    if chat_id in water_enabled:
        water_enabled.discard(chat_id)
        await update.message.reply_text(
            "Напоминания о воде <b>отключены</b>.\n\nНажми «Напоминания 💧» снова, чтобы включить.",
            reply_markup=main_keyboard(), parse_mode='HTML',
        )
    else:
        water_enabled.add(chat_id)
        await update.message.reply_text(
            "💧 <b>Напоминания включены!</b>\n\n"
            "Буду напоминать 6 раз в день:\n09:00 · 11:00 · 13:00 · 15:00 · 18:00 · 21:00\n\n"
            "<b>Цель:</b> 2.2 л в день\n\n<i>Нажми ещё раз, чтобы отключить.</i>",
            reply_markup=main_keyboard(), parse_mode='HTML',
        )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(init_db).build()

    for t, msg in WATER_SCHEDULE:
        app.job_queue.run_daily(water_reminder_job, time=t, data=msg)
    app.job_queue.run_daily(food_log_reminder_job, time=dtime(21, 0, tzinfo=MSK))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profil", my_profile))
    app.add_handler(CommandHandler("voda", toggle_water))
    app.add_handler(CommandHandler("log", food_log_start))
    app.add_handler(CallbackQueryHandler(eat_dish_callback, pattern="^eat:"))
    app.add_handler(CallbackQueryHandler(snack_more_callback, pattern="^snack_more$"))
    app.add_handler(CallbackQueryHandler(food_toggle_callback, pattern="^fd_t:"))
    app.add_handler(CallbackQueryHandler(food_custom_callback, pattern="^fd_custom$"))
    app.add_handler(CallbackQueryHandler(food_confirm_callback, pattern="^fd_confirm$"))
    app.add_handler(MessageHandler(filters.PHOTO, menu_got_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
