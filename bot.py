import os
import sys
import time
import random
import logging
import threading
import asyncio
import base64
from flask import Flask, jsonify
from telethon import TelegramClient, events, functions
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

# --- ПРОВЕРКА API ДАННЫХ ---
if not API_ID or not API_HASH:
    logger.error("❌ API_ID и API_HASH не установлены в переменных окружения!")
    logger.error("Добавь их на Render в Environment Variables")
    sys.exit(1)

# --- ПОДГОТОВКА СЕССИИ (ПРИОРИТЕТЫ) ---

# 1. ПРОВЕРЯЕМ SESSION_STRING (из переменных окружения)
if SESSION_STRING and SESSION_STRING not in ["None", "NONE", "none", ""]:
    logger.info("🔑 Использую StringSession из переменной окружения")
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# 2. ПРОВЕРЯЕМ ФАЙЛ session.b64 (из Secret Files)
elif os.path.exists("session.b64"):
    logger.info("📁 Найден session.b64, декодирую в userbot_session.session...")
    try:
        with open("session.b64", "r") as f:
            b64_data = f.read().strip()
        
        # Декодируем Base64 в бинарный файл сессии
        with open("userbot_session.session", "wb") as f:
            f.write(base64.b64decode(b64_data))
        
        logger.info("✅ Сессия успешно декодирована из Base64")
        client = TelegramClient("userbot_session", API_ID, API_HASH)
    except Exception as e:
        logger.error(f"❌ Ошибка декодирования session.b64: {e}")
        logger.error("Проверь, что в session.b64 правильная Base64 строка")
        sys.exit(1)

# 3. ПРОВЕРЯЕМ ОБЫЧНЫЙ ФАЙЛ СЕССИИ
elif os.path.exists("userbot_session.session"):
    logger.info("📁 Использую файл сессии userbot_session.session")
    client = TelegramClient("userbot_session", API_ID, API_HASH)

# 4. НЕТ СЕССИИ
else:
    logger.error("❌ Не найдена сессия!")
    logger.error("Добавь один из вариантов:")
    logger.error("  1. SESSION_STRING в переменные окружения")
    logger.error("  2. session.b64 в Secret Files")
    logger.error("  3. userbot_session.session в Secret Files")
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
        "service": "Gift Checker (Telethon)",
        "client_ready": client_ready,
        "active_checks": len([u for u in user_data.values() if u.get("status") == "active"]),
        "total_checks": len(user_data)
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
    """Проверяет лимит запросов (20 в минуту)"""
    global request_timestamps
    now = time.time()
    request_timestamps = [t for t in request_timestamps if now - t < 60]
    if len(request_timestamps) >= 20:
        wait_time = 60 - (now - request_timestamps[0])
        return False, wait_time
    return True, 0

async def check_user_gifts(username):
    """Проверяет подарки пользователя"""
    global client, request_timestamps
    
    try:
        # Проверяем лимит
        can_request, wait_time = can_make_request()
        if not can_request:
            await asyncio.sleep(wait_time)
            return await check_user_gifts(username)
        
        # Получаем пользователя
        try:
            entity = await client.get_entity(username)
        except ValueError:
            return None, f"Пользователь {username} не найден"
        
        # Запрашиваем подарки
        result = await client(functions.payments.GetSavedStarGiftsRequest(
            peer=entity,
            exclude_unsaved=True,
            exclude_saved=False,
            exclude_upgradable=False,
            exclude_unupgradable=True
        ))
        
        # Считаем неулучшенные
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
        logger.error(f"❌ Ошибка {username}: {e}")
        return None, str(e)

