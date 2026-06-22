import logging
import os
import random
from datetime import datetime
from google import genai
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

SYSTEM_PROMPT = """Ты персональный нутрициолог и диетолог по имени Нутри.
Профиль пользователя:
- Пол: мужчина, возраст 30-45 лет
- Главная цель: похудение
- Образ жизни: сидячий (офис/удалёнка)
- Пищевых ограничений нет
- Суточная норма воды: ~2.0-2.2 литра
- Рекомендуемый дефицит калорий: ~300-500 ккал от нормы

Твои принципы:
1. Никакого голодания - только разумный дефицит
2. Белок в каждом приёме пищи (минимум 25-30 г)
3. Медленные углеводы, минимум сахара
4. Советы практичные, без сложных рецептов
5. Отвечай на русском языке
6. Стиль: дружелюбный, без лишних слов - только суть
7. Используй эмодзи умеренно для читаемости
8. Ответы короткие и конкретные - никакой воды, никаких вступлений и приветствий
9. Никогда не начинай ответ с приветствия, обращения по имени или вводных фраз типа "Отлично!", "Конечно!", "Давай разберёмся"
10. Сразу переходи к сути"""


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
        return "Произошла ошибка при обращении к AI. Попробуй чуть позже."


def main_keyboard():
    keyboard = [
        [KeyboardButton("Совет на день"), KeyboardButton("Меню из продуктов")],
        [KeyboardButton("Водный баланс"), KeyboardButton("Лайфхак")],
        [KeyboardButton("Идеи для перекуса"), KeyboardButton("Мой профиль")],
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


async def menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Напиши список продуктов через запятую или каждый с новой строки.\n\n"
        "Например: яйца, куриная грудка, гречка, огурцы, творог"
    )
    return WAITING_PRODUCTS


async def menu_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = update.message.text
    prompt = (
        f"Продукты: {products}\n\n"
        f"Составь план питания на день:\n"
        f"ЗАВТРАК ~400-450 ккал\n"
        f"ОБЕД ~500-600 ккал\n"
        f"УЖИН ~350-400 ккал\n"
        f"ПЕРЕКУС ~150-200 ккал\n\n"
        f"Для каждого: название, ингредиенты с граммовкой, способ приготовления (2-3 шага), калорийность.\n"
        f"В конце блок 'ДОКУПИТЬ:' — 5-7 позиций для более полного рациона."
    )
    await update.message.reply_text("Составляю меню...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"Меню на день\n\n{response}")
    return ConversationHandler.END


async def menu_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=main_keyboard())
    return ConversationHandler.END


async def free_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text

    if user_text == "Совет на день":
        return await daily_tip(update, context)
    elif user_text == "Меню из продуктов":
        return await menu_start(update, context)
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
        entry_points=[CommandHandler("menu", menu_start)],
        states={WAITING_PRODUCTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_generate)]},
        fallbacks=[CommandHandler("cancel", menu_cancel)],
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
