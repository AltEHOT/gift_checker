import os
import sys
import time
import random
import logging
import asyncio
import base64
import traceback
import re
from flask import Flask, jsonify
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

# --- НАСТРОЙКА ЛОГОВ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
PORT = int(os.getenv("PORT", 8080))

# --- НАСТРОЙКИ ---
MAX_USERS = 10

if not API_ID or not API_HASH:
    logger.error("❌ API_ID и API_HASH не установлены!")
    sys.exit(1)

# --- ПОДГОТОВКА СЕССИИ ---
if SESSION_STRING and SESSION_STRING not in ["None", "NONE", "none", ""]:
    logger.info("🔑 Использую StringSession")
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
elif os.path.exists("session.b64"):
    logger.info("📁 Декодирую session.b64...")
    try:
        with open("session.b64", "r") as f:
            b64_data = f.read().strip()
        with open("userbot_session.session", "wb") as f:
            f.write(base64.b64decode(b64_data))
        logger.info("✅ Сессия декодирована")
        client = TelegramClient("userbot_session", API_ID, API_HASH)
    except Exception as e:
        logger.error(f"❌ Ошибка декодирования: {e}")
        sys.exit(1)
elif os.path.exists("userbot_session.session"):
    logger.info("📁 Использую файл сессии")
    client = TelegramClient("userbot_session", API_ID, API_HASH)
else:
    logger.error("❌ Не найдена сессия!")
    sys.exit(1)

# --- FLASK ---
app = Flask(__name__)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
client_ready = False
scanning_users = {}

# --- ЭНДПОИНТЫ ---
@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "running",
        "service": "Chat Parser",
        "client_ready": client_ready,
        "active_scans": len(scanning_users)
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "alive", "client_ready": client_ready}), 200

# --- ФУНКЦИЯ ДЛЯ ПРОВЕРКИ, БОТ ЛИ ЭТО ---
def is_bot_user(user):
    if hasattr(user, 'is_bot'):
        return user.is_bot
    if hasattr(user, 'bot'):
        return user.bot is not None
    if hasattr(user, 'bot_info'):
        return user.bot_info is not None
    return False

# --- ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ ИМЕНИ (БЕЗОПАСНО) ---
def get_user_name(user):
    try:
        if hasattr(user, 'first_name'):
            name = user.first_name or ''
            if hasattr(user, 'last_name') and user.last_name:
                name += f" {user.last_name}"
            return name.strip()
    except:
        pass
    return None

# --- ГЛАВНАЯ ФУНКЦИЯ ПОИСКА ПОЛЬЗОВАТЕЛЕЙ ---
async def find_users_in_chat(entity):
    """Ищет пользователей по сообщениям и комментариям"""
    global client
    
    users = []
    seen_ids = set()
    
    try:
        # 1. Ищем по сообщениям в чате
        logger.info("🔍 Ищу пользователей по сообщениям...")
        async for message in client.iter_messages(entity, limit=500):
            if not message.sender:
                continue
            
            user = message.sender
            user_id = user.id
            
            # Пропускаем ботов и каналы
            if is_bot_user(user):
                continue
            
            # Пропускаем если нет юзернейма
            if not hasattr(user, 'username') or not user.username:
                continue
            
            if user_id not in seen_ids:
                seen_ids.add(user_id)
                users.append(user)
                logger.info(f"   Найден: @{user.username}")
                if len(users) >= MAX_USERS:
                    return users
        
        # 2. Если не хватило, ищем по комментариям (для каналов)
        if len(users) < MAX_USERS:
            logger.info("🔍 Ищу пользователей по комментариям...")
            async for message in client.iter_messages(entity, limit=500, reply_to=0):
                if not message.sender:
                    continue
                
                user = message.sender
                user_id = user.id
                
                if is_bot_user(user):
                    continue
                
                if not hasattr(user, 'username') or not user.username:
                    continue
                
                if user_id not in seen_ids:
                    seen_ids.add(user_id)
                    users.append(user)
                    logger.info(f"   Найден (комментарий): @{user.username}")
                    if len(users) >= MAX_USERS:
                        return users
        
        return users
        
    except FloodWaitError as e:
        wait = e.seconds
        logger.warning(f"⏳ FloodWait: {wait} сек")
        await asyncio.sleep(wait + 1)
        return await find_users_in_chat(entity)
    except Exception as e:
        logger.error(f"❌ Ошибка поиска: {e}")
        return users

