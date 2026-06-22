import logging
import os
import random
from datetime import datetime
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
8. Сразу к сути"""


def ask_gemini(prompt: str) -> str:
    full = f"{SYSTEM_PROMPT}\n\n{prompt}"
    for model in [GEMINI_MODEL, FALLBACK_MODEL]:
        try:
            response = client.models.generate_content(model=model, contents=full)
            return response.text
        except Exception as e:
            logger.warning(f"Model {model} failed ({type(e).__name__}): {e}")
    return "Ошибка при обращении к AI. Попробуй через минуту."


def recognize_products(text_products: list, image_list: list) -> str:
    """Распознаёт продукты с фото и объединяет с текстовыми. Возвращает список строкой."""
    if not image_list:
        if text_products:
            return "\n".join(f"* {p}" for p in text_products)
        return "* продукты не указаны"

    try:
        parts = []
        for img_bytes in image_list[:2]:
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))

        prompt = "Определи все продукты питания на фото."
        if text_products:
            prompt += f" Также добавлены текстом: {'; '.join(text_products)}. Включи их тоже."
        prompt += " Верни только список — каждый продукт с новой строки со звёздочкой (* продукт). Без лишних слов."

        parts.append(types.Part.from_text(text=prompt))
        response = client.models.generate_content(model=GEMINI_MODEL, contents=parts)
        logger.info("Products recognized with vision")
        return response.text.strip()
    except Exception as e:
        logger.warning(f"Vision recognition failed ({type(e).__name__}): {e}")
        if text_products:
            return "\n".join(f"* {p}" for p in text_products)
        return "* продукты не распознаны"


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
        "Составь план питания на день: завтрак (~400 ккал), обед (~550 ккал), "
        "ужин (~380 ккал), перекус (~180 ккал).\n"
        "Для каждого: название, ингредиенты с граммовкой, способ приготовления (2-3 шага), калорийность.\n"
        "В конце: ДОКУПИТЬ — 5-7 позиций."
    ),
    "Завтрак": (
        "Составь 2 варианта завтрака (~400 ккал каждый). "
        "Для каждого: название, ингредиенты с граммовкой, способ приготовления (2-3 шага), калорийность."
    ),
    "Обед": (
        "Составь 2 варианта обеда (~550 ккал каждый). "
        "Для каждого: название, ингредиенты с граммовкой, способ приготовления (2-3 шага), калорийность."
    ),
    "Ужин": (
        "Составь 2 варианта ужина (~380 ккал каждый, лёгкие). "
        "Для каждого: название, ингредиенты с граммовкой, способ приготовления (2-3 шага), калорийность."
    ),
    "Перекус": (
        "Предложи 3 варианта перекуса (~180 ккал каждый, высокий белок). "
        "Для каждого: название, ингредиенты с граммовкой, калорийность."
    ),
}


# --- Стандартные команды ---

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
        f"Формат: заголовок + 2-3 предложения + мотивирующая фраза."
    )
    await update.message.reply_text(f"Совет на {datetime.now().strftime('%d.%m')}\n\n{response}", reply_markup=main_keyboard())


async def water_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    hour = datetime.now().hour
    await update.message.reply_text("Считаю водный баланс...")
    response = ask_gemini(
        f"Сейчас {hour}:00. Короткий совет по водному балансу. "
        f"Норма воды, как распределить по дню, 1-2 лайфхака."
    )
    await update.message.reply_text(f"Водный баланс\n\n{response}", reply_markup=main_keyboard())


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
        f"Формат: название + 2-3 предложения + как применить сегодня."
    )
    await update.message.reply_text(f"Лайфхак дня\n\n{response}", reply_markup=main_keyboard())


async def snack_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Подбираю перекусы...")
    response = ask_gemini(
        "3 перекуса для похудения при сидячей работе. "
        "До 5 минут готовки, высокий белок, до 200 ккал. "
        "Для каждого: название, калорийность, почему подходит."
    )
    await update.message.reply_text(f"Идеи для перекуса\n\n{response}", reply_markup=main_keyboard())


async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Твой профиль\n\n"
        "Мужчина, 30-45 лет | Цель: похудение | Активность: сидячий\n\n"
        "Рекомендации:\n"
        "Калории: ~1800-2000 ккал/день\n"
        "Белок: ~120-140 г/день\n"
        "Вода: ~2.0-2.2 л/день\n"
        "Приёмов пищи: 3 основных + 1-2 перекуса",
        reply_markup=main_keyboard(),
    )


# --- Меню из продуктов ---

async def menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["menu_state"] = "waiting_products"
    context.user_data["text_products"] = []
    context.user_data["image_bytes_list"] = []
    await update.message.reply_text(
        "Напиши список продуктов или отправь фото.\nМожно добавить несколько фото и текст.",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
    )


async def show_add_or_compose(update: Update, context: ContextTypes.DEFAULT_TYPE, label: str):
    texts = context.user_data.get("text_products", [])
    photos = context.user_data.get("image_bytes_list", [])
    parts = []
    if texts:
        parts.append(f"текст: {len(texts)} поз.")
    if photos:
        parts.append(f"фото: {len(photos)} шт.")
    summary = ", ".join(parts) if parts else "пусто"
    context.user_data["menu_state"] = "waiting_add_or_compose"
    await update.message.reply_text(
        f"Добавлено ({summary}). Последнее: {label}\n\nЧто дальше?",
        reply_markup=add_or_compose_keyboard(),
    )


async def menu_got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("menu_state")
    if state not in ("waiting_products", "waiting_add_or_compose"):
        return

    photo = update.message.photo[0]  # smallest size
    file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())
    context.user_data.setdefault("image_bytes_list", []).append(image_bytes)
    context.user_data.setdefault("text_products", [])

    n = len(context.user_data["image_bytes_list"])
    await show_add_or_compose(update, context, f"фото #{n}")


# --- Основной обработчик текста ---

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    menu_state = context.user_data.get("menu_state")

    if user_text == "Отмена":
        context.user_data.clear()
        await update.message.reply_text("Отменено.", reply_markup=main_keyboard())
        return

    if menu_state == "waiting_products":
        context.user_data.setdefault("text_products", []).append(user_text)
        context.user_data.setdefault("image_bytes_list", [])
        await show_add_or_compose(update, context, f'"{user_text[:40]}"')
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
            ingredients = recognize_products(text_products, image_list)
            context.user_data["recognized_ingredients"] = ingredients
            context.user_data["menu_state"] = "waiting_meal_type"

            await update.message.reply_text(
                f"Твои ингредиенты:\n{ingredients}\n\nЧто составить?",
                reply_markup=meal_type_keyboard(),
            )
        else:
            await update.message.reply_text("Выбери кнопку.", reply_markup=add_or_compose_keyboard())
        return

    if menu_state == "waiting_meal_type":
        if user_text not in MEAL_PROMPTS:
            await update.message.reply_text("Выбери один из вариантов.", reply_markup=meal_type_keyboard())
            return

        recognized = context.user_data.get("recognized_ingredients", "* продукты не указаны")
        meal_prompt = MEAL_PROMPTS[user_text]

        context.user_data.clear()
        await update.message.reply_text(f"Составляю: {user_text.lower()}...")

        response = ask_gemini(f"Продукты:\n{recognized}\n\n{meal_prompt}")
        await update.message.reply_text(f"{user_text}\n\n{response}", reply_markup=main_keyboard())
        return

    # Главное меню
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
        await update.message.reply_text(response, reply_markup=main_keyboard())


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
