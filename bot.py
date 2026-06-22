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
    ConversationHandler,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-2.5-flash"

WAITING_PRODUCTS = 1
WAITING_MEAL_TYPE = 2

SYSTEM_PROMPT = """Ты персональный нутрициолог и диетолог по имени Нутри.
Профиль пользователя:
- Пол: мужчина, возраст 30-45 лет
- Главная цель: похудение
- Образ жизни: сидячий (офис/удалёнка)
- Пищевых ограничений нет
- Суточная норма воды: 2.0-2.2 литра
- Рекомендуемый дефицит калорий: 300-500 ккал от нормы

Твои принципы:
1. Никакого голодания - только разумный дефицит
2. Белок в каждом приёме пищи (минимум 25-30 г)
3. Медленные углеводы, минимум сахара
4. Советы практичные, без сложных рецептов
5. Отвечай на русском языке
6. Стиль: дружелюбный, без лишних слов - только суть
7. Ответы короткие и конкретные - никакой воды, никаких вступлений и приветствий
8. Никогда не начинай ответ с приветствия или вводных фраз типа "Отлично!", "Конечно!"
9. Сразу переходи к сути"""


def ask_gemini(prompt: str) -> str:
    try:
        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=full_prompt,
        )
        return response.text
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return "Ошибка при обращении к AI. Попробуй чуть позже."


def ask_gemini_with_image(prompt: str, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    try:
        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                types.Part.from_text(text=full_prompt),
            ],
        )
        return response.text
    except Exception as e:
        logger.error(f"Gemini vision error: {e}")
        return "Ошибка при анализе фото. Попробуй чуть позже."


