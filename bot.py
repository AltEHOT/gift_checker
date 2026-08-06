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

# --- ЛОГИКА ПРОВЕРКИ ПОДАРКОВ ---

def can_make_request():
    global request_timestamps
    now = time.time()
    request_timestamps = [t for t in request_timestamps if now - t < 60]
    if len(request_timestamps) >= 20:
        wait_time = 60 - (now - request_timestamps[0])
        return False, wait_time
    return True, 0

async def safe_send_message(chat, text, retry_count=0):
    """Безопасная отправка сообщения с обработкой FloodWait"""
    try:
        await client.send_message(chat, text)
        return True
    except FloodWaitError as e:
        wait_time = e.seconds
        logger.warning(f"⏳ FloodWait при отправке: {wait_time} сек")
        if retry_count < 3:
            await asyncio.sleep(wait_time + 1)
            return await safe_send_message(chat, text, retry_count + 1)
        else:
            logger.error(f"❌ Превышено количество попыток отправки")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        return False

async def check_user_gifts(username):
    """Проверяет НЕУЛУЧШЕННЫЕ подарки у пользователя по @username"""
    global client, request_timestamps
    
    try:
        # ПРОВЕРЯЕМ, ЧТО USERNAME — ЭТО СТРОКА
        if not isinstance(username, str):
            logger.warning(f"⚠️ Пропускаем {username} (не строка)")
            return None, "Неверный формат username"
        
        can_request, wait_time = can_make_request()
        if not can_request:
            await asyncio.sleep(wait_time)
            return await check_user_gifts(username)
        
        # 1. УБИРАЕМ @ ИЗ ЮЗЕРНЕЙМА
        clean_username = username
        if clean_username.startswith('@'):
            clean_username = clean_username[1:]
        
        logger.info(f"🔍 Ищу пользователя: {clean_username}")
        
        # 2. ПОЛУЧАЕМ ПОЛЬЗОВАТЕЛЯ ПО ЮЗЕРНЕЙМУ
        try:
            entity = await client.get_entity(clean_username)
        except ValueError as e:
            return None, f"Пользователь @{clean_username} не найден"
        except Exception as e:
            return None, f"Ошибка получения @{clean_username}: {str(e)[:50]}"
        
        # 3. ЗАПРАШИВАЕМ ЕГО ПОДАРКИ
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
            wait_time = e.seconds
            logger.warning(f"⏳ FloodWait для {username}: {wait_time} сек")
            await asyncio.sleep(wait_time + 1)
            return await check_user_gifts(username)
        except RPCError as e:
            return None, f"Ошибка API: {str(e)[:50]}"
        
        # 4. СЧИТАЕМ НЕУЛУЧШЕННЫЕ
        upgradable_count = 0
        if result and result.gifts:
            for gift in result.gifts:
                if gift.can_upgrade:
                    upgradable_count += 1
        
        request_timestamps.append(time.time())
        return upgradable_count, None
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки {username}: {e}")
        return None, str(e)

async def process_batch_async(event, user_id):
    """Обрабатывает список аккаунтов"""
    global client, user_data
    
    data = user_data.get(user_id)
    if not data:
        return
    
    usernames = data["usernames"]
    total = len(usernames)
    
    await safe_send_message(event.chat_id, f"🚀 Начинаю проверку {total} аккаунтов...")
    
    for index, username in enumerate(usernames):
        if data.get("status") == "stopped":
            await safe_send_message(event.chat_id, "⏹️ Проверка остановлена.")
            break
        
        data["index"] = index
        
        if index > 0 and index % 50 == 0:
            await safe_send_message(event.chat_id, f"⏸️ Пауза 30 сек (обработано {index}/{total})")
            await asyncio.sleep(30)
        
        # ПРОВЕРЯЕМ, ЧТО USERNAME НЕ ПУСТОЙ
        if not username or not isinstance(username, str):
            logger.warning(f"⚠️ Пропускаем пустой username: {username}")
            continue
        
        await safe_send_message(event.chat_id, f"⏳ {index + 1}/{total} - Проверяю {username}...")
        
        result, error = await check_user_gifts(username)
        
        if error:
            await safe_send_message(event.chat_id, f"❌ **{username}**\n{error}")
        else:
            if result > 0:
                data['total_gifts'] = data.get('total_gifts', 0) + result
                await safe_send_message(event.chat_id, f"✅ **{username}**\n📦 Неулучшенных подарков: **{result}**")
            else:
                await safe_send_message(event.chat_id, f"ℹ️ **{username}**\n📦 Неулучшенных подарков: **0**")
        
        await asyncio.sleep(random.uniform(2, 5))
    
    if data.get("status") != "stopped":
        total_time = int(time.time() - data["start_time"])
        avg_time = total_time / total if total > 0 else 0
        await safe_send_message(
            event.chat_id,
            f"✅ **Проверка завершена!**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📊 Всего проверено: **{total}** аккаунтов\n"
            f"⏱ Время: **{total_time}** сек\n"
            f"📈 Среднее: **{avg_time:.1f}** сек/аккаунт\n"
            f"🎁 Найдено неулучшенных подарков: **{data.get('total_gifts', 0)}**"
        )
    
    data["status"] = "finished"

