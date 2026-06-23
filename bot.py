import logging
import os
import random
import json
from datetime import datetime
from pydantic import BaseModel
from google import genai
from google.genai import types
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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

# --- Pydantic-схема ---

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

CATEGORY_EMOJI = {
    "белки": "🥩",
    "жиры": "🥑",
    "углеводы": "🌾",
    "овощи-зелень": "🥦",
    "молочное": "🥛",
    "прочее": "🍽",
}

SYSTEM_PROMPT = """Ты персональный нутрициолог и диетолог по имени Нутри.
Профиль пользователя:
- Пол: мужчина, возраст 30-45 лет
- Главная цель: похудение
- Образ жизни: сидячий (офис/удалёнка)
- Пищевых ограничений нет
- Суточная норма воды: 2.0-2.2 литра
- Рекомендуемый дефицит калорий: 300-500 ккал от нормы

Принципы:
1. Никакого голодания - только разумный дефицит
2. Белок в каждом приёме пищи (минимум 25-30 г)
3. Медленные углеводы, минимум сахара
4. Советы практичные, без сложных рецептов
5. Отвечай на русском языке
6. Стиль: без лишних слов - только суть
7. Никаких вступлений, приветствий, вводных фраз
8. Сразу к сути

ФОРМАТИРОВАНИЕ — строго соблюдай этот шаблон для каждого блюда:

<b>Название блюда</b> (~NNN ккал)

<b>Ингредиенты</b>
• Ингредиент 1 — количество
• Ингредиент 2 — количество

<b>Приготовление</b>
1. <b>Шаг:</b> описание действия.
2. <b>Шаг:</b> описание действия.
3. <b>Шаг:</b> описание действия.

Между блюдами — пустая строка-разделитель.
Никаких символов * и **. Только HTML-теги <b> и <i>.
Для секции Докупить используй: <b>Докупить</b> и список через •"""


def ask_gemini(prompt: str) -> str:
    full = f"{SYSTEM_PROMPT}\n\n{prompt}"
    for model in [GEMINI_MODEL, FALLBACK_MODEL, FALLBACK_MODEL_2]:
        try:
            response = client.models.generate_content(model=model, contents=full)
            return response.text
        except Exception as e:
            logger.warning(f"Model {model} failed ({type(e).__name__}): {e}")
    return "Ошибка при обращении к AI. Попробуй через минуту."


def _parse_json_response(text: str) -> AnalysisResult | None:
    """Парсит JSON из ответа Gemini, убирая markdown-обёртки."""
    try:
        text = text.strip()
        # убрать ```json ... ``` если есть
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                try:
                    return AnalysisResult.model_validate_json(part)
                except Exception:
                    continue
        return AnalysisResult.model_validate_json(text)
    except Exception as e:
        logger.warning(f"JSON parse error: {e}\nText was: {text[:300]}")
        return None


def recognize_products_structured(text_products: list, image_list: list) -> AnalysisResult | None:
    """Распознаёт продукты и КБЖУ. Использует Vision если есть фото."""
    JSON_SCHEMA = '''{
  "products": [
    {
      "name": "название продукта на русском",
      "amount": "примерное количество (200г / 3 шт / пол-пачки)",
      "category": "белки | жиры | углеводы | овощи-зелень | молочное | прочее",
      "calories_per_100g": 000,
      "protein_per_100g": 0.0,
      "fat_per_100g": 0.0,
      "carbs_per_100g": 0.0
    }
  ]
}'''

    try:
        parts = []
        for img_bytes in image_list[:5]:
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))

        if image_list:
            prompt = "Определи все продукты питания на фото."
        else:
            prompt = "Определи продукты питания."

        if text_products:
            prompt += f" Также добавлены текстом: {'; '.join(text_products)}."

        prompt += (
            f"\n\nДля каждого продукта укажи КБЖУ на 100г и примерное количество."
            f"\nВерни ТОЛЬКО валидный JSON без markdown и без пояснений:\n{JSON_SCHEMA}"
        )

        parts.append(types.Part.from_text(text=prompt))

        for model in [GEMINI_MODEL, FALLBACK_MODEL, FALLBACK_MODEL_2]:
            try:
                response = client.models.generate_content(model=model, contents=parts)
                logger.info(f"Raw recognition response ({model}): {response.text[:200]}")
                result = _parse_json_response(response.text)
                if result and result.products:
                    logger.info(f"Structured recognition OK: {len(result.products)} products")
                    return result
            except Exception as e:
                logger.warning(f"Recognition model {model} failed: {type(e).__name__}: {e}")

        return None
    except Exception as e:
        logger.warning(f"recognize_products_structured outer error: {e}")
        return None


