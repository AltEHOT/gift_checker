import re
import asyncio
from telethon import TelegramClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ВАШИ ДАННЫЕ (получите их на my.telegram.org)
API_ID = 12345  # ЗАМЕНИТЕ на ваш api_id
API_HASH = "ваш_api_hash"  # ЗАМЕНИТЕ на ваш api_hash
BOT_TOKEN = "ваш_токен_бота_от_BotFather"  # ЗАМЕНИТЕ на токен бота

# Хранилище списков пользователей
user_lists = {}

# ========== ФУНКЦИЯ ПРОВЕРКИ ПОДАРКОВ ЧЕРЕЗ @GiftBot ==========
async def check_gifts_through_bot(username, api_id, api_hash):
    """
    Проверка подарков через бота @GiftBot
    Отправляет запрос боту и парсит ответ
    """
    try:
        # Создаем клиент Telethon (без cryptg)
        client = TelegramClient(
            f"session_{username.replace('@', '')}",
            api_id,
            api_hash,
            system_version="4.16.30-vxCUSTOM",
            device_model="iPhone 15 Pro Max",
            app_version="10.3.0"
        )
        
        # Подключаемся
        await client.connect()
        
        # Проверяем авторизацию
        if not await client.is_user_authorized():
            await client.disconnect()
            return f"❌ {username} - требуется авторизация API"
        
        # Получаем GiftBot
        try:
            gift_bot = await client.get_entity("@GiftBot")
        except:
            await client.disconnect()
            return f"❌ {username} - не найден @GiftBot"
        
        # Отправляем запрос на проверку подарков
        await client.send_message(gift_bot, f"/gifts {username}")
        
        # Ждем ответ (боту нужно время на обработку)
        await asyncio.sleep(6)
        
        # Получаем последние сообщения от GiftBot
        regular_gifts = 0
        
        async for message in client.iter_messages(gift_bot, limit=5):
            if message.text and username in message.text:
                text = message.text
                
                # Ищем числа в тексте
                numbers = re.findall(r'\d+', text)
                
                # В ответе GiftBot обычно два числа: улучшенные и обычные
                if numbers:
                    # Если есть 2 числа - второе это обычные подарки
                    if len(numbers) >= 2:
                        regular_gifts = int(numbers[1]) if len(numbers) > 1 else 0
                    else:
                        # Если одно число - это общее количество
                        regular_gifts = int(numbers[0])
                
                break
        
        # Отключаемся
        await client.disconnect()
        
        # Возвращаем результат только если есть обычные подарки
        if regular_gifts > 0:
            return f"✅ {username} - {regular_gifts} обычных подарков"
        else:
            return None
                
    except Exception as e:
        return f"❌ {username} - ошибка: {str(e)[:50]}"

# ========== КОМАНДА /start ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    keyboard = [
        [InlineKeyboardButton("📝 Ввести список аккаунтов", callback_data="enter_list")],
        [InlineKeyboardButton("📋 Посмотреть мой список", callback_data="view_list")],
        [InlineKeyboardButton("🚀 Начать проверку", callback_data="start_check")],
        [InlineKeyboardButton("🗑 Очистить список", callback_data="clear_list")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎁 **Бот для проверки обычных подарков**\n\n"
        "Я проверяю аккаунты через @GiftBot.\n\n"
        "📌 **Как пользоваться:**\n"
        "1. Нажмите 'Ввести список аккаунтов'\n"
        "2. Отправьте список юзернеймов (каждый с новой строки)\n"
        "3. Нажмите 'Начать проверку'\n\n"
        "⏱ Проверка может занять 2-3 минуты.\n"
        "⚠️ Аккаунты проверяются через ваш API (my.telegram.org)",
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
            "Или просто отправьте текст с юзернеймами.\n"
            "Нажмите /cancel чтобы отменить.",
            parse_mode="Markdown"
        )
    
    elif action == "view_list":
        if user_id in user_lists and user_lists[user_id]:
            accounts = "\n".join([f"@{u}" for u in user_lists[user_id]])
            await query.edit_message_text(
                f"📋 **Ваш список аккаунтов:**\n\n{accounts}\n\n"
                f"Всего: {len(user_lists[user_id])} аккаунтов",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                "📭 **Список пуст**\n\n"
                "Нажмите 'Ввести список аккаунтов' чтобы добавить аккаунты.",
                parse_mode="Markdown"
            )
    
    elif action == "start_check":
        if user_id not in user_lists or not user_lists[user_id]:
            await query.edit_message_text(
                "❌ **Список пуст!**\n\n"
                "Сначала добавьте аккаунты через 'Ввести список аккаунтов'.",
                parse_mode="Markdown"
            )
            return
        
        await query.edit_message_text(
            "🔍 **Начинаю проверку...**\n\n"
            f"Проверяю {len(user_lists[user_id])} аккаунтов.\n"
            "⏱ Это может занять 2-3 минуты...\n\n"
            "Бот отправляет запросы @GiftBot и ждет ответы.",
            parse_mode="Markdown"
        )
        
        results = []
        total = len(user_lists[user_id])
        
        for i, username in enumerate(user_lists[user_id], 1):
            # Используем функцию проверки через @GiftBot
            try:
                result = await check_gifts_through_bot(username, API_ID, API_HASH)
                if result:
                    results.append(result)
            except Exception as e:
                pass
            
            # Обновляем прогресс каждые 3 аккаунта
            if i % 3 == 0 or i == total:
                try:
                    await query.edit_message_text(
                        f"🔍 **Проверка...**\n\n"
                        f"✅ Проверено: {i}/{total}\n"
                        f"🎁 Найдено с обычными подарками: {len(results)}",
                        parse_mode="Markdown"
                    )
                except:
                    pass
        
        # Формируем финальный отчет
        if results:
            report = "🎁 **Аккаунты с обычными подарками:**\n\n"
            report += "\n".join(results)
            report += f"\n\n📊 **Найдено: {len(results)} из {total}**"
        else:
            report = f"😕 **Не найдено аккаунтов с обычными подарками**\n\nПроверено: {total} аккаунтов"
        
        # Отправляем результат
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
            "🤖 **Что умеет этот бот?**\n"
            "Проверяет аккаунты на наличие обычных (неулучшенных) подарков.\n"
            "Для этого он использует @GiftBot.\n\n"
            "📝 **Как добавить аккаунты?**\n"
            "1. Нажмите 'Ввести список аккаунтов'\n"
            "2. Отправьте юзернеймы (с @ или без)\n"
            "3. Каждый юзернейм с новой строки\n\n"
            "🚀 **Как проверить?**\n"
            "После добавления списка нажмите 'Начать проверку'\n\n"
            "⚠️ **Важно:**\n"
            "Бот использует ваш API ID и API HASH из my.telegram.org\n"
            "Аккаунты НЕ сохраняются на сервере.\n\n"
            "⏱ Время проверки: ~3-5 секунд на аккаунт",
            parse_mode="Markdown"
        )
        await show_main_menu(query.message, user_id)
    
    elif action == "main_menu":
        await show_main_menu(query.message, user_id)

