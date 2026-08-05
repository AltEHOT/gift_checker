import os
import re
import time
import signal
import sys
import asyncio
import threading
from flask import Flask, request, jsonify
from telethon import TelegramClient, types
from telethon.tl.functions.users import GetFullUserRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
PORT = int(os.environ.get("PORT", 8080))

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

# ========== ФУНКЦИЯ ПРОВЕРКИ ПОДАРКОВ ЧЕРЕЗ TELETHON ==========
async def check_gifts_telethon(username, api_id, api_hash):
    """
    Асинхронная проверка подарков через Telethon
    Возвращает: количество обычных подарков или None при ошибке
    """
    try:
        # Создаем клиент Telethon
        client = TelegramClient(
            "my_session",
            api_id,
            api_hash,
            system_version="4.16.30-vxCUSTOM"
        )
        
        # Подключаемся
        await client.connect()
        
        # Проверяем авторизацию
        if not await client.is_user_authorized():
            await client.disconnect()
            print(f"❌ {username} - требуется авторизация")
            return None
        
        try:
            # Получаем пользователя
            user = await client.get_entity(username)
            
            # Получаем полную информацию о пользователе
            full_user = await client(GetFullUserRequest(user.id))
            
            # Пытаемся получить подарки
            # В Telethon нет прямого метода get_gifts, но можно попробовать через full_user
            regular_gifts = 0
            
            # Проверяем, есть ли поле gifts в full_user
            if hasattr(full_user, 'gifts') and full_user.gifts:
                for gift in full_user.gifts:
                    # Проверяем, улучшен ли подарок
                    is_upgraded = False
                    if hasattr(gift, 'upgraded'):
                        is_upgraded = gift.upgraded
                    elif hasattr(gift, 'is_upgraded'):
                        is_upgraded = gift.is_upgraded
                    
                    if not is_upgraded:
                        regular_gifts += 1
            
            # Если через GetFullUserRequest не получилось, пробуем альтернативный метод
            if regular_gifts == 0:
                # Пробуем получить через метод get_gifts (если есть в новой версии)
                try:
                    if hasattr(client, 'get_gifts'):
                        gifts = await client.get_gifts(user.id)
                        if gifts:
                            for gift in gifts:
                                is_upgraded = False
                                if hasattr(gift, 'upgraded'):
                                    is_upgraded = gift.upgraded
                                elif hasattr(gift, 'is_upgraded'):
                                    is_upgraded = gift.is_upgraded
                                
                                if not is_upgraded:
                                    regular_gifts += 1
                except:
                    pass
            
            # Отключаемся
            await client.disconnect()
            
            return regular_gifts
                
        except Exception as e:
            await client.disconnect()
            print(f"❌ Ошибка при проверке {username}: {e}")
            return None
            
    except Exception as e:
        print(f"❌ Ошибка при создании клиента для {username}: {e}")
        return None

# ========== ФУНКЦИЯ ДЛЯ ЗАПУСКА В ОТДЕЛЬНОМ ПОТОКЕ ==========
def check_gifts_thread(username, api_id, api_hash):
    """
    Запускает проверку в отдельном потоке для избежания конфликтов asyncio
    """
    try:
        # Создаем новый event loop для этого потока
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            check_gifts_telethon(username, api_id, api_hash)
        )
        loop.close()
        return result
    except Exception as e:
        print(f"❌ Ошибка в потоке для {username}: {e}")
        return None

# ========== АСИНХРОННАЯ ОБЕРТКА ДЛЯ ПРОВЕРКИ ==========
async def check_gifts_async(username, api_id, api_hash):
    """
    Асинхронная обертка для запуска синхронной проверки в отдельном потоке
    """
    try:
        # Запускаем синхронную функцию в отдельном потоке
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, 
            check_gifts_thread, 
            username, 
            api_id, 
            api_hash
        )
        return result
    except Exception as e:
        print(f"❌ Ошибка при асинхронном запуске {username}: {e}")
        return None

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
        "🎁 Бот для проверки обычных подарков\n\n"
        "Я проверяю аккаунты напрямую через Telegram API (Telethon).\n\n"
        "📌 Как пользоваться:\n"
        "1. Нажмите 'Ввести список'\n"
        "2. Отправьте список юзернеймов\n"
        "3. Нажмите 'Начать проверку'\n\n"
        "⏱ Проверка: ~2-3 сек на аккаунт",
        reply_markup=reply_markup
    )

