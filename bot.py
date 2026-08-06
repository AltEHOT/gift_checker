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

# --- ФУНКЦИЯ ДЛЯ ПРОВЕРКИ, ЧТО ЭТО ЮЗЕРНЕЙМ ---
def is_valid_username(text):
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    return re.match(r'^@[A-Za-z0-9_]{3,}$', text) is not None

# --- ПРОВЕРКА ПОДАРКОВ (ИСПРАВЛЕННАЯ) ---
async def check_gifts(username):
    global client, request_timestamps
    
    try:
        username = str(username).strip()
        if username.startswith('@'):
            username = username[1:]
        
        if not username or username.isdigit():
            return None, "Невалидный username"
        
        logger.info(f"🔍 Проверяю: {username}")
        
        # --- ПОЛУЧАЕМ InputPeer (ГАРАНТИРОВАННО РАБОТАЕТ) ---
        try:
            input_peer = await client.get_input_entity(username)
        except Exception as e:
            logger.error(f"❌ Ошибка получения {username}: {e}")
            return None, f"Не найден"
        
        # --- ПРАВИЛЬНЫЙ ВЫЗОВ ---
        try:
            result = await client(functions.payments.GetSavedStarGiftsRequest(
                peer=input_peer,
                offset=0,
                limit=100,
                exclude_unsaved=True,
                exclude_saved=False,
                exclude_upgradable=False,
                exclude_unupgradable=True
            ))
        except FloodWaitError as e:
            wait = e.seconds
            logger.warning(f"⏳ FloodWait {wait} сек для {username}")
            await asyncio.sleep(wait + 1)
            return await check_gifts(username)
        except Exception as e:
            logger.error(f"❌ Ошибка GetSavedStarGifts для {username}: {e}")
            return None, f"Ошибка API: {str(e)[:50]}"
        
        # Считаем неулучшенные
        count = 0
        if result and result.gifts:
            for gift in result.gifts:
                if gift.can_upgrade:
                    count += 1
        
        request_timestamps.append(time.time())
        logger.info(f"✅ {username}: {count} подарков")
        return count, None
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки {username}: {e}")
        logger.error(traceback.format_exc())
        return None, str(e)[:50]

# --- ФУНКЦИЯ ДЛЯ ФОРМИРОВАНИЯ ОТЧЕТА ---
def format_report(results, total_time, total_gifts):
    lines = []
    lines.append("✅ **ПРОВЕРКА ЗАВЕРШЕНА!**")
    lines.append("━━━━━━━━━━━━━━━━━")
    
    sorted_results = sorted(results, key=lambda x: x[1] if x[1] is not None else -1, reverse=True)
    
    for username, count, error in sorted_results:
        if error:
            lines.append(f"❌ {username}: {error}")
        elif count is None:
            lines.append(f"❌ {username}: ошибка")
        elif count > 0:
            lines.append(f"✅ {username}: **{count}** 🎁")
        else:
            lines.append(f"ℹ️ {username}: 0")
    
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 Всего проверено: **{len(results)}** аккаунтов")
    lines.append(f"⏱ Время: **{total_time}** сек")
    lines.append(f"🎁 Найдено подарков: **{total_gifts}**")
    
    return "\n".join(lines)

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
                        f"📊 Прогресс: {current}/{total}\n"
                        f"🎁 Найдено: {data.get('total_gifts', 0)}"
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
        
        lines = text.strip().split('\n')
        usernames = []
        for line in lines:
            if '@' in line:
                for sep in [' - ', '—', ' -', '- ', '\t', ' ']:
                    if sep in line:
                        username = line.split(sep)[0].strip()
                        break
                else:
                    username = line.strip()
                
                if is_valid_username(username):
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
        
        # Сохраняем данные
        user_data[user_id] = {
            "usernames": usernames,
            "index": 0,
            "status": "active",
            "start_time": time.time(),
            "total_gifts": 0,
            "results": []
        }
        
        await event.reply(
            f"✅ Получено {len(usernames)} аккаунтов.\n"
            f"⏱ ~{len(usernames) * 3} сек\n"
            f"Команда /stop для остановки"
        )
        
        # --- ЗАПУСК ПРОВЕРКИ ---
        data = user_data[user_id]
        total = len(usernames)
        results = []
        
        for index, username in enumerate(usernames):
            if data.get("status") == "stopped":
                await event.reply("⏹️ Остановлено.")
                break
            
            data["index"] = index
            
            if index > 0 and index % 50 == 0:
                await event.reply(f"⏸️ Пауза 30 сек ({index}/{total})")
                await asyncio.sleep(30)
            
            if index % 10 == 0 or index == total - 1:
                await event.reply(f"⏳ {index + 1}/{total} - проверяю...")
            
            count, error = await check_gifts(username)
            
            if error:
                results.append((username, None, error))
            else:
                if count and count > 0:
                    data['total_gifts'] = data.get('total_gifts', 0) + count
                results.append((username, count, None))
            
            await asyncio.sleep(random.uniform(2, 4))
        
        # --- ОТПРАВЛЯЕМ ФИНАЛЬНЫЙ ОТЧЕТ ---
        if data.get("status") != "stopped":
            total_time = int(time.time() - data["start_time"])
            report = format_report(results, total_time, data.get('total_gifts', 0))
            
            if len(report) > 4000:
                parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
                for part in parts:
                    await event.reply(part)
            else:
                await event.reply(report)
        else:
            await event.reply("⏹️ Проверка остановлена.")
        
        data["status"] = "finished"
        
    except FloodWaitError as e:
        wait = e.seconds
        logger.warning(f"⏳ FloodWait: {wait} сек")
        await asyncio.sleep(wait + 1)
        await handler(event)
    except Exception as e:
        logger.error(f"❌ Ошибка в handler: {e}")
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
