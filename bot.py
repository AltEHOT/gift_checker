import asyncio
import re
from pyrogram import Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ВАШИ ДАННЫЕ (получите их на my.telegram.org)
API_ID = 12345  # ЗАМЕНИТЕ на ваш api_id
API_HASH = "ваш_api_hash"  # ЗАМЕНИТЕ на ваш api_hash
BOT_TOKEN = "ваш_токен_бота_от_BotFather"  # ЗАМЕНИТЕ на токен бота

# Хранилище списков пользователей
user_lists = {}

# Функция проверки ТОЛЬКО неулучшенных подарков
async def check_regular_gifts(username, api_id, api_hash):
    try:
        # Используем новый способ создания клиента с правильным event loop
        loop = asyncio.get_event_loop()
        
        app = Client(
            "temp_session",
            api_id=api_id,
            api_hash=api_hash,
            in_memory=True,
            workdir="."  # Добавляем рабочую директорию
        )
        
        # Запускаем клиент и проверяем
        async with app:
            user = await app.get_users(username)
            
            # Получаем подарки пользователя
            try:
                gifts = await app.get_gifts(user.id)
            except:
                gifts = []
            
            regular_gifts = 0
            if gifts:
                for gift in gifts:
                    # Проверяем, улучшен ли подарок
                    is_upgraded = False
                    if hasattr(gift, 'is_upgraded'):
                        is_upgraded = gift.is_upgraded
                    elif hasattr(gift, 'upgraded'):
                        is_upgraded = gift.upgraded
                    elif hasattr(gift, 'upgrade'):
                        is_upgraded = gift.upgrade
                    
                    if not is_upgraded:
                        regular_gifts += 1
            
            if regular_gifts > 0:
                return f"✅ {username} - {regular_gifts} обычных подарков"
            else:
                return None
                
    except Exception as e:
        return f"❌ {username} - ошибка: {str(e)[:50]}"

# Функция для проверки в отдельном event loop
async def check_all_accounts(accounts):
    results = []
    for i, username in enumerate(accounts, 1):
        result = await check_regular_gifts(username, API_ID, API_HASH)
        if result:
            results.append(result)
    return results

# Команда /start
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

# Обработчик кнопок
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
        
        # Проверяем аккаунты
        results = []
        total = len(user_lists[user_id])
        
        # Создаем отдельный event loop для pyrogram
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        for i, username in enumerate(user_lists[user_id], 1):
            try:
                # Запускаем проверку с правильным event loop
                result = await check_regular_gifts(username, API_ID, API_HASH)
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
        
        loop.close()
        
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
        
        # Возвращаем главное меню
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

# Показать главное меню
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

# Обработчик текстовых сообщений (для ввода списка)
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Если пользователь в режиме ввода списка
    if context.user_data.get('waiting_for_list'):
        # Парсим юзернеймы из текста
        usernames = re.findall(r'@?[a-zA-Z0-9_]{5,}', text)
        
        # Очищаем от @ в начале
        usernames = [u.lstrip('@') for u in usernames]
        
        # Убираем дубликаты
        usernames = list(dict.fromkeys(usernames))
        
        if usernames:
            # Сохраняем в список пользователя
            if user_id not in user_lists:
                user_lists[user_id] = []
            
            # Добавляем новые аккаунты (без дубликатов внутри списка)
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
            
            # Показываем главное меню
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
        # Если не в режиме ввода - показываем меню
        await show_main_menu(update.message, user_id)

# Команда /cancel - отмена ввода списка
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['waiting_for_list'] = False
    await update.message.reply_text(
        "❌ **Ввод списка отменен**",
        parse_mode="Markdown"
    )
    await show_main_menu(update.message, update.effective_user.id)

# Команда /list - показать список
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

# Команда /check - начать проверку
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
    
    # Проверяем аккаунты (аналогично кнопке)
    results = []
    total = len(user_lists[user_id])
    
    # Создаем отдельный event loop для pyrogram
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    for i, username in enumerate(user_lists[user_id], 1):
        try:
            result = await check_regular_gifts(username, API_ID, API_HASH)
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
    
    loop.close()
    
    if results:
        report = "🎁 **Аккаунты с обычными подарками:**\n\n"
        report += "\n".join(results)
        report += f"\n\n📊 **Найдено: {len(results)} из {total}**"
    else:
        report = f"😕 **Не найдено аккаунтов с обычными подарками**\n\nПроверено: {total} аккаунтов"
    
    await update.message.reply_text(report, parse_mode="Markdown")

# Запуск бота
def main():
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("list", show_list))
    app.add_handler(CommandHandler("check", start_check_command))
    
    # Обработчики
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("🤖 Бот запущен и готов к работе!")
    print("📝 Пользователи могут вводить свои списки аккаунтов")
    
    # Запускаем бота
    app.run_polling()

if __name__ == "__main__":
    main()
