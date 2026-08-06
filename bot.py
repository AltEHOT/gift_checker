import os
import sys
import time
import random
import logging
import threading
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

# --- ПРОВЕРКА API ДАННЫХ ---
if not API_ID or not API_HASH:
    logger.error("❌ API_ID и API_HASH не установлены в переменных окружения!")
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

@app.route('/stats', methods=['GET'])
def stats():
    active = []
    for user_id, data in user_data.items():
        if data.get("status") == "active":
            total = len(data["usernames"])
            current = data.get("index", 0)
            active.append({
                "user_id": user_id,
                "progress": f"{current}/{total}",
                "gifts_found": data.get('total_gifts', 0)
            })
    return jsonify({
        "active_checks": active,
        "finished": len([u for u in user_data.items() if u[1].get("status") == "finished"])
    })

# --- ЛОГИКА TELEGRAM ---

def can_make_request():
    global request_timestamps
    now = time.time()
    request_timestamps = [t for t in request_timestamps if now - t < 60]
    if len(request_timestamps) >= 20:
        wait_time = 60 - (now - request_timestamps[0])
        return False, wait_time
    return True, 0

async def check_user_gifts(username):
    global client, request_timestamps
    try:
        can_request, wait_time = can_make_request()
        if not can_request:
            await asyncio.sleep(wait_time)
            return await check_user_gifts(username)
        
        # ПОЛУЧАЕМ ПОЛЬЗОВАТЕЛЯ ПО ЮЗЕРНЕЙМУ
        try:
            entity = await client.get_entity(username)
        except ValueError as e:
            return None, f"Пользователь {username} не найден: {e}"
        except Exception as e:
            return None, f"Ошибка получения пользователя {username}: {e}"
        
        # ЗАПРАШИВАЕМ ПОДАРКИ
        try:
            result = await client(functions.payments.GetSavedStarGiftsRequest(
                peer=entity,
                exclude_unsaved=True,
                exclude_saved=False,
                exclude_upgradable=False,
                exclude_unupgradable=True
            ))
        except RPCError as e:
            return None, f"RPC ошибка: {e}"
        except Exception as e:
            return None, f"Ошибка запроса подарков: {e}"
        
        # СЧИТАЕМ НЕУЛУЧШЕННЫЕ
        upgradable_count = 0
        if result and result.gifts:
            for gift in result.gifts:
                if gift.can_upgrade:
                    upgradable_count += 1
        
        request_timestamps.append(time.time())
        return upgradable_count, None
        
    except FloodWaitError as e:
        wait_time = e.seconds
        logger.warning(f"⏳ FloodWait: {wait_time} сек")
        await asyncio.sleep(min(wait_time, 60))
        if wait_time < 60:
            return await check_user_gifts(username)
        return None, f"FloodWait: {wait_time} сек"
    except Exception as e:
        logger.error(f"❌ Ошибка проверки {username}: {e}")
        logger.error(traceback.format_exc())
        return None, str(e)

async def process_batch_async(chat_id, user_id):
    global client, user_data
    data = user_data.get(user_id)
    if not data:
        return
    
    usernames = data["usernames"]
    total = len(usernames)
    
    for index, username in enumerate(usernames):
        if data.get("status") == "stopped":
            await client.send_message(chat_id, "⏹️ Проверка остановлена.")
            break
        
        data["index"] = index
        if index > 0 and index % 50 == 0:
            await client.send_message(chat_id, f"⏸️ Пауза 30 сек (обработано {index}/{total})")
            await asyncio.sleep(30)
        
        progress_msg = await client.send_message(
            chat_id,
            f"⏳ {index + 1}/{total}\n🔄 Проверяю {username}..."
        )
        result, error = await check_user_gifts(username)
        
        if error:
            await client.send_message(chat_id, f"❌ **{username}**\n{error}")
        else:
            if result > 0:
                data['total_gifts'] = data.get('total_gifts', 0) + result
                await client.send_message(chat_id, f"✅ **{username}**\n📦 Неулучшенных: **{result}**")
            else:
                await client.send_message(chat_id, f"ℹ️ **{username}**\n📦 Неулучшенных: **0**")
        
        try:
            await progress_msg.delete()
        except:
            pass
        await asyncio.sleep(random.uniform(2, 5))
    
    if data.get("status") != "stopped":
        total_time = int(time.time() - data["start_time"])
        avg_time = total_time / total if total > 0 else 0
        await client.send_message(
            chat_id,
            f"✅ **Проверка завершена!**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📊 Всего: **{total}** аккаунтов\n"
            f"⏱ Время: **{total_time}** сек\n"
            f"📈 Среднее: **{avg_time:.1f}** сек/аккаунт\n"
            f"🎁 Найдено подарков: **{data.get('total_gifts', 0)}**"
        )
    data["status"] = "finished"

