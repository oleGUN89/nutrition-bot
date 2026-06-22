# 🥗 Нутри — Telegram бот-нутрициолог

Персональный бот-диетолог на базе Gemini AI.

## Возможности
- 🌅 Ежедневные советы по питанию
- 🍽️ Меню из твоих продуктов (завтрак / обед / ужин)
- 🛒 Список что докупить
- 💧 Советы по водному балансу
- 💡 Лайфхаки по питанию
- 🥗 Идеи для перекусов
- 💬 Свободный диалог с нутрициологом

---

## 🚀 Быстрый старт (локально)

### 1. Получи ключи

**Telegram токен:**
1. Открой [@BotFather](https://t.me/BotFather) в Telegram
2. Напиши `/newbot`
3. Придумай имя и username для бота
4. Скопируй токен

**Gemini API ключ:**
1. Зайди на [aistudio.google.com](https://aistudio.google.com)
2. Нажми "Get API Key" → "Create API Key"
3. Скопируй ключ (бесплатно!)

### 2. Установи зависимости

```bash
pip install -r requirements.txt
```

### 3. Настрой ключи

Создай файл `.env` (или просто задай переменные окружения):

```bash
export TELEGRAM_TOKEN="твой_токен"
export GEMINI_API_KEY="твой_ключ"
```

Или отредактируй прямо в `bot.py` строки:
```python
TELEGRAM_TOKEN = "вставь_сюда"
GEMINI_API_KEY = "вставь_сюда"
```

### 4. Запусти бота

```bash
python bot.py
```

Открой своего бота в Telegram и напиши `/start` 🎉

---

## ☁️ Деплой на Railway (бот работает 24/7)

1. Создай аккаунт на [railway.app](https://railway.app)
2. Нажми "New Project" → "Deploy from GitHub repo"
3. Загрузи эти файлы в GitHub репозиторий
4. В Railway перейди в Settings → Variables и добавь:
   - `TELEGRAM_TOKEN` = твой токен
   - `GEMINI_API_KEY` = твой ключ
5. Railway автоматически запустит бота!

---

## 📁 Структура файлов

```
nutrition_bot/
├── bot.py            ← основной код бота
├── requirements.txt  ← зависимости Python
├── Procfile          ← инструкция для Railway
├── .env.example      ← пример файла с ключами
└── README.md         ← эта инструкция
```

---

## 🆘 Частые проблемы

**Бот не отвечает:**
- Проверь правильность TELEGRAM_TOKEN
- Убедись что бот запущен (`python bot.py`)

**Ошибка Gemini:**
- Проверь GEMINI_API_KEY
- Убедись что есть интернет-соединение

**Ошибка при установке:**
```bash
pip install --upgrade pip
pip install -r requirements.txt
```