# --- ОБРАБОТЧИК СООБЩЕНИЙ ---
@client.on(events.NewMessage)
async def handler(event):
    global scanning_users
    
    try:
        if not event.is_private:
            return
        
        user_id = event.sender_id
        text = event.message.text
        
        if not text:
            return
        
        logger.info(f"📩 Сообщение от {user_id}: {text[:50]}...")
        
        # --- КОМАНДА /help ---
        if text.lower() in ['/help', 'помощь']:
            await event.reply(
                "🤖 **Помощь**\n\n"
                "Отправь ссылку на чат или канал:\n"
                "`t.me/gift_chat`\n"
                "`@gift_chat`\n\n"
                "Я найду 10 пользователей с юзернеймами."
            )
            return
        
        # --- ПРОВЕРЯЕМ, НЕ ССЫЛКА ЛИ ЭТО ---
        chat_input = text.strip()
        
        if 't.me/' in chat_input:
            chat_username = chat_input.split('t.me/')[-1].strip('/')
        else:
            chat_username = chat_input.strip('/')
        
        if chat_username.startswith('@'):
            chat_username = chat_username[1:]
        
        if ' ' in chat_username or len(chat_username) < 3:
            await event.reply(
                "❌ Это не похоже на ссылку на чат.\n\n"
                "Отправь ссылку в формате:\n"
                "`t.me/gift_chat`\n"
                "или `@gift_chat`"
            )
            return
        
        if user_id in scanning_users and scanning_users[user_id].get("status") == "active":
            await event.reply("⏳ Уже идет сканирование. Дождись завершения.")
            return
        
        # --- НАЧИНАЕМ СКАНИРОВАНИЕ ---
        scanning_users[user_id] = {"status": "active", "chat": chat_username}
        
        await event.reply(
            f"🔍 Ищу чат: @{chat_username}\n"
            f"⏳ Это может занять несколько секунд..."
        )
        
        try:
            # --- 1. ПОЛУЧАЕМ ЧАТ ---
            try:
                entity = await client.get_entity(chat_username)
            except Exception as e:
                await event.reply(f"❌ Не могу найти чат: {e}")
                scanning_users.pop(user_id, None)
                return
            
            try:
                chat_name = entity.title
                await event.reply(f"✅ Найден чат: {chat_name}")
            except:
                await event.reply(f"✅ Чат найден (ID: {entity.id})")
            
            # --- 2. ИЩЕМ ПОЛЬЗОВАТЕЛЕЙ ---
            await event.reply("👥 Ищу пользователей по сообщениям...")
            await event.reply("⏳ Это может занять время...")
            
            all_users = await find_users_in_chat(entity)
            
            # --- 3. ФОРМИРУЕМ ОТВЕТ ---
            if not all_users:
                await event.reply(
                    "❌ В чате не найдено пользователей с юзернеймами.\n\n"
                    "Возможные причины:\n"
                    "• В чате нет сообщений\n"
                    "• Все пользователи без юзернеймов\n"
                    "• Бот не может прочитать сообщения (нужны права)"
                )
                scanning_users.pop(user_id, None)
                return
            
            # Формируем список
            lines = []
            lines.append(f"✅ Найдено пользователей с юзернеймами: {len(all_users)}")
            lines.append("")
            lines.append(f"📋 Первые {min(MAX_USERS, len(all_users))} юзернеймов:")
            lines.append("")
            
            for i, user in enumerate(all_users[:MAX_USERS], 1):
                username = f"@{user.username}"
                name = get_user_name(user)
                
                if name:
                    lines.append(f"{i:2}. {username} — {name}")
                else:
                    lines.append(f"{i:2}. {username}")
            
            if len(all_users) > MAX_USERS:
                lines.append("")
                lines.append(f"... и еще {len(all_users) - MAX_USERS} пользователей")
            
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━")
            lines.append("💡 Можешь скопировать эти юзернеймы")
            
            await event.reply("\n".join(lines))
            
        except FloodWaitError as e:
            wait = e.seconds
            await event.reply(f"⏳ Telegram просит подождать {wait} секунд")
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            logger.error(traceback.format_exc())
            await event.reply(f"❌ Ошибка: {str(e)[:200]}")
        
        finally:
            scanning_users.pop(user_id, None)
        
    except Exception as e:
        logger.error(f"❌ Ошибка в handler: {e}")
        logger.error(traceback.format_exc())
        try:
            await event.reply(f"❌ Ошибка: {str(e)[:100]}")
        except:
            pass

# --- ЗАПУСК TELEGRAM ---
def start_telethon():
    global client_ready
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        logger.info("🚀 Запуск Telethon...")
        client.start()
        logger.info("✅ Telethon запущен")
        
        me = loop.run_until_complete(client.get_me())
        logger.info(f"👤 Аккаунт: @{me.username}")
        client_ready = True
        
        client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"❌ Ошибка Telethon: {e}")
        logger.error(traceback.format_exc())
        client_ready = False

# --- ГЛАВНЫЙ ЗАПУСК ---
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК ПАРСЕРА ЧАТОВ")
    logger.info("=" * 60)
    logger.info("📌 Отправь боту ссылку на чат")
    logger.info("📌 Например: t.me/gift_chat")
    logger.info("=" * 60)
    
    import threading
    telethon_thread = threading.Thread(target=start_telethon, daemon=True)
    telethon_thread.start()
    
    time.sleep(3)
    logger.info(f"🌐 Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