async def process_batch_async(chat_id, user_id):
    """Асинхронная обработка списка"""
    global client, user_data
    
    data = user_data.get(user_id)
    if not data:
        return
    
    usernames = data["usernames"]
    total = len(usernames)
    
    for index, username in enumerate(usernames):
        # Проверяем, не остановлено ли
        if data.get("status") == "stopped":
            await client.send_message(chat_id, "⏹️ Проверка остановлена.")
            break
        
        data["index"] = index
        
        # Пауза между батчами (каждые 50 аккаунтов)
        if index > 0 and index % 50 == 0:
            await client.send_message(
                chat_id,
                f"⏸️ Пауза 30 сек (обработано {index}/{total})"
            )
            await asyncio.sleep(30)
        
        # Отправляем статус
        progress_msg = await client.send_message(
            chat_id,
            f"⏳ {index + 1}/{total}\n🔄 Проверяю {username}..."
        )
        
        # Проверяем подарки
        result, error = await check_user_gifts(username)
        
        # Отправляем результат
        if error:
            await client.send_message(chat_id, f"❌ **{username}**\n{error}")
        else:
            if result > 0:
                data['total_gifts'] = data.get('total_gifts', 0) + result
                await client.send_message(
                    chat_id, 
                    f"✅ **{username}**\n📦 Неулучшенных: **{result}**"
                )
            else:
                await client.send_message(
                    chat_id, 
                    f"ℹ️ **{username}**\n📦 Неулучшенных: **0**"
                )
        
        # Удаляем статусное сообщение
        try:
            await progress_msg.delete()
        except:
            pass
        
        # Задержка между запросами (2-5 секунд)
        await asyncio.sleep(random.uniform(2, 5))
    
    # Финальное сообщение
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
    """Синхронная обертка для запуска асинхронной обработки"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(process_batch_async(chat_id, user_id))

async def handle_new_message(event):
    """Обработчик новых сообщений"""
    global client, user_data
    
    # Только личные сообщения
    if not event.is_private:
        return
    
    user_id = event.sender_id
    chat_id = event.chat_id
    text = event.message.text
    
    if not text:
        return
    
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
                    f"📊 **Прогресс:** {current}/{total}\n"
                    f"🎁 Найдено: {data.get('total_gifts', 0)}"
                )
            else:
                await client.send_message(chat_id, "ℹ️ Нет активной проверки.")
            return
        
        if text.lower() in ["/help", "помощь"]:
            await client.send_message(
                chat_id,
                "🤖 **Помощь**\n\n"
                "Отправь список @username\n"
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
        await client.send_message(
            chat_id,
            f"⏳ Уже идет проверка: {data['index']}/{len(data['usernames'])}"
        )
        return
    
    # Извлекаем все @username из сообщения
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
            "❌ Не найдено @username\n\n"
            "Отправь список в формате:\n"
            "@username1 - 1\n"
            "@username2 - 2"
        )
        return
    
    if len(usernames) > 200:
        await client.send_message(
            chat_id, 
            f"⚠️ Слишком много аккаунтов ({len(usernames)})\n"
            f"Максимум: 200 за раз"
        )
        return
    
    # Сохраняем данные
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
        f"✅ Получено {len(usernames)} аккаунтов.\n"
        f"⏱ Примерное время: ~{len(usernames) * 3} сек\n"
        f"🛡️ Защита от флуда: ВКЛ\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Для остановки: /stop\n"
        f"Для статистики: /stats"
    )
    
    # Запускаем обработку в отдельном потоке
    thread = threading.Thread(
        target=run_batch_sync, 
        args=(chat_id, user_id)
    )
    thread.daemon = True
    thread.start()

# --- ЗАПУСК TELEGRAM (ИСПРАВЛЕННЫЙ) ---

def start_telethon():
    """Запускает Telethon клиент в отдельном потоке"""
    global client_ready
    
    # Создаем event loop для этого потока
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        logger.info("🚀 Запуск Telethon...")
        client.start()
        logger.info("✅ Telethon запущен")
        
        # ПРАВИЛЬНЫЙ АСИНХРОННЫЙ ВЫЗОВ get_me()
        me = loop.run_until_complete(client.get_me())
        
        logger.info(f"👤 Аккаунт: @{me.username}")
        logger.info(f"📱 ID: {me.id}")
        logger.info(f"📛 Имя: {me.first_name} {me.last_name or ''}")
        client_ready = True
        
        # Регистрируем обработчик сообщений
        @client.on(events.NewMessage)
        async def message_handler(event):
            await handle_new_message(event)
        
        # Блокируем поток
        client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"❌ Ошибка Telethon: {e}")
        client_ready = False

# --- ГЛАВНЫЙ ЗАПУСК ---

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК СЕРВИСА GIFT CHECKER")
    logger.info("=" * 60)
    logger.info(f"📊 API_ID: {API_ID}")
    logger.info(f"📊 API_HASH: {API_HASH[:10]}... (скрыто)")
    logger.info(f"🌐 Порт: {PORT}")
    logger.info("=" * 60)
    
    # Запускаем Telethon в фоне
    telethon_thread = threading.Thread(target=start_telethon, daemon=True)
    telethon_thread.start()
    
    # Даем время на инициализацию
    time.sleep(3)
    
    if client_ready:
        logger.info("✅ Сервис полностью готов к работе!")
    else:
        logger.warning("⚠️ Telethon еще не готов, но Flask запускается...")
    
    logger.info(f"🌐 Запуск Flask на порту {PORT}")
    logger.info("📊 Для проверки: /health, /stats")
    logger.info("=" * 60)
    
    # Запускаем Flask
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
