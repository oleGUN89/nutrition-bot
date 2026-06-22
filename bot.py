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

# ─── Логирование ────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-2.5-flash"

# ─── Состояния диалога ───────────────────────────────────────────────────────
WAITING_PRODUCTS = 1

# ─── System Prompt ───────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты персональный нутрициолог и диетолог по имени Нутри.
Профиль пользователя:
- Пол: мужчина, возраст 30–45 лет
- Главная цель: похудение
- Образ жизни: сидячий (офис/удалёнка)
- Пищевых ограничений нет
- Суточная норма воды: ~2.0–2.2 литра
- Рекомендуемый дефицит калорий: ~300–500 ккал от нормы

Твои принципы:
1. Никакого голодания — только разумный дефицит
2. Белок в каждом приёме пищи (минимум 25–30 г)
3. Медленные углеводы, минимум сахара
4. Советы практичные, без сложных рецептов
5. Отвечай на русском языке
6. Стиль: дружелюбный, мотивирующий, с лёгким юмором — как хороший друг-врач
7. Используй эмодзи умеренно для читаемости
8. Ответы структурированные, но не занудные"""


def ask_gemini(prompt: str) -> str:
    """Отправить запрос в Gemini и получить ответ."""
    try:
        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=full_prompt,
        )
        return response.text
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return "⚠️ Произошла ошибка при обращении к AI. Попробуй чуть позже."


# ─── Клавиатура ──────────────────────────────────────────────────────────────
def main_keyboard():
    keyboard = [
        [KeyboardButton("🌅 Совет на день"), KeyboardButton("🍽️ Меню из продуктов")],
        [KeyboardButton("💧 Водный баланс"), KeyboardButton("💡 Лайфхак")],
        [KeyboardButton("🥗 Идеи для перекуса"), KeyboardButton("📊 Мой профиль")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# ─── Обработчики команд ──────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "друг"
    text = (
        f"Привет, {name}! 👋\n\n"
        "Я *Нутри* — твой личный нутрициолог и диетолог в Telegram.\n\n"
        "Я знаю о тебе главное:\n"
        "• 🎯 Цель — похудение\n"
        "• 💼 Образ жизни — сидячий\n"
        "• 🚫 Ограничений в питании нет\n\n"
        "Вот что я умею:\n"
        "🌅 *Совет на день* — утренняя рекомендация по питанию\n"
        "🍽️ *Меню из продуктов* — напиши что есть в холодильнике, получи завтрак/обед/ужин\n"
        "💧 *Водный баланс* — как и когда пить воду\n"
        "💡 *Лайфхак* — практичный совет дня\n"
        "🥗 *Перекус* — что съесть между основными приёмами\n\n"
        "Выбирай кнопку или просто напиши мне вопрос! 👇"
    )
    await update.message.reply_text(text, reply_markup=main_keyboard(), parse_mode="Markdown")


async def daily_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = datetime.now().strftime("%A, %d %B")
    prompt = (
        f"Сегодня {day}. Дай один практичный совет по питанию для похудения "
        f"на сегодняшний день. Совет должен быть конкретным и выполнимым за день. "
        f"Формат: краткий заголовок + 3-4 предложения объяснения + 1 мотивирующая фраза."
    )
    await update.message.reply_text("⏳ Готовлю совет на сегодня...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"🌅 *Совет на {datetime.now().strftime('%d.%m')}*\n\n{response}", parse_mode="Markdown")


async def water_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hour = datetime.now().hour
    prompt = (
        f"Сейчас {hour}:00. Дай совет по водному балансу для мужчины с сидячим образом жизни "
        f"учитывая время суток. Напомни норму воды, объясни как распределить по времени дня, "
        f"дай 2 практичных лайфхака как не забывать пить воду."
    )
    await update.message.reply_text("⏳ Считаю твой водный баланс...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"💧 *Водный баланс*\n\n{response}", parse_mode="Markdown")


async def lifehack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = [
        "скорость поглощения пищи и насыщение",
        "правильные перекусы при сидячей работе",
        "как не переедать вечером",
        "замена вредных продуктов полезными аналогами",
        "питание для поддержания энергии в течение дня",
        "как читать состав продуктов в магазине",
        "белковые завтраки для похудения",
        "интервальное питание — просто о сложном",
        "как уменьшить тягу к сладкому",
        "приготовление еды заранее (meal prep)",
    ]
    topic = random.choice(topics)
    prompt = (
        f"Дай один конкретный лайфхак на тему: '{topic}'. "
        f"Формат: название лайфхака + объяснение 3-4 предложения + как применить прямо сегодня."
    )
    await update.message.reply_text("⏳ Ищу крутой лайфхак...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"💡 *Лайфхак дня*\n\n{response}", parse_mode="Markdown")


async def snack_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = (
        "Предложи 3 варианта перекуса для мужчины, который хочет похудеть и работает сидя. "
        "Перекусы должны быть: простые в приготовлении (до 5 минут), насыщающие, "
        "с высоким содержанием белка, до 200 ккал. "
        "Для каждого укажи: название, примерную калорийность, почему подходит для похудения."
    )
    await update.message.reply_text("⏳ Подбираю перекусы...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"🥗 *Идеи для перекуса*\n\n{response}", parse_mode="Markdown")


async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📊 *Твой профиль*\n\n"
        "👤 Мужчина, 30–45 лет\n"
        "🎯 Цель: похудение\n"
        "💼 Активность: сидячий образ жизни\n"
        "🚫 Ограничений: нет\n\n"
        "📐 *Рекомендации для тебя:*\n"
        "• Калории: ~1800–2000 ккал/день\n"
        "• Белок: ~120–140 г/день\n"
        "• Вода: ~2.0–2.2 л/день\n"
        "• Приёмов пищи: 3 основных + 1–2 перекуса\n\n"
        "_Хочешь изменить профиль? Напиши мне об этом_"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── Меню из продуктов (диалог) ──────────────────────────────────────────────
async def menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍽️ Отлично! Напиши список продуктов, которые у тебя есть.\n\n"
        "Просто перечисли через запятую или каждый с новой строки. Например:\n\n"
        "_яйца, куриная грудка, гречка, огурцы, помидоры, творог, хлеб_",
        parse_mode="Markdown",
    )
    return WAITING_PRODUCTS


async def menu_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = update.message.text
    prompt = (
        f"У пользователя есть следующие продукты: {products}\n\n"
        f"Составь полноценный план питания на день:\n"
        f"1. ЗАВТРАК — сытный, с белком, ~400–450 ккал\n"
        f"2. ОБЕД — основной приём, ~500–600 ккал\n"
        f"3. УЖИН — лёгкий, ~350–400 ккал\n"
        f"4. ПЕРЕКУС — опциональный, ~150–200 ккал\n\n"
        f"Для каждого блюда: название, список ингредиентов с граммовкой, "
        f"способ приготовления (кратко, 2–3 шага), примерная калорийность.\n\n"
        f"В конце отдельным блоком напиши: '🛒 ДОКУПИТЬ:' — что нужно купить "
        f"чтобы питание было более полноценным и разнообразным (5–7 позиций)."
    )
    await update.message.reply_text("⏳ Составляю меню из твоих продуктов...")
    response = ask_gemini(prompt)
    await update.message.reply_text(f"🍽️ *Меню на день*\n\n{response}", parse_mode="Markdown")
    return ConversationHandler.END


async def menu_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. Возвращаемся в главное меню.", reply_markup=main_keyboard())
    return ConversationHandler.END


# ─── Свободный диалог ────────────────────────────────────────────────────────
async def free_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text

    if user_text == "🌅 Совет на день":
        return await daily_tip(update, context)
    elif user_text == "🍽️ Меню из продуктов":
        return await menu_start(update, context)
    elif user_text == "💧 Водный баланс":
        return await water_tip(update, context)
    elif user_text == "💡 Лайфхак":
        return await lifehack(update, context)
    elif user_text == "🥗 Идеи для перекуса":
        return await snack_ideas(update, context)
    elif user_text == "📊 Мой профиль":
        return await my_profile(update, context)

    prompt = (
        f"Пользователь задаёт вопрос или пишет сообщение: '{user_text}'\n\n"
        f"Ответь как персональный нутрициолог. Если вопрос не связан с питанием или здоровьем — "
        f"мягко верни разговор к теме питания и здорового образа жизни."
    )
    await update.message.reply_text("⏳ Думаю над ответом...")
    response = ask_gemini(prompt)
    await update.message.reply_text(response, parse_mode="Markdown")


# ─── Запуск ──────────────────────────────────────────────────────────────────
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

    logger.info("🤖 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()