async def handle_new_message(event):
    """Обработчик входящих сообщений"""
    global client, user_data
    
    try:
        if not event.is_private:
            return
        
        user_id = event.sender_id
        text = event.message.text
        
        if not text:
            return
        
        logger.info(f"📩 Сообщение от {user_id}: {text[:50]}...")
        
        # --- КОМАНДЫ ---
        if text.startswith('/'):
            if text.lower() in ["/stop", "стоп"]:
                if user_id in user_data:
                    user_data[user_id]["status"] = "stopped"
                    await safe_send_message(event.chat_id, "⏹️ Проверка остановлена.")
                return
            
            if text.lower() in ["/stats", "статистика"]:
                if user_id in user_data and user_data[user_id].get("status") == "active":
                    data = user_data[user_id]
                    total = len(data["usernames"])
                    current = data.get("index", 0)
                    await safe_send_message(
                        event.chat_id,
                        f"📊 **Прогресс:** {current}/{total}\n🎁 Найдено: {data.get('total_gifts', 0)}"
                    )
                else:
                    await safe_send_message(event.chat_id, "ℹ️ Нет активной проверки.")
                return
            
            if text.lower() in ["/help", "помощь"]:
                await safe_send_message(
                    event.chat_id,
                    "🤖 **Помощь**\n\n"
                    "Отправь список @username для проверки\n"
                    "Формат: @username1 - 1\n\n"
                    "Команды:\n"
                    "/stop - остановить проверку\n"
                    "/stats - показать прогресс\n"
                    "/help - эта справка"
                )
                return
            return
        
        # --- ПАРСИНГ СПИСКА ---
        if user_id in user_data and user_data[user_id].get("status") == "active":
            data = user_data[user_id]
            await safe_send_message(
                event.chat_id,
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
                if username.startswith('@') and len(username) > 1:
                    usernames.append(username)
        
        if not usernames:
            await safe_send_message(
                event.chat_id,
                "❌ Не найдено @username\n\n"
                "Отправь список в формате:\n"
                "@username1 - 1\n"
                "@username2 - 2"
            )
            return
        
        if len(usernames) > 200:
            await safe_send_message(
                event.chat_id,
                f"⚠️ Слишком много аккаунтов ({len(usernames)})\n"
                f"Максимум: 200 за раз"
            )
            return
        
        user_data[user_id] = {
            "usernames": usernames,
            "index": 0,
            "status": "active",
            "start_time": time.time(),
            "total_gifts": 0
        }
        
        await safe_send_message(
            event.chat_id,
            f"✅ Получено {len(usernames)} аккаунтов.\n"
            f"⏱ Примерное время: ~{len(usernames) * 3} сек\n"
            f"🛡️ Защита от флуда: ВКЛ\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Для остановки: /stop\n"
            f"Для статистики: /stats"
        )
        
        # Запускаем проверку
        await process_batch_async(event, user_id)
        
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_new_message: {e}")
        logger.error(traceback.format_exc())
        try:
            await safe_send_message(event.chat_id, f"❌ Внутренняя ошибка: {str(e)[:100]}")
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
        logger.info(f"📱 ID: {me.id}")
        client_ready = True
        
        @client.on(events.NewMessage)
        async def message_handler(event):
            try:
                await handle_new_message(event)
            except Exception as e:
                logger.error(f"❌ Ошибка в message_handler: {e}")
                logger.error(traceback.format_exc())
        
        client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"❌ Ошибка Telethon: {e}")
        logger.error(traceback.format_exc())
        client_ready = False

# --- ГЛАВНЫЙ ЗАПУСК ---
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК ЮЗЕРБОТА ДЛЯ ПРОВЕРКИ ПОДАРКОВ")
    logger.info("=" * 60)
    logger.info(f"📊 API_ID: {API_ID}")
    logger.info(f"🌐 Порт: {PORT}")
    logger.info("=" * 60)
    
    telethon_thread = threading.Thread(target=start_telethon, daemon=True)
    telethon_thread.start()
    
    time.sleep(3)
    logger.info(f"🌐 Запуск Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