def main_keyboard():
    keyboard = [
        [KeyboardButton("Совет на день"), KeyboardButton("Меню из продуктов")],
        [KeyboardButton("Водный баланс"), KeyboardButton("Лайфхак")],
        [KeyboardButton("Идеи для перекуса"), KeyboardButton("Мой профиль")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def meal_type_keyboard():
    keyboard = [
        [KeyboardButton("На весь день"), KeyboardButton("Завтрак")],
        [KeyboardButton("Обед"), KeyboardButton("Ужин"), KeyboardButton("Перекус")],
        [KeyboardButton("Отмена")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "друг"
    text = (
        f"Привет, {name}!\n\n"
        "Я Нутри — твой личный нутрициолог в Telegram.\n\n"
        "Цель: похудение. Образ жизни: сидячий. Ограничений в питании нет.\n\n"
        "Выбирай кнопку или напиши вопрос:"
    )
    await update.message.reply_text(text, reply_markup=main_keyboard())


async def daily_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = datetime.now().strftime("%A, %d %B")
    prompt = (
        f"Сегодня {day}. Дай один практичный совет по питанию для похудения. "
        f"Формат: заголовок + 2-3 предложения + 1 мотивирующая фраза. Коротко."
    )
    await update.message.reply_text("Готовлю совет...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"Совет на {datetime.now().strftime('%d.%m')}\n\n{response}")


async def water_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hour = datetime.now().hour
    prompt = (
        f"Сейчас {hour}:00. Дай короткий совет по водному балансу с учётом времени суток. "
        f"Норма воды, как распределить по дню, 1-2 лайфхака. Коротко."
    )
    await update.message.reply_text("Считаю водный баланс...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"Водный баланс\n\n{response}")


async def lifehack(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    prompt = (
        f"Дай один лайфхак на тему: '{topic}'. "
        f"Формат: название + 2-3 предложения + как применить сегодня. Коротко."
    )
    await update.message.reply_text("Ищу лайфхак...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"Лайфхак дня\n\n{response}")


async def snack_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = (
        "Предложи 3 перекуса для похудения при сидячей работе. "
        "Требования: до 5 минут готовки, высокий белок, до 200 ккал. "
        "Для каждого: название, калорийность, почему подходит. Коротко."
    )
    await update.message.reply_text("Подбираю перекусы...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"Идеи для перекуса\n\n{response}")


async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Твой профиль\n\n"
        "Мужчина, 30-45 лет\n"
        "Цель: похудение\n"
        "Активность: сидячий образ жизни\n"
        "Ограничений: нет\n\n"
        "Рекомендации:\n"
        "Калории: ~1800-2000 ккал/день\n"
        "Белок: ~120-140 г/день\n"
        "Вода: ~2.0-2.2 л/день\n"
        "Приёмов пищи: 3 основных + 1-2 перекуса\n\n"
        "Хочешь изменить профиль? Напиши мне об этом."
    )
    await update.message.reply_text(text)


# --- Меню из продуктов (многошаговый диалог) ---

async def menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Напиши список продуктов или отправь фото холодильника/продуктов.",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
    )
    return WAITING_PRODUCTS


async def menu_got_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["products"] = update.message.text
    await update.message.reply_text("Что составить?", reply_markup=meal_type_keyboard())
    return WAITING_MEAL_TYPE


async def menu_got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Анализирую фото...")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()

    products_list = ask_gemini_with_image(
        "Перечисли все продукты питания, которые видишь на фото. "
        "Просто список через запятую, без лишних слов.",
        bytes(image_bytes),
    )
    context.user_data["products"] = products_list
    await update.message.reply_text(
        f"Вижу: {products_list}\n\nЧто составить?",
        reply_markup=meal_type_keyboard(),
    )
    return WAITING_MEAL_TYPE


async def menu_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    meal_type = update.message.text
    products = context.user_data.get("products", "")

    meal_prompts = {
        "На весь день": (
            f"Продукты: {products}\n\n"
            f"Составь план питания на день: завтрак (~400 ккал), обед (~550 ккал), "
            f"ужин (~380 ккал), перекус (~180 ккал).\n"
            f"Для каждого: название, ингредиенты с граммовкой, способ приготовления (2-3 шага), калорийность.\n"
            f"В конце: ДОКУПИТЬ — 5-7 позиций."
        ),
        "Завтрак": (
            f"Продукты: {products}\n\n"
            f"Составь 2 варианта завтрака (~400 ккал каждый). "
            f"Для каждого: название, ингредиенты с граммовкой, способ приготовления (2-3 шага), калорийность."
        ),
        "Обед": (
            f"Продукты: {products}\n\n"
            f"Составь 2 варианта обеда (~550 ккал каждый). "
            f"Для каждого: название, ингредиенты с граммовкой, способ приготовления (2-3 шага), калорийность."
        ),
        "Ужин": (
            f"Продукты: {products}\n\n"
            f"Составь 2 варианта ужина (~380 ккал каждый, лёгкие). "
            f"Для каждого: название, ингредиенты с граммовкой, способ приготовления (2-3 шага), калорийность."
        ),
        "Перекус": (
            f"Продукты: {products}\n\n"
            f"Предложи 3 варианта перекуса (~180 ккал каждый, высокий белок). "
            f"Для каждого: название, ингредиенты с граммовкой, калорийность."
        ),
    }

    prompt = meal_prompts.get(meal_type)
    if not prompt:
        await update.message.reply_text("Выбери один из вариантов.", reply_markup=meal_type_keyboard())
        return WAITING_MEAL_TYPE

    await update.message.reply_text(f"Составляю: {meal_type.lower()}...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"{meal_type}\n\n{response}", reply_markup=main_keyboard())
    return ConversationHandler.END


async def menu_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=main_keyboard())
    return ConversationHandler.END


# --- Свободный диалог ---

async def free_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text

    if user_text == "Совет на день":
        return await daily_tip(update, context)
    elif user_text == "Водный баланс":
        return await water_tip(update, context)
    elif user_text == "Лайфхак":
        return await lifehack(update, context)
    elif user_text == "Идеи для перекуса":
        return await snack_ideas(update, context)
    elif user_text == "Мой профиль":
        return await my_profile(update, context)

    prompt = (
        f"Пользователь пишет: '{user_text}'\n\n"
        f"Ответь как нутрициолог. Если вопрос не о питании или здоровье — "
        f"мягко верни к теме питания."
    )
    await update.message.reply_text("Думаю...")
    response = ask_gemini(prompt)
    await update.message.reply_text(response)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    menu_conv = ConversationHandler(
        entry_points=[
            CommandHandler("menu", menu_start),
            MessageHandler(filters.TEXT & filters.Regex("^Меню из продуктов$"), menu_start),
        ],
        states={
            WAITING_PRODUCTS: [
                MessageHandler(filters.PHOTO, menu_got_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^Отмена$"), menu_got_text),
            ],
            WAITING_MEAL_TYPE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^Отмена$"), menu_generate),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", menu_cancel),
            MessageHandler(filters.Regex("^Отмена$"), menu_cancel),
        ],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sovet", daily_tip))
    app.add_handler(CommandHandler("voda", water_tip))
    app.add_handler(CommandHandler("laifhak", lifehack))
    app.add_handler(CommandHandler("perekus", snack_ideas))
    app.add_handler(CommandHandler("profil", my_profile))
    app.add_handler(menu_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_chat))

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
