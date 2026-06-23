import logging
import os
import random
from datetime import time as dtime, timezone, timedelta
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-3.5-flash"
FALLBACK_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL_2 = "gemini-2.0-flash"

water_enabled: set[int] = set()

MSK = timezone(timedelta(hours=3))

WATER_SCHEDULE = [
    (dtime(9, 0, tzinfo=MSK),
     "💧 <b>Вода</b>\nНачни день со стакана воды (250 мл). <b>Цель: 2.2 л</b> (~6 стаканов сейчас + остаток вечером)."),
    (dtime(11, 0, tzinfo=MSK),
     "💧 <b>Вода</b>\nЕщё стакан. К обеду должно быть 2-3 из 6."),
    (dtime(13, 0, tzinfo=MSK),
     "🍽 <b>Перед обедом</b>\nВыпей стакан за 30 мин до еды — снизит аппетит."),
    (dtime(15, 0, tzinfo=MSK),
     "💧 <b>Вода</b>\nПослеобеденный стакан. Не путай жажду с голодом."),
    (dtime(18, 0, tzinfo=MSK),
     "💧 <b>Вода</b>\nЕщё стакан! Уже должно быть 5 из 6."),
    (dtime(21, 0, tzinfo=MSK),
     "🌙 <b>Итог дня</b>\nВыпил ~2 л сегодня? Отличная работа!\nЗавтра продолжай."),
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
                   '"calories_per_100g":0,"protein_per_100g":0.0,'
                   '"fat_per_100g":0.0,"carbs_per_100g":0.0}]}')
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
                logger.info(f"Recognition raw ({model}): {response.text[:150]}")
                result = _parse_json_response(response.text)
                if result and result.products:
                    return result
            except Exception as e:
                logger.warning(f"Recognition model {model} failed: {type(e).__name__}: {e}")
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
        [KeyboardButton("Совет на день"), KeyboardButton("Меню из продуктов")],
        [KeyboardButton("Напоминания 💧"), KeyboardButton("Лайфхак")],
        [KeyboardButton("Идеи для перекуса"), KeyboardButton("Мой профиль")],
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
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Предложить ещё", callback_data="snack_more")
    ]])


# --- Water reminders ---

async def water_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    msg = context.job.data
    for chat_id in list(water_enabled):
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='HTML')
        except Exception as e:
            logger.warning(f"Water reminder failed for {chat_id}: {e}")


async def toggle_water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("menu_state", None)
    chat_id = update.effective_chat.id
    if chat_id in water_enabled:
        water_enabled.discard(chat_id)
        await update.message.reply_text(
            "Напоминания о воде <b>отключены</b>.\n\nНажми «Напоминания 💧» снова, чтобы включить.",
            reply_markup=main_keyboard(),
            parse_mode='HTML',
        )
    else:
        water_enabled.add(chat_id)
        await update.message.reply_text(
            "💧 <b>Напоминания включены!</b>\n\n"
            "Буду напоминать 6 раз в день:\n"
            "09:00 · 11:00 · 13:00 · 15:00 · 18:00 · 21:00\n\n"
            "<b>Цель:</b> 2.2 л в день (~9 стаканов по 250 мл)\n\n"
            "<i>Нажми ещё раз, чтобы отключить.</i>",
            reply_markup=main_keyboard(),
            parse_mode='HTML',
        )


# --- Snacks ---

async def _generate_snacks(avoid: list[str] = None) -> str:
    avoid_part = f" Не повторяй: {', '.join(avoid)}." if avoid else ""
    return await ask_gemini(
        f"3 перекуса для похудения при сидячей работе. До 5 мин готовки, высокий белок, до 200 ккал.{avoid_part}\n"
        "Для каждого: <b>название</b> (~ккал)\n• ингредиент — количество\n<i>Почему подходит:</i> одно предложение.\n"
        "Без раздела 'Докупить'."
    )


async def snack_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("menu_state", None)
    context.user_data.pop("shown_snacks", None)
    await update.message.reply_text("Подбираю перекусы...")
    response = await _generate_snacks()
    context.user_data["shown_snacks"] = [response]
    await update.message.reply_text(
        f"<b>Идеи для перекуса</b>\n\n{response}",
        reply_markup=snack_more_keyboard(),
        parse_mode='HTML',
    )


async def snack_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    shown = context.user_data.get("shown_snacks", [])
    # Собираем текст из предыдущих ответов как "avoid" подсказку
    avoid_hint = [f"перекусы из блока {i+1}" for i in range(len(shown))] if shown else []

    await query.message.reply_text("Ищу другие варианты...")
    response = await _generate_snacks(avoid=avoid_hint if avoid_hint else None)
    shown.append(response)
    context.user_data["shown_snacks"] = shown

    await query.message.reply_text(
        f"<b>Ещё варианты</b>\n\n{response}",
        reply_markup=snack_more_keyboard(),
        parse_mode='HTML',
    )