def format_ingredients_display(result: AnalysisResult) -> str:
    lines = [f"• {p.name}" for p in result.products]
    return "\n".join(lines)


def format_ingredients_for_menu(result: AnalysisResult) -> str:
    lines = []
    for p in result.products:
        lines.append(
            f"- {p.name} ({p.amount}): {p.calories_per_100g} ккал/100г, "
            f"белки {p.protein_per_100g}г, жиры {p.fat_per_100g}г, углеводы {p.carbs_per_100g}г"
        )
    return "\n".join(lines)


def main_keyboard():
    keyboard = [
        [KeyboardButton("Совет на день"), KeyboardButton("Меню из продуктов")],
        [KeyboardButton("Водный баланс"), KeyboardButton("Лайфхак")],
        [KeyboardButton("Идеи для перекуса"), KeyboardButton("Мой профиль")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def add_or_compose_keyboard():
    keyboard = [
        [KeyboardButton("Добавить продукты"), KeyboardButton("Составить меню")],
        [KeyboardButton("Отмена")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def meal_type_keyboard():
    keyboard = [
        [KeyboardButton("На весь день"), KeyboardButton("Завтрак")],
        [KeyboardButton("Обед"), KeyboardButton("Ужин"), KeyboardButton("Перекус")],
        [KeyboardButton("Отмена")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


MEAL_PROMPTS = {
    "На весь день": (
        "Составь план питания на весь день: завтрак (~400 ккал), обед (~550 ккал), "
        "ужин (~380 ккал), перекус (~180 ккал).\n"
        "Для каждого блюда используй шаблон из системного промпта.\n"
        "В конце добавь секцию: <b>Докупить</b> — 5-7 позиций через •"
    ),
    "Завтрак": (
        "Составь 2 варианта завтрака (~400 ккал каждый). "
        "Для каждого используй шаблон из системного промпта."
    ),
    "Обед": (
        "Составь 2 варианта обеда (~550 ккал каждый). "
        "Для каждого используй шаблон из системного промпта."
    ),
    "Ужин": (
        "Составь 2 варианта ужина (~380 ккал каждый, лёгкие). "
        "Для каждого используй шаблон из системного промпта."
    ),
    "Перекус": (
        "Предложи 3 варианта перекуса (~180 ккал каждый, высокий белок). "
        "Для каждого используй шаблон из системного промпта (без шагов приготовления, только ингредиенты)."
    ),
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    name = update.effective_user.first_name or "друг"
    await update.message.reply_text(
        f"Привет, {name}!\n\nЯ Нутри — твой личный нутрициолог.\n"
        "Цель: похудение. Образ жизни: сидячий. Ограничений нет.\n\nВыбирай:",
        reply_markup=main_keyboard(),
    )


async def daily_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    day = datetime.now().strftime("%A, %d %B")
    await update.message.reply_text("Готовлю совет...")
    response = ask_gemini(
        f"Сегодня {day}. Один практичный совет по питанию для похудения. "
        f"Формат: <b>заголовок</b> + 2-3 предложения + мотивирующая фраза."
    )
    await update.message.reply_text(
        f"<b>Совет на {datetime.now().strftime('%d.%m')}</b>\n\n{response}",
        reply_markup=main_keyboard(), parse_mode='HTML',
    )


async def water_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    hour = datetime.now().hour
    await update.message.reply_text("Считаю водный баланс...")
    response = ask_gemini(
        f"Сейчас {hour}:00. Короткий совет по водному балансу. "
        f"Норма воды, как распределить по дню, 1-2 лайфхака."
    )
    await update.message.reply_text(
        f"<b>Водный баланс</b>\n\n{response}",
        reply_markup=main_keyboard(), parse_mode='HTML',
    )


async def lifehack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    topics = [
        "скорость поглощения пищи и насыщение",
        "правильные перекусы при сидячей работе",
        "как не переедать вечером",
        "замена вредных продуктов полезными аналогами",
        "питание для поддержания энергии в течение дня",
        "как читать состав продуктов в магазине",
        "белковые завтраки для похудения",
        "интервальное питание",
        "как уменьшить тягу к сладкому",
        "приготовление еды заранее (meal prep)",
    ]
    topic = random.choice(topics)
    await update.message.reply_text("Ищу лайфхак...")
    response = ask_gemini(
        f"Один лайфхак на тему: '{topic}'. "
        f"Формат: <b>название</b> + 2-3 предложения + как применить сегодня."
    )
    await update.message.reply_text(
        f"<b>Лайфхак дня</b>\n\n{response}",
        reply_markup=main_keyboard(), parse_mode='HTML',
    )


async def snack_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Подбираю перекусы...")
    response = ask_gemini(
        "3 перекуса для похудения при сидячей работе. "
        "До 5 минут готовки, высокий белок, до 200 ккал. "
        "Для каждого: <b>название</b> (~ккал), ингредиенты через •, почему подходит."
    )
    await update.message.reply_text(
        f"<b>Идеи для перекуса</b>\n\n{response}",
        reply_markup=main_keyboard(), parse_mode='HTML',
    )


async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "<b>Твой профиль</b>\n\n"
        "Мужчина, 30-45 лет | Цель: похудение | Активность: сидячий\n\n"
        "<b>Рекомендации:</b>\n"
        "• Калории: ~1800-2000 ккал/день\n"
        "• Белок: ~120-140 г/день\n"
        "• Вода: ~2.0-2.2 л/день\n"
        "• Приёмов пищи: 3 основных + 1-2 перекуса",
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
    product_list = "\n".join(lines) if lines else "• пусто"
    context.user_data["menu_state"] = "waiting_add_or_compose"
    await update.message.reply_text(
        f"<b>Добавлено:</b>\n{product_list}\n\nЧто дальше?",
        reply_markup=add_or_compose_keyboard(), parse_mode='HTML',
    )


async def menu_got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("menu_state")
    if state not in ("waiting_products", "waiting_add_or_compose"):
        return
    # Берём среднее по размеру фото (индекс 1 если есть, иначе последнее)
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

            result = recognize_products_structured(text_products, image_list)

            if result and result.products:
                display = format_ingredients_display(result)
                menu_str = format_ingredients_for_menu(result)
                context.user_data["recognized_ingredients"] = menu_str
                context.user_data["menu_state"] = "waiting_meal_type"
                await update.message.reply_text(
                    f"<b>Твои ингредиенты:</b>\n\n{display}\n\nЧто составить?",
                    reply_markup=meal_type_keyboard(), parse_mode='HTML',
                )
            else:
                fallback = "\n".join(f"• {p}" for p in text_products) if text_products else "• продукты не указаны"
                context.user_data["recognized_ingredients"] = fallback
                context.user_data["menu_state"] = "waiting_meal_type"
                await update.message.reply_text(
                    f"<b>Продукты:</b>\n{fallback}\n\nЧто составить?",
                    reply_markup=meal_type_keyboard(), parse_mode='HTML',
                )
        else:
            await update.message.reply_text("Выбери кнопку.", reply_markup=add_or_compose_keyboard())
        return

    if menu_state == "waiting_meal_type":
        if user_text not in MEAL_PROMPTS:
            await update.message.reply_text("Выбери один из вариантов.", reply_markup=meal_type_keyboard())
            return
        recognized = context.user_data.get("recognized_ingredients", "продукты не указаны")
        meal_prompt = MEAL_PROMPTS[user_text]
        context.user_data.clear()
        await update.message.reply_text(f"Составляю: {user_text.lower()}...")
        response = ask_gemini(
            f"Имеющиеся продукты (с КБЖУ):\n{recognized}\n\n"
            f"Правила:\n"
            f"- Используй имеющиеся продукты там, где они уместны — не обязательно все и не обязательно каждый в отдельное блюдо\n"
            f"- Дополняй блюда другими ингредиентами для полноценного питания\n"
            f"- В конце каждого варианта меню добавь список того, что нужно докупить\n"
            f"- Используй точные данные КБЖУ для расчёта калорийности\n\n{meal_prompt}"
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
    elif user_text == "Водный баланс":
        await water_tip(update, context)
    elif user_text == "Лайфхак":
        await lifehack(update, context)
    elif user_text == "Идеи для перекуса":
        await snack_ideas(update, context)
    elif user_text == "Мой профиль":
        await my_profile(update, context)
    else:
        await update.message.reply_text("Думаю...")
        response = ask_gemini(
            f"Пользователь пишет: '{user_text}'\n\n"
            f"Ответь как нутрициолог. Если вопрос не о питании — мягко верни к теме."
        )
        await update.message.reply_text(response, reply_markup=main_keyboard(), parse_mode='HTML')


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sovet", daily_tip))
    app.add_handler(CommandHandler("voda", water_tip))
    app.add_handler(CommandHandler("laifhak", lifehack))
    app.add_handler(CommandHandler("perekus", snack_ideas))
    app.add_handler(CommandHandler("profil", my_profile))
    app.add_handler(MessageHandler(filters.PHOTO, menu_got_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
