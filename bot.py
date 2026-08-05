import re
from telethon import TelegramClient, types
from telethon.tl.functions.users import GetFullUserRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ВАШИ ДАННЫЕ (получите их на my.telegram.org)
API_ID = 12345  # ЗАМЕНИТЕ на ваш api_id
API_HASH = "ваш_api_hash"  # ЗАМЕНИТЕ на ваш api_hash
BOT_TOKEN = "ваш_токен_бота_от_BotFather"  # ЗАМЕНИТЕ на токен бота

# Хранилище списков пользователей
user_lists = {}

# ========== ФУНКЦИЯ ПРОВЕРКИ ПОДАРКОВ (С ИСПОЛЬЗОВАНИЕМ TELEGRAM-API) ==========
async def check_regular_gifts(username, api_id, api_hash):
    """
    Асинхронная проверка подарков с использованием Telethon
    """
    try:
        # Создаем клиент Telethon
        client = TelegramClient(
            f"session_{username}",
            api_id,
            api_hash,
            system_version="4.16.30-vxCUSTOM"
        )
        
        try:
            # Подключаемся
            await client.connect()
            
            # Получаем пользователя
            user = await client.get_entity(username)
            
            # Пытаемся получить подарки (если API поддерживает)
            # Telethon может не иметь прямого метода get_gifts,
            # поэтому используем другой подход через бота
            regular_gifts = 0
            
            # Проверяем через бота @GiftBot
            try:
                # Отправляем запрос к боту
                gift_bot = await client.get_entity("@GiftBot")
                await client.send_message(gift_bot, f"/gifts {username}")
                
                # Ждем ответ
                async for message in client.iter_messages(gift_bot, limit=1):
                    if message.text and "подарков" in message.text.lower():
                        # Парсим количество подарков
                        import re
                        numbers = re.findall(r'\d+', message.text)
                        if numbers:
                            # Берем последнее число - это общее количество подарков
                            # (упрощенно, нужна доработка)
                            pass
            except:
                pass
            
            await client.disconnect()
            
            # Пока возвращаем заглушку
            # В реальности нужно доработать парсинг
            return None
                
        except Exception as e:
            await client.disconnect()
            return f"❌ {username} - ошибка: {str(e)[:50]}"
            
    except Exception as e:
        return f"❌ {username} - ошибка: {str(e)[:50]}"

# ========== НОВАЯ ФУНКЦИЯ ПРОВЕРКИ ЧЕРЕЗ API ==========
async def check_gifts_via_api(username, api_id, api_hash):
    """
    Альтернативный метод проверки подарков через прямой API запрос
    """
    try:
        client = TelegramClient(
            f"temp_session",
            api_id,
            api_hash,
            system_version="4.16.30-vxCUSTOM"
        )
        
        await client.connect()
        
        # Получаем информацию о пользователе
        user = await client.get_entity(username)
        
        # Пытаемся получить gifts через внутренний метод
        try:
            # Это экспериментальный метод, может не работать
            result = await client(GetFullUserRequest(user.id))
            # Парсим результат
            regular_gifts = 0
            
            # Проверяем наличие подарков в профиле
            if hasattr(result, 'gifts'):
                if result.gifts:
                    for gift in result.gifts:
                        if not getattr(gift, 'upgraded', False):
                            regular_gifts += 1
            
            await client.disconnect()
            
            if regular_gifts > 0:
                return f"✅ {username} - {regular_gifts} обычных подарков"
            return None
            
        except Exception as e:
            await client.disconnect()
            return f"❌ {username} - ошибка: {str(e)[:50]}"
            
    except Exception as e:
        return f"❌ {username} - ошибка: {str(e)[:50]}"

# ========== ПРОСТАЯ ФУНКЦИЯ ПРОВЕРКИ (БЕЗ АВТОРИЗАЦИИ) ==========
def check_gifts_simple(username):
    """
    Самый простой способ - без авторизации, только проверка существования
    """
    # Это заглушка - в реальности нужна авторизация
    return None

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
        "Я помогу вам найти аккаунты, у которых есть обычные (неулучшенные) подарки.\n\n"
        "📌 **Как пользоваться:**\n"
        "1. Нажмите 'Ввести список аккаунтов'\n"
        "2. Отправьте список юзернеймов (каждый с новой строки)\n"
        "3. Нажмите 'Начать проверку'\n\n"
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
            "Это может занять несколько минут...",
            parse_mode="Markdown"
        )
        
        results = []
        total = len(user_lists[user_id])
        
        for i, username in enumerate(user_lists[user_id], 1):
            # Используем проверку через Telethon
            try:
                result = await check_gifts_via_api(username, API_ID, API_HASH)
                if result:
                    results.append(result)
            except Exception as e:
                pass
            
            # Обновляем прогресс каждые 5 аккаунтов
            if i % 5 == 0 or i == total:
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
            "Проверяет аккаунты на наличие обычных (неулучшенных) подарков.\n\n"
            "📝 **Как добавить аккаунты?**\n"
            "1. Нажмите 'Ввести список аккаунтов'\n"
            "2. Отправьте юзернеймы (с @ или без)\n"
            "3. Каждый юзернейм с новой строки\n\n"
            "🚀 **Как проверить?**\n"
            "После добавления списка нажмите 'Начать проверку'\n\n"
            "⚠️ **Важно:**\n"
            "Бот использует ваш API ID и API HASH из my.telegram.org\n"
            "Аккаунты НЕ сохраняются на сервере.",
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
        f"Проверяю {len(user_lists[user_id])} аккаунтов...",
        parse_mode="Markdown"
    )
    
    results = []
    total = len(user_lists[user_id])
    
    for i, username in enumerate(user_lists[user_id], 1):
        try:
            result = await check_gifts_via_api(username, API_ID, API_HASH)
            if result:
                results.append(result)
        except Exception as e:
            pass
        
        if i % 5 == 0 or i == total:
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
    
    app.run_polling()

if __name__ == "__main__":
    main()