# ========== ФУНКЦИЯ ПАРСИНГА СПИСКА ==========
def parse_accounts(text):
    """
    Парсит список аккаунтов
    Поддерживает форматы:
    - @username - число
    - @username
    - username
    """
    accounts = []
    
    lines = text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Ищем паттерн: @username - число или username - число
        match = re.match(r'@?([a-zA-Z0-9_]{5,})\s*[-–—]\s*(\d+)', line)
        if match:
            username = match.group(1)
            total_gifts = int(match.group(2))
            accounts.append({
                "username": username,
                "total_gifts": total_gifts
            })
        else:
            # Если формат не совпадает, пытаемся найти просто юзернейм
            usernames = re.findall(r'@?([a-zA-Z0-9_]{5,})', line)
            if usernames:
                accounts.append({
                    "username": usernames[0],
                    "total_gifts": 0
                })
    
    return accounts

# ========== ОБРАБОТЧИК КНОПОК ==========
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    action = query.data
    
    if action == "enter_list":
        context.user_data['waiting_for_list'] = True
        await query.edit_message_text(
            "📝 Отправьте список аккаунтов\n\n"
            "Каждый аккаунт с новой строки.\n"
            "Формат:\n"
            "@username - количество_подарков (необязательно)\n\n"
            "Пример:\n"
            "@sirkapirkaw - 1\n"
            "@sofuuha - 2\n"
            "@nuwxkdr\n\n"
            "Нажмите /cancel чтобы отменить."
        )
    
    elif action == "view_list":
        if user_id in user_lists and user_lists[user_id]:
            accounts_text = "\n".join([
                f"@{acc['username']} - {acc['total_gifts']}" 
                for acc in user_lists[user_id]
            ])
            await query.edit_message_text(
                f"📋 Ваш список:\n\n{accounts_text}\n\n"
                f"Всего: {len(user_lists[user_id])} аккаунтов"
            )
        else:
            await query.edit_message_text("📭 Список пуст")
    
    elif action == "start_check":
        if user_id not in user_lists or not user_lists[user_id]:
            await query.edit_message_text("❌ Список пуст!\n\nДобавьте аккаунты.")
            return
        
        await query.edit_message_text(
            f"🔍 Начинаю проверку...\n\n"
            f"Проверяю {len(user_lists[user_id])} аккаунтов.\n"
            "⏱ Это может занять несколько минут..."
        )
        
        results = []
        total = len(user_lists[user_id])
        
        for i, account in enumerate(user_lists[user_id], 1):
            username = account['username']
            user_total_gifts = account['total_gifts']
            
            # Проверяем через Telethon
            regular_gifts = await check_gifts_async(username, API_ID, API_HASH)
            
            if regular_gifts is not None:
                if regular_gifts > 0:
                    if user_total_gifts > 0:
                        results.append(
                            f"✅ @{username} - {regular_gifts} обычных (всего {user_total_gifts})"
                        )
                    else:
                        results.append(f"✅ @{username} - {regular_gifts} обычных подарков")
                else:
                    results.append(f"ℹ️ @{username} - нет обычных подарков")
            else:
                results.append(f"❌ @{username} - ошибка проверки")
            
            if i % 3 == 0 or i == total:
                try:
                    await query.edit_message_text(
                        f"🔍 Проверка...\n\n"
                        f"✅ Проверено: {i}/{total}\n"
                        f"🎁 Найдено с обычными подарками: {len([r for r in results if '✅' in r])}"
                    )
                except:
                    pass
        
        # Формируем финальный отчет
        if results:
            with_gifts = [r for r in results if '✅' in r]
            without_gifts = [r for r in results if 'ℹ️' in r]
            errors = [r for r in results if '❌' in r]
            
            report_parts = []
            report_parts.append("🎁 Результаты проверки:\n")
            
            if with_gifts:
                report_parts.append("\n✅ С обычными подарками:")
                report_parts.extend(with_gifts)
            
            if without_gifts:
                report_parts.append("\nℹ️ Без обычных подарков:")
                report_parts.extend(without_gifts)
            
            if errors:
                report_parts.append("\n❌ Ошибки:")
                report_parts.extend(errors)
            
            report_parts.append(f"\n📊 Всего проверено: {total}")
            
            report = "\n".join(report_parts)
        else:
            report = "😕 Не удалось проверить аккаунты"
        
        if len(report) > 4000:
            for x in range(0, len(report), 4000):
                await query.message.reply_text(report[x:x+4000])
        else:
            await query.message.reply_text(report)
        
        await show_main_menu(query.message, user_id)
    
    elif action == "clear_list":
        if user_id in user_lists:
            del user_lists[user_id]
        await query.edit_message_text("🗑 Список очищен!")
        await show_main_menu(query.message, user_id)
    
    elif action == "help":
        await query.edit_message_text(
            "❓ Помощь\n\n"
            "🤖 Что умеет этот бот?\n"
            "Проверяет аккаунты на наличие обычных (неулучшенных) подарков.\n\n"
            "📝 Формат ввода:\n"
            "@username - количество_всех_подарков (необязательно)\n\n"
            "Пример:\n"
            "@sirkapirkaw - 1\n"
            "@sofuuha - 2\n\n"
            "🚀 После ввода нажмите 'Начать проверку'\n\n"
            "⚠️ Использует ваш API ID и HASH"
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
        "🏠 Главное меню\n\nВыберите действие:",
        reply_markup=reply_markup
    )

