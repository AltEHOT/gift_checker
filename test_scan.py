import os
import sys
import time
import random
import logging
import asyncio
import base64
import traceback
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

# --- ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ УЧАСТНИКОВ ---
async def get_users_from_group(entity):
    """Просто получает участников группы"""
    global client
    
    users = []
    
    try:
        async for user in client.iter_participants(entity):
            # Пропускаем если нет юзернейма
            if not user.username:
                continue
            users.append(user)
            if len(users) >= MAX_USERS:
                break
        
        return users, None
        
    except FloodWaitError as e:
        wait = e.seconds
        logger.warning(f"⏳ FloodWait: {wait} сек")
        await asyncio.sleep(wait + 1)
        return await get_users_from_group(entity)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return None, str(e)

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
                "Отправь ссылку на **ГРУППУ**:\n"
                "`t.me/gift_group`\n"
                "`@gift_group`\n\n"
                "⚠️ Только для групп! Каналы не поддерживаются."
            )
            return
        
        # --- ПАРСИМ ССЫЛКУ ---
        chat_input = text.strip()
        
        if 't.me/' in chat_input:
            chat_username = chat_input.split('t.me/')[-1].strip('/')
        else:
            chat_username = chat_input.strip('/')
        
        if chat_username.startswith('@'):
            chat_username = chat_username[1:]
        
        if ' ' in chat_username or len(chat_username) < 3:
            await event.reply(
                "❌ Это не похоже на ссылку.\n\n"
                "Отправь ссылку в формате:\n"
                "`t.me/gift_group`"
            )
            return
        
        if user_id in scanning_users and scanning_users[user_id].get("status") == "active":
            await event.reply("⏳ Уже идет сканирование.")
            return
        
        # --- НАЧИНАЕМ ---
        scanning_users[user_id] = {"status": "active"}
        
        await event.reply(f"🔍 Ищу группу: @{chat_username}...")
        
        try:
            # Получаем чат
            try:
                entity = await client.get_entity(chat_username)
            except Exception as e:
                await event.reply(f"❌ Не могу найти чат: {e}")
                scanning_users.pop(user_id, None)
                return
            
            # Проверяем, что это группа
            try:
                chat_title = entity.title
                await event.reply(f"✅ Найдена группа: {chat_title}")
            except:
                await event.reply(f"✅ Группа найдена (ID: {entity.id})")
            
            # Получаем участников
            await event.reply("👥 Получаю список участников...")
            
            users, error = await get_users_from_group(entity)
            
            if error:
                await event.reply(f"❌ Ошибка: {error}")
                scanning_users.pop(user_id, None)
                return
            
            if not users:
                await event.reply("❌ В группе нет пользователей с юзернеймами")
                scanning_users.pop(user_id, None)
                return
            
            # Формируем ответ
            lines = []
            lines.append(f"✅ Найдено пользователей с юзернеймами: {len(users)}")
            lines.append("")
            lines.append(f"📋 Первые {min(MAX_USERS, len(users))} юзернеймов:")
            lines.append("")
            
            for i, user in enumerate(users[:MAX_USERS], 1):
                username = f"@{user.username}"
                name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                
                if name:
                    lines.append(f"{i:2}. {username} — {name}")
                else:
                    lines.append(f"{i:2}. {username}")
            
            if len(users) > MAX_USERS:
                lines.append("")
                lines.append(f"... и еще {len(users) - MAX_USERS} пользователей")
            
            lines.append("")
            lines.append("💡 Скопируй эти юзернеймы")
            
            await event.reply("\n".join(lines))
            
        except FloodWaitError as e:
            await event.reply(f"⏳ Жди {e.seconds} секунд")
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            await event.reply(f"❌ Ошибка: {str(e)[:200]}")
        
        finally:
            scanning_users.pop(user_id, None)
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        try:
            await event.reply(f"❌ Ошибка: {str(e)[:100]}")
        except:
            pass

# --- ЗАПУСК ---
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
        client_ready = False

# --- ГЛАВНЫЙ ЗАПУСК ---
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК ПАРСЕРА ГРУПП")
    logger.info("=" * 60)
    logger.info("📌 Отправь ссылку на ГРУППУ")
    logger.info("📌 Например: t.me/gift_group")
    logger.info("=" * 60)
    
    import threading
    telethon_thread = threading.Thread(target=start_telethon, daemon=True)
    telethon_thread.start()
    
    time.sleep(3)
    logger.info(f"🌐 Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