# --- Handlers ---

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
    name = update.effective_user.first_name or "друг"
    status = "💧 включены" if update.effective_chat.id in water_enabled else "выключены"
    await update.message.reply_text(
        f"Привет, {name}! Я Нутри — твой нутрициолог.\n"
        f"Цель: похудение. Напоминания о воде: {status}\n\nВыбирай:",
        reply_markup=main_keyboard(),
    )


async def daily_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Готовлю совет...")
    from datetime import datetime
    day = datetime.now(tz=MSK).strftime("%d.%m")
    response = await ask_gemini(
        f"Один практичный совет по питанию для похудения на сегодня ({day}). "
        f"Формат: <b>заголовок</b> + 2-3 предложения + мотивирующая фраза."
    )
    await update.message.reply_text(
        f"<b>Совет на {day}</b>\n\n{response}",
        reply_markup=main_keyboard(), parse_mode='HTML',
    )


async def lifehack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    topics = [
        "скорость поглощения пищи и насыщение",
        "перекусы при сидячей работе",
        "как не переедать вечером",
        "замена вредных продуктов полезными",
        "питание для энергии в течение дня",
        "как читать состав продуктов",
        "белковые завтраки для похудения",
        "интервальное питание",
        "как уменьшить тягу к сладкому",
        "meal prep — готовка еды заранее",
    ]
    await update.message.reply_text("Ищу лайфхак...")
    response = await ask_gemini(
        f"Лайфхак на тему: '{random.choice(topics)}'. "
        f"Формат: <b>название</b> + 2-3 предложения + как применить сегодня."
    )
    await update.message.reply_text(
        f"<b>Лайфхак дня</b>\n\n{response}",
        reply_markup=main_keyboard(), parse_mode='HTML',
    )


async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    status = "✅ включены" if update.effective_chat.id in water_enabled else "❌ выключены"
    await update.message.reply_text(
        "<b>Твой профиль</b>\n\n"
        "Мужчина, 30-45 лет | Цель: похудение | Активность: сидячий\n\n"
        "<b>Рекомендации:</b>\n"
        "• Калории: ~1800-2000 ккал/день\n"
        "• Белок: ~120-140 г/день\n"
        "• Вода: ~2.0-2.2 л/день\n"
        "• Приёмов пищи: 3 основных + 1-2 перекуса\n\n"
        f"<b>Напоминания о воде:</b> {status}",
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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    menu_state = context.user_data.get("menu_state")

    if user_text == "Отмена":
        context.user_data.clear()
        await update.message.reply_text("Отменено.", reply_markup=main_keyboard())
        return

    if menu_state == "waiting_products":
        new_items = [line.strip() for line in user_text.splitlines() if line.strip()]
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

            await update.message.reply_text("Анализирую продукты...")
            result = await recognize_products_structured(text_products, image_list)

            if result and result.products:
                display = format_ingredients_display(result)
                menu_str = format_ingredients_for_menu(result)
                context.user_data["recognized_ingredients"] = menu_str
            else:
                display = "\n".join(f"• {p}" for p in text_products) if text_products else "• не указаны"
                context.user_data["recognized_ingredients"] = display

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
        await update.message.reply_text(
            f"<b>{user_text}</b>\n\n{response}",
            reply_markup=main_keyboard(), parse_mode='HTML',
        )
        return

    if user_text == "Меню из продуктов":
        await menu_start(update, context)
    elif user_text == "Совет на день":
        await daily_tip(update, context)
    elif user_text in ("Напоминания 💧", "Водный баланс"):
        await toggle_water(update, context)
    elif user_text == "Лайфхак":
        await lifehack(update, context)
    elif user_text == "Идеи для перекуса":
        await snack_ideas(update, context)
    elif user_text == "Мой профиль":
        await my_profile(update, context)
    else:
        await update.message.reply_text("Думаю...")
        response = await ask_gemini(
            f"Пользователь: '{user_text}'. Ответь как нутрициолог. Если не о питании — мягко верни к теме."
        )
        await update.message.reply_text(response, reply_markup=main_keyboard(), parse_mode='HTML')


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    for t, msg in WATER_SCHEDULE:
        app.job_queue.run_daily(water_reminder_job, time=t, data=msg)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sovet", daily_tip))
    app.add_handler(CommandHandler("laifhak", lifehack))
    app.add_handler(CommandHandler("perekus", snack_ideas))
    app.add_handler(CommandHandler("profil", my_profile))
    app.add_handler(CommandHandler("voda", toggle_water))
    app.add_handler(CallbackQueryHandler(snack_more_callback, pattern="^snack_more$"))
    app.add_handler(MessageHandler(filters.PHOTO, menu_got_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