def run_batch_sync(chat_id, user_id):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(process_batch_async(chat_id, user_id))
    except Exception as e:
        logger.error(f"❌ Ошибка в run_batch_sync: {e}")
        logger.error(traceback.format_exc())

async def handle_new_message(event):
    global client, user_data
    try:
        if not event.is_private:
            return
        
        user_id = event.sender_id
        chat_id = event.chat_id
        text = event.message.text
        
        if not text:
            return
        
        logger.info(f"📩 Сообщение от {user_id}: {text[:50]}...")
        
        # --- КОМАНДЫ ---
        if text.startswith('/'):
            if text.lower() in ["/stop", "стоп"]:
                if user_id in user_data:
                    user_data[user_id]["status"] = "stopped"
                    await client.send_message(chat_id, "⏹️ Проверка остановлена.")
                return
            
            if text.lower() in ["/stats", "статистика"]:
                if user_id in user_data and user_data[user_id].get("status") == "active":
                    data = user_data[user_id]
                    total = len(data["usernames"])
                    current = data.get("index", 0)
                    await client.send_message(
                        chat_id,
                        f"📊 **Прогресс:** {current}/{total}\n🎁 Найдено: {data.get('total_gifts', 0)}"
                    )
                else:
                    await client.send_message(chat_id, "ℹ️ Нет активной проверки.")
                return
            
            if text.lower() in ["/help", "помощь"]:
                await client.send_message(
                    chat_id,
                    "🤖 **Помощь**\n\nОтправь список @username\nФормат: @username1 - 1\n\nКоманды:\n/stop - остановить\n/stats - прогресс\n/help - справка"
                )
                return
            return
        
        # --- ПАРСИНГ СПИСКА ---
        if user_id in user_data and user_data[user_id].get("status") == "active":
            data = user_data[user_id]
            await client.send_message(
                chat_id,
                f"⏳ Уже идет проверка: {data['index']}/{len(data['usernames'])}"
            )
            return
        
        lines = text.split('\n')
        usernames = []
        for line in lines:
            if '@' in line:
                for sep in [' - ', '—', ' -', '- ', '\t']:
                    if sep in line:
                        username = line.split(sep)[0].strip()
                        break
                else:
                    username = line.strip()
                if username.startswith('@'):
                    usernames.append(username)
        
        if not usernames:
            await client.send_message(
                chat_id, 
                "❌ Не найдено @username\n\nОтправь список в формате:\n@username1 - 1\n@username2 - 2"
            )
            return
        
        if len(usernames) > 200:
            await client.send_message(
                chat_id, 
                f"⚠️ Слишком много аккаунтов ({len(usernames)})\nМаксимум: 200 за раз"
            )
            return
        
        user_data[user_id] = {
            "usernames": usernames,
            "index": 0,
            "status": "active",
            "start_time": time.time(),
            "total_gifts": 0,
            "chat_id": chat_id
        }
        
        await client.send_message(
            chat_id,
            f"✅ Получено {len(usernames)} аккаунтов.\n⏱ ~{len(usernames) * 3} сек\n🛡️ Защита от флуда: ВКЛ\n━━━━━━━━━━━━━━━━━\nДля остановки: /stop\nДля статистики: /stats"
        )
        
        thread = threading.Thread(target=run_batch_sync, args=(chat_id, user_id))
        thread.daemon = True
        thread.start()
        
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_new_message: {e}")
        logger.error(traceback.format_exc())
        try:
            if 'chat_id' in locals():
                await client.send_message(chat_id, f"❌ Внутренняя ошибка: {str(e)[:100]}")
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
        
        @client.on(events.NewMessage)
        async def message_handler(event):
            try:
                await handle_new_message(event)
            except Exception as e:
                logger.error(f"❌ Ошибка в message_handler: {e}")
                logger.error(traceback.format_exc())
                try:
                    await client.send_message(event.chat_id, f"❌ Ошибка: {str(e)[:100]}")
                except:
                    pass
        
        client.run_until_disconnected()
    except Exception as e:
        logger.error(f"❌ Ошибка Telethon: {e}")
        logger.error(traceback.format_exc())
        client_ready = False

# --- ГЛАВНЫЙ ЗАПУСК ---
if __name__ == "__main__":
    logger.info("🚀 ЗАПУСК СЕРВИСА GIFT CHECKER")
    logger.info(f"📊 API_ID: {API_ID}")
    logger.info(f"🌐 Порт: {PORT}")
    
    telethon_thread = threading.Thread(target=start_telethon, daemon=True)
    telethon_thread.start()
    
    time.sleep(3)
    logger.info(f"🌐 Запуск Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
