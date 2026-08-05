import os
import re
import time
import signal
import sys
import asyncio
import threading
from flask import Flask, request, jsonify
from telethon import TelegramClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==========
BOT_TOKEN = "8534024087:AAE0MAIsHKoWjPA4cuqSKOubAlm7F0_xpG0"
API_ID = 30993809
API_HASH ="9f8a6194865005795b237ab95b4b0559"
PORT = 8080

if not BOT_TOKEN or not API_ID or not API_HASH:
    print("❌ ОШИБКА: Не все переменные окружения установлены!")
    exit(1)

print(f"✅ Токен загружен: {BOT_TOKEN[:10]}...")
print(f"✅ API_ID: {API_ID}")
print(f"✅ Порт: {PORT}")

# Хранилище списков пользователей
user_lists = {}

# ========== СОЗДАЕМ ВЕБ-СЕРВЕР ДЛЯ RENDER ==========
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return jsonify({
        "status": "ok",
        "message": "Бот для проверки подарков работает!",
        "version": "1.0.0"
    })

@app_web.route('/health')
def health():
    return jsonify({"status": "healthy", "uptime": "running"})

@app_web.route('/ping')
def ping():
    return jsonify({"status": "pong"})

def run_web_server():
    """Запускает веб-сервер в отдельном потоке"""
    print(f"🌐 Веб-сервер запущен на порту {PORT}")
    app_web.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ========== ОБРАБОТЧИК СИГНАЛОВ ==========
def shutdown_handler(signum, frame):
    print("\n🛑 Получен сигнал завершения, останавливаю бота...")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

# ========== ФУНКЦИЯ ПРОВЕРКИ ПОДАРКОВ ==========
async def check_gifts_through_bot(username, api_id, api_hash):
    try:
        client = TelegramClient(
            f"session_{username.replace('@', '')}",
            api_id,
            api_hash,
            system_version="4.16.30-vxCUSTOM"
        )
        
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.disconnect()
            return f"❌ {username} - требуется авторизация"
        
        try:
            gift_bot = await client.get_entity("@GiftBot")
        except:
            await client.disconnect()
            return f"❌ {username} - не найден @GiftBot"
        
        await client.send_message(gift_bot, f"/gifts {username}")
        await asyncio.sleep(6)
        
        regular_gifts = 0
        
        async for message in client.iter_messages(gift_bot, limit=5):
            if message.text and username in message.text:
                numbers = re.findall(r'\d+', message.text)
                if numbers:
                    if len(numbers) >= 2:
                        regular_gifts = int(numbers[1])
                    else:
                        regular_gifts = int(numbers[0])
                break
        
        await client.disconnect()
        
        if regular_gifts > 0:
            return f"✅ {username} - {regular_gifts} обычных подарков"
        else:
            return None
                
    except Exception as e:
        return f"❌ {username} - ошибка: {str(e)[:50]}"