# ========== ПОКАЗАТЬ ГЛАВНОЕ МЕНЮ ==========
async def show_main_menu(message, user_id):
    keyboard = [
        [InlineKeyboardButton("📝 Ввести список аккаунтов", callback_data="enter_list")],
        [InlineKeyboardButton("📋 Посмотреть мой список", callback_data="view_list")],
        [InlineKeyboardButton("🚀 Начать проверку", callback_data="start_check")],
        [InlineKeyboardButton("🗑 Очистить список", callback_data="clear_list")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.reply_text(
        "🏠 **Главное меню**\n\n"
        "Выберите действие:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# ========== ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ ==========
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if context.user_data.get('waiting_for_list'):
        # Находим все юзернеймы в тексте
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
                f"Всего в списке: {len(user_lists[user_id])} аккаунтов\n\n"
                f"Добавленные:\n" + "\n".join([f"@{u}" for u in new_accounts[:10]]) + 
                (f"\n...и еще {len(new_accounts)-10}" if len(new_accounts) > 10 else ""),
                parse_mode="Markdown"
            )
            
            await show_main_menu(update.message, user_id)
        else:
            await update.message.reply_text(
                "❌ **Не найдено юзернеймов**\n\n"
                "Убедитесь, что вы отправили список в правильном формате:\n"
                "`@user1\n@user2\n@user3`\n\n"
                "Или просто отправьте /cancel чтобы отменить.",
                parse_mode="Markdown"
            )
    else:
        await show_main_menu(update.message, user_id)

# ========== КОМАНДА /cancel ==========
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['waiting_for_list'] = False
    await update.message.reply_text(
        "❌ **Ввод списка отменен**",
        parse_mode="Markdown"
    )
    await show_main_menu(update.message, update.effective_user.id)

# ========== КОМАНДА /list ==========
async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_lists and user_lists[user_id]:
        accounts = "\n".join([f"@{u}" for u in user_lists[user_id]])
        await update.message.reply_text(
            f"📋 **Ваш список аккаунтов:**\n\n{accounts}\n\n"
            f"Всего: {len(user_lists[user_id])} аккаунтов",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("📭 Список пуст")

# ========== КОМАНДА /check ==========
async def start_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_lists or not user_lists[user_id]:
        await update.message.reply_text(
            "❌ **Список пуст!**\n\nСначала добавьте аккаунты через /start",
            parse_mode="Markdown"
        )
        return
    
    await update.message.reply_text(
        f"🔍 **Начинаю проверку...**\n\n"
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
                await update.message.reply_text(
                    f"⏳ Прогресс: {i}/{total}\n"
                    f"Найдено: {len(results)}",
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
    
    await update.message.reply_text(report, parse_mode="Markdown")

# ========== ЗАПУСК БОТА ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("list", show_list))
    app.add_handler(CommandHandler("check", start_check_command))
    
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("🤖 Бот запущен и готов к работе!")
    print("📝 Пользователи могут вводить свои списки аккаунтов")
    print("🔍 Проверка выполняется через @GiftBot")
    
    app.run_polling()

if __name__ == "__main__":
    main()
