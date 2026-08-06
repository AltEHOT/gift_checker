import os
import sys
import time
import random
import logging
import asyncio
import base64
import traceback
from flask import Flask, jsonify
from telethon import TelegramClient, events, functions
from telethon.errors import FloodWaitError, RPCError
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
user_data = {}
request_timestamps = []
client_ready = False

# --- ЭНДПОИНТЫ ---
@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "running",
        "service": "Gift Checker",
        "client_ready": client_ready,
        "active_checks": len([u for u in user_data.values() if u.get("status") == "active"])
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "alive", "client_ready": client_ready}), 200

# --- ПРОВЕРКА ПОДАРКОВ ---
async def check_gifts(username):
    """Проверяет неулучшенные подарки у пользователя"""
    global client, request_timestamps
    
    try:
        # ПРИВОДИМ К СТРОКЕ И УБИРАЕМ @
        username = str(username).strip()
        if username.startswith('@'):
            username = username[1:]
        
        if not username:
            return None, "Пустой username"
        
        logger.info(f"🔍 Проверяю: {username}")
        
        # Получаем пользователя
        try:
            entity = await client.get_entity(username)
        except Exception as e:
            return None, f"Не найден: {str(e)[:30]}"
        
        # Запрашиваем подарки
        try:
            result = await client(functions.payments.GetSavedStarGiftsRequest(
                peer=entity,
                offset=0,
                limit=100,
                exclude_unsaved=True,
                exclude_saved=False,
                exclude_upgradable=False,
                exclude_unupgradable=True
            ))
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
            return await check_gifts(username)
        except Exception as e:
            return None, f"Ошибка API: {str(e)[:30]}"
        
        # Считаем неулучшенные
        count = 0
        if result and result.gifts:
            for gift in result.gifts:
                if gift.can_upgrade:
                    count += 1
        
        request_timestamps.append(time.time())
        return count, None
        
    except Exception as e:
        logger.error(f"❌ Ошибка {username}: {e}")
        return None, str(e)[:50]

# --- ОБРАБОТЧИК СООБЩЕНИЙ ---
@client.on(events.NewMessage)
async def handler(event):
    global user_data
    
    try:
        if not event.is_private:
            return
        
        user_id = event.sender_id
        text = event.message.text
        
        if not text:
            return
        
        logger.info(f"📩 Сообщение от {user_id}")
        
        # --- КОМАНДЫ ---
        if text.startswith('/'):
            if text.lower() in ["/stop", "стоп"]:
                if user_id in user_data:
                    user_data[user_id]["status"] = "stopped"
                    await event.reply("⏹️ Остановлено.")
                return
            
            if text.lower() in ["/stats", "статистика"]:
                if user_id in user_data and user_data[user_id].get("status") == "active":
                    data = user_data[user_id]
                    total = len(data["usernames"])
                    current = data.get("index", 0)
                    await event.reply(
                        f"📊 Прогресс: {current}/{total}\n🎁 Найдено: {data.get('total_gifts', 0)}"
                    )
                else:
                    await event.reply("ℹ️ Нет активной проверки.")
                return
            
            if text.lower() in ["/help", "помощь"]:
                await event.reply(
                    "🤖 **Помощь**\n\n"
                    "Отправь список @username\n"
                    "Например:\n"
                    "@sirkapirkaw - 1\n"
                    "@sofuuha - 2\n\n"
                    "Команды:\n"
                    "/stop - остановить\n"
                    "/stats - прогресс"
                )
                return
            return
        
        # --- ПАРСИНГ СПИСКА ---
        if user_id in user_data and user_data[user_id].get("status") == "active":
            data = user_data[user_id]
            await event.reply(
                f"⏳ Уже идет проверка: {data['index']}/{len(data['usernames'])}"
            )
            return
        
        # Извлекаем все @username
        lines = text.strip().split('\n')
        usernames = []
        for line in lines:
            if '@' in line:
                # Берем часть до разделителя
                for sep in [' - ', '—', ' -', '- ', '\t', ' ']:
                    if sep in line:
                        username = line.split(sep)[0].strip()
                        break
                else:
                    username = line.strip()
                
                # ПРИВОДИМ К СТРОКЕ И ПРОВЕРЯЕМ
                username = str(username).strip()
                if username.startswith('@') and len(username) > 1:
                    usernames.append(username)
        
        if not usernames:
            await event.reply(
                "❌ Не найдено @username\n\n"
                "Отправь список:\n"
                "@username1 - 1\n"
                "@username2 - 2"
            )
            return
        
        if len(usernames) > 200:
            await event.reply(
                f"⚠️ Слишком много ({len(usernames)}). Максимум 200."
            )
            return
        
        # Сохраняем
        user_data[user_id] = {
            "usernames": usernames,
            "index": 0,
            "status": "active",
            "start_time": time.time(),
            "total_gifts": 0
        }
        
        await event.reply(
            f"✅ Получено {len(usernames)} аккаунтов.\n"
            f"⏱ ~{len(usernames) * 3} сек\n"
            f"Команда /stop для остановки"
        )
        
        # --- ЗАПУСК ПРОВЕРКИ ---
        data = user_data[user_id]
        total = len(usernames)
        
        for index, username in enumerate(usernames):
            if data.get("status") == "stopped":
                await event.reply("⏹️ Остановлено.")
                break
            
            data["index"] = index
            
            if index > 0 and index % 50 == 0:
                await event.reply(f"⏸️ Пауза 30 сек ({index}/{total})")
                await asyncio.sleep(30)
            
            await event.reply(f"⏳ {index + 1}/{total} - {username}")
            
            count, error = await check_gifts(username)
            
            if error:
                await event.reply(f"❌ {username}\n{error}")
            else:
                if count > 0:
                    data['total_gifts'] = data.get('total_gifts', 0) + count
                    await event.reply(f"✅ {username}\n📦 Неулучшенных: **{count}**")
                else:
                    await event.reply(f"ℹ️ {username}\n📦 Неулучшенных: **0**")
            
            await asyncio.sleep(random.uniform(2, 4))
        
        # Финал
        if data.get("status") != "stopped":
            total_time = int(time.time() - data["start_time"])
            await event.reply(
                f"✅ **Проверка завершена!**\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"📊 Всего: **{total}** аккаунтов\n"
                f"⏱ Время: **{total_time}** сек\n"
                f"🎁 Найдено: **{data.get('total_gifts', 0)}**"
            )
        
        data["status"] = "finished"
        
    except FloodWaitError as e:
        wait = e.seconds
        logger.warning(f"⏳ FloodWait: {wait} сек")
        await asyncio.sleep(wait + 1)
        # Пробуем перезапустить обработку
        await handler(event)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        logger.error(traceback.format_exc())
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
        logger.error(traceback.format_exc())
        client_ready = False

# --- ГЛАВНЫЙ ЗАПУСК ---
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК ЮЗЕРБОТА")
    logger.info("=" * 60)
    
    import threading
    telethon_thread = threading.Thread(target=start_telethon, daemon=True)
    telethon_thread.start()
    
    time.sleep(3)
    logger.info(f"🌐 Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