# ========== КОМАНДА /start ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📝 Ввести список", callback_data="enter_list")],
        [InlineKeyboardButton("📋 Мой список", callback_data="view_list")],
        [InlineKeyboardButton("🚀 Начать проверку", callback_data="start_check")],
        [InlineKeyboardButton("🗑 Очистить список", callback_data="clear_list")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎁 **Бот для проверки обычных подарков**\n\n"
        "Я проверяю аккаунты через @GiftBot.\n\n"
        "📌 **Как пользоваться:**\n"
        "1. Нажмите 'Ввести список'\n"
        "2. Отправьте список юзернеймов\n"
        "3. Нажмите 'Начать проверку'\n\n"
        "⏱ Проверка: ~3-5 сек на аккаунт",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# ========== ОБРАБОТЧИК КНОПОК ==========
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    action = query.data
    
    if action == "enter_list":
        context.user_data['waiting_for_list'] = True
        await query.edit_message_text(
            "📝 **Отправьте список аккаунтов**\n\n"
            "Каждый юзернейм с новой строки.\n"
            "Пример:\n"
            "`@user1\n@user2\n@user3`\n\n"
            "Нажмите /cancel чтобы отменить.",
            parse_mode="Markdown"
        )
    
    elif action == "view_list":
        if user_id in user_lists and user_lists[user_id]:
            accounts = "\n".join([f"@{u}" for u in user_lists[user_id]])
            await query.edit_message_text(
                f"📋 **Ваш список:**\n\n{accounts}\n\n"
                f"Всего: {len(user_lists[user_id])} аккаунтов",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                "📭 **Список пуст**",
                parse_mode="Markdown"
            )
    
    elif action == "start_check":
        if user_id not in user_lists or not user_lists[user_id]:
            await query.edit_message_text(
                "❌ **Список пуст!**\n\nДобавьте аккаунты.",
                parse_mode="Markdown"
            )
            return
        
        await query.edit_message_text(
            "🔍 **Начинаю проверку...**\n\n"
            f"Проверяю {len(user_lists[user_id])} аккаунтов.\n"
            "⏱ Это может занять несколько минут...",
            parse_mode="Markdown"
        )
        
        results = []
        total = len(user_lists[user_id])
        
        for i, username in enumerate(user_lists[user_id], 1):
            try:
                result = await check_gifts_through_bot(username, API_ID, API_HASH)
                if result:
                    results.append(result)
            except Exception as e:
                pass
            
            if i % 3 == 0 or i == total:
                try:
                    await query.edit_message_text(
                        f"🔍 **Проверка...**\n\n"
                        f"✅ Проверено: {i}/{total}\n"
                        f"🎁 Найдено: {len(results)}",
                        parse_mode="Markdown"
                    )
                except:
                    pass
        
        if results:
            report = "🎁 **Аккаунты с обычными подарками:**\n\n"
            report += "\n".join(results)
            report += f"\n\n📊 **Найдено: {len(results)} из {total}**"
        else:
            report = f"😕 **Не найдено аккаунтов с обычными подарками**\n\nПроверено: {total} аккаунтов"
        
        if len(report) > 4000:
            for x in range(0, len(report), 4000):
                await query.message.reply_text(report[x:x+4000])
        else:
            await query.message.reply_text(report, parse_mode="Markdown")
        
        await show_main_menu(query.message, user_id)
    
    elif action == "clear_list":
        if user_id in user_lists:
            del user_lists[user_id]
        await query.edit_message_text(
            "🗑 **Список очищен!**",
            parse_mode="Markdown"
        )
        await show_main_menu(query.message, user_id)
    
    elif action == "help":
        await query.edit_message_text(
            "❓ **Помощь**\n\n"
            "🤖 Проверяет обычные подарки через @GiftBot\n"
            "📝 Добавьте список аккаунтов\n"
            "🚀 Нажмите 'Начать проверку'\n"
            "⚠️ Использует ваш API ID и HASH",
            parse_mode="Markdown"
        )
        await show_main_menu(query.message, user_id)
    
    elif action == "main_menu":
        await show_main_menu(query.message, user_id)

# ========== ПОКАЗАТЬ ГЛАВНОЕ МЕНЮ ==========
async def show_main_menu(message, user_id):
    keyboard = [
        [InlineKeyboardButton("📝 Ввести список", callback_data="enter_list")],
        [InlineKeyboardButton("📋 Мой список", callback_data="view_list")],
        [InlineKeyboardButton("🚀 Начать проверку", callback_data="start_check")],
        [InlineKeyboardButton("🗑 Очистить список", callback_data="clear_list")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.reply_text(
        "🏠 **Главное меню**\n\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# ========== ОБРАБОТЧИК ТЕКСТА ==========
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if context.user_data.get('waiting_for_list'):
        usernames = re.findall(r'@?[a-zA-Z0-9_]{5,}', text)
        usernames = [u.lstrip('@') for u in usernames]
        usernames = list(dict.fromkeys(usernames))
        
        if usernames:
            if user_id not in user_lists:
                user_lists[user_id] = []
            
            existing = set(user_lists[user_id])
            new_accounts = [u for u in usernames if u not in existing]
            user_lists[user_id].extend(new_accounts)
            
            context.user_data['waiting_for_list'] = False
            
            await update.message.reply_text(
                f"✅ **Добавлено {len(new_accounts)} аккаунтов!**\n\n"
                f"Всего: {len(user_lists[user_id])} аккаунтов",
                parse_mode="Markdown"
            )
            
            await show_main_menu(update.message, user_id)
        else:
            await update.message.reply_text(
                "❌ **Не найдено юзернеймов**\n\n"
                "Отправьте список в формате:\n"
                "`@user1\n@user2\n@user3`",
                parse_mode="Markdown"
            )
    else:
        await show_main_menu(update.message, user_id)

# ========== КОМАНДЫ ==========
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['waiting_for_list'] = False
    await update.message.reply_text("❌ Отменено")
    await show_main_menu(update.message, update.effective_user.id)

async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_lists and user_lists[user_id]:
        accounts = "\n".join([f"@{u}" for u in user_lists[user_id]])
        await update.message.reply_text(
            f"📋 **Ваш список:**\n\n{accounts}\n\n"
            f"Всего: {len(user_lists[user_id])} аккаунтов",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("📭 Список пуст")

async def start_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_lists or not user_lists[user_id]:
        await update.message.reply_text("❌ Список пуст!")
        return
    
    await update.message.reply_text(
        f"🔍 Начинаю проверку {len(user_lists[user_id])} аккаунтов..."
    )
    
    results = []
    total = len(user_lists[user_id])
    
    for i, username in enumerate(user_lists[user_id], 1):
        try:
            result = await check_gifts_through_bot(username, API_ID, API_HASH)
            if result:
                results.append(result)
        except:
            pass
        
        if i % 3 == 0 or i == total:
            try:
                await update.message.reply_text(
                    f"⏳ Прогресс: {i}/{total}\nНайдено: {len(results)}"
                )
            except:
                pass
    
    if results:
        report = "🎁 **Аккаунты с обычными подарками:**\n\n"
        report += "\n".join(results)
        report += f"\n\n📊 Найдено: {len(results)} из {total}"
    else:
        report = f"😕 Не найдено аккаунтов с обычными подарками\n\nПроверено: {total}"
    
    await update.message.reply_text(report, parse_mode="Markdown")

# ========== ЗАПУСК БОТА И ВЕБ-СЕРВЕРА ==========
def main():
    # Запускаем веб-сервер в отдельном потоке
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    print("🌐 Веб-сервер запущен в фоновом потоке")
    
    # Даем время веб-серверу запуститься
    time.sleep(2)
    
    # Запускаем Telegram бота
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("list", show_list))
    app.add_handler(CommandHandler("check", start_check_command))
    
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("🤖 Бот запущен!")
    print("📝 Проверка через @GiftBot")
    print(f"🌐 Веб-сервер доступен по адресу: https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}")
    
    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"]
        )
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        print("🔄 Перезапуск через 5 секунд...")
        time.sleep(5)
        main()

if __name__ == "__main__":
    main()