# ========== ОБРАБОТЧИК ТЕКСТА ==========
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if context.user_data.get('waiting_for_list'):
        parsed_accounts = parse_accounts(text)
        
        if parsed_accounts:
            if user_id not in user_lists:
                user_lists[user_id] = []
            
            existing_usernames = set([acc['username'] for acc in user_lists[user_id]])
            new_accounts = [
                acc for acc in parsed_accounts 
                if acc['username'] not in existing_usernames
            ]
            user_lists[user_id].extend(new_accounts)
            
            context.user_data['waiting_for_list'] = False
            
            added_text = "\n".join([
                f"@{acc['username']} - {acc['total_gifts']}" 
                for acc in new_accounts[:10]
            ])
            
            await update.message.reply_text(
                f"✅ Добавлено {len(new_accounts)} аккаунтов!\n\n"
                f"Всего в списке: {len(user_lists[user_id])} аккаунтов\n\n"
                f"Добавленные:\n{added_text}" +
                (f"\n...и еще {len(new_accounts)-10}" if len(new_accounts) > 10 else "")
            )
            
            await show_main_menu(update.message, user_id)
        else:
            await update.message.reply_text(
                "❌ Не найдено аккаунтов в правильном формате\n\n"
                "Отправьте список в формате:\n"
                "@username - количество_подарков (необязательно)\n\n"
                "Пример:\n"
                "@sirkapirkaw - 1\n"
                "@sofuuha - 2\n\n"
                "Или просто отправьте /cancel чтобы отменить."
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
        accounts_text = "\n".join([
            f"@{acc['username']} - {acc['total_gifts']}" 
            for acc in user_lists[user_id]
        ])
        await update.message.reply_text(
            f"📋 Ваш список:\n\n{accounts_text}\n\n"
            f"Всего: {len(user_lists[user_id])} аккаунтов"
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
    
    for i, account in enumerate(user_lists[user_id], 1):
        username = account['username']
        user_total_gifts = account['total_gifts']
        
        regular_gifts = await check_gifts_async(username, API_ID, API_HASH)
        
        if regular_gifts is not None:
            if regular_gifts > 0:
                if user_total_gifts > 0:
                    results.append(
                        f"✅ @{username} - {regular_gifts} обычных (всего {user_total_gifts})"
                    )
                else:
                    results.append(f"✅ @{username} - {regular_gifts} обычных")
            else:
                results.append(f"ℹ️ @{username} - нет обычных подарков")
        else:
            results.append(f"❌ @{username} - ошибка проверки")
        
        if i % 3 == 0 or i == total:
            try:
                await update.message.reply_text(
                    f"⏳ Прогресс: {i}/{total}\n"
                    f"Найдено: {len([r for r in results if '✅' in r])}"
                )
            except:
                pass
    
    if results:
        with_gifts = [r for r in results if '✅' in r]
        without_gifts = [r for r in results if 'ℹ️' in r]
        errors = [r for r in results if '❌' in r]
        
        report_parts = []
        report_parts.append("🎁 Результаты проверки:\n")
        
        if with_gifts:
            report_parts.append("\n✅ С обычными подарками:")
            report_parts.extend(with_gifts)
        
        if without_gifts:
            report_parts.append("\nℹ️ Без обычных подарков:")
            report_parts.extend(without_gifts)
        
        if errors:
            report_parts.append("\n❌ Ошибки:")
            report_parts.extend(errors)
        
        report_parts.append(f"\n📊 Всего проверено: {total}")
        
        report = "\n".join(report_parts)
    else:
        report = "😕 Не удалось проверить аккаунты"
    
    await update.message.reply_text(report)

# ========== ЗАПУСК ==========
def main():
    # Запускаем веб-сервер в отдельном потоке
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    print("🌐 Веб-сервер запущен в фоновом потоке")
    
    time.sleep(2)
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("list", show_list))
    app.add_handler(CommandHandler("check", start_check_command))
    
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("🤖 Бот запущен!")
    print("📝 Проверка через Telethon (прямое API)")
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
