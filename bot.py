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

async def check_user_gifts(username):
    """Проверяет НЕУЛУЧШЕННЫЕ подарки у пользователя по @username"""
    global client, request_timestamps
    
    try:
        can_request, wait_time = can_make_request()
        if not can_request:
            await asyncio.sleep(wait_time)
            return await check_user_gifts(username)
        
        # 1. УБИРАЕМ @ ИЗ ЮЗЕРНЕЙМА (если есть)
        clean_username = username
        if clean_username.startswith('@'):
            clean_username = clean_username[1:]
        
        logger.info(f"🔍 Ищу пользователя: {clean_username}")
        
        # 2. ПОЛУЧАЕМ ПОЛЬЗОВАТЕЛЯ ПО ЮЗЕРНЕЙМУ (БЕЗ @)
        try:
            entity = await client.get_entity(clean_username)
        except ValueError as e:
            return None, f"Пользователь @{clean_username} не найден: {e}"
        except Exception as e:
            return None, f"Ошибка получения @{clean_username}: {e}"
        
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
        except RPCError as e:
            return None, f"Ошибка API: {e}"
        
        # 4. СЧИТАЕМ НЕУЛУЧШЕННЫЕ
        upgradable_count = 0
        if result and result.gifts:
            for gift in result.gifts:
                if gift.can_upgrade:
                    upgradable_count += 1
        
        request_timestamps.append(time.time())
        return upgradable_count, None
        
    except FloodWaitError as e:
        wait_time = e.seconds
        logger.warning(f"⏳ FloodWait: {wait_time} сек для {username}")
        await asyncio.sleep(min(wait_time, 60))
        if wait_time < 60:
            return await check_user_gifts(username)
        return None, f"FloodWait: {wait_time} сек"
        
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
    
    try:
        await event.reply(f"🚀 Начинаю проверку {total} аккаунтов...")
    except Exception as e:
        logger.error(f"❌ Не могу отправить стартовое: {e}")
    
    for index, username in enumerate(usernames):
        if data.get("status") == "stopped":
            await event.reply("⏹️ Проверка остановлена.")
            break
        
        data["index"] = index
        
        if index > 0 and index % 50 == 0:
            try:
                await event.reply(f"⏸️ Пауза 30 сек (обработано {index}/{total})")
            except:
                pass
            await asyncio.sleep(30)
        
        try:
            await event.reply(f"⏳ {index + 1}/{total} - Проверяю {username}...")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки прогресса: {e}")
        
        result, error = await check_user_gifts(username)
        
        if error:
            try:
                await event.reply(f"❌ **{username}**\n{error}")
            except:
                pass
        else:
            try:
                if result > 0:
                    data['total_gifts'] = data.get('total_gifts', 0) + result
                    await event.reply(
                        f"✅ **{username}**\n📦 Неулучшенных подарков: **{result}**"
                    )
                else:
                    await event.reply(
                        f"ℹ️ **{username}**\n📦 Неулучшенных подарков: **0**"
                    )
            except Exception as e:
                logger.error(f"❌ Ошибка отправки результата: {e}")
        
        await asyncio.sleep(random.uniform(2, 5))
    
    if data.get("status") != "stopped":
        total_time = int(time.time() - data["start_time"])
        avg_time = total_time / total if total > 0 else 0
        try:
            await event.reply(
                f"✅ **Проверка завершена!**\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"📊 Всего проверено: **{total}** аккаунтов\n"
                f"⏱ Время: **{total_time}** сек\n"
                f"📈 Среднее: **{avg_time:.1f}** сек/аккаунт\n"
                f"🎁 Найдено неулучшенных подарков: **{data.get('total_gifts', 0)}**"
            )
        except Exception as e:
            logger.error(f"❌ Ошибка отправки финала: {e}")
    
    data["status"] = "finished"

# --- ОБРАБОТЧИК СООБЩЕНИЙ ---
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
                    await event.reply("⏹️ Проверка остановлена.")
                return
            
            if text.lower() in ["/stats", "статистика"]:
                if user_id in user_data and user_data[user_id].get("status") == "active":
                    data = user_data[user_id]
                    total = len(data["usernames"])
                    current = data.get("index", 0)
                    await event.reply(
                        f"📊 **Прогресс:** {current}/{total}\n🎁 Найдено: {data.get('total_gifts', 0)}"
                    )
                else:
                    await event.reply("ℹ️ Нет активной проверки.")
                return
            
            if text.lower() in ["/help", "помощь"]:
                await event.reply(
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
            await event.reply(
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
            await event.reply(
                "❌ Не найдено @username\n\n"
                "Отправь список в формате:\n"
                "@username1 - 1\n"
                "@username2 - 2"
            )
            return
        
        if len(usernames) > 200:
            await event.reply(
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
        
        await event.reply(
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
            await event.reply(f"❌ Внутренняя ошибка: {str(e)[:100]}")
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
