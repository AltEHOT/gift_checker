import os
import sys
import time
import random
import logging
import threading
from flask import Flask, jsonify

# --- НАСТРОЙКА ЛОГОВ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
SESSION_NAME = "userbot_session"
PORT = int(os.getenv("PORT", 5000))

# --- НАСТРОЙКИ АНТИ-ФЛУДА ---
MIN_DELAY = 2.0
MAX_DELAY = 5.0
MAX_REQUESTS_PER_MINUTE = 20
BATCH_SIZE = 50
BATCH_PAUSE = 30

if not API_ID or not API_HASH:
    logger.error("❌ API_ID и API_HASH не установлены!")
    sys.exit(1)

# --- FLASK ---
app = Flask(__name__)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
user_data = {}
request_timestamps = []
pyro_client = None
is_ready = False

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def can_make_request():
    global request_timestamps
    now = time.time()
    request_timestamps = [t for t in request_timestamps if now - t < 60]
    if len(request_timestamps) >= MAX_REQUESTS_PER_MINUTE:
        wait_time = 60 - (now - request_timestamps[0])
        return False, wait_time
    return True, 0

def get_delay():
    return random.uniform(MIN_DELAY, MAX_DELAY)

# --- ИНИЦИАЛИЗАЦИЯ PYROGRAM ---

def init_pyrogram():
    global pyro_client
    from pyrogram import Client
    from pyrogram.enums import ChatType
    from pyrogram.errors import FloodWait, RPCError
    from pyrogram.raw.functions.payments import GetSavedStarGifts
    
    try:
        pyro_client = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            workdir=".",
            sleep_threshold=30,
            no_updates=True,
            in_memory=False
        )
        logger.info("✅ Клиент Pyrogram создан")
        return pyro_client
    except Exception as e:
        logger.error(f"❌ Ошибка создания клиента: {e}")
        return None

# --- ОСНОВНАЯ ЛОГИКА ---

def process_account_sync(username, chat_id, user_id):
    global pyro_client, request_timestamps
    
    if not pyro_client:
        return None, "Клиент не инициализирован"
    
    try:
        can_request, wait_time = can_make_request()
        if not can_request:
            time.sleep(wait_time)
            return process_account_sync(username, chat_id, user_id)
        
        entity = pyro_client.get_users(username)
        peer = pyro_client.resolve_peer(entity.id)
        
        from pyrogram.raw.functions.payments import GetSavedStarGifts
        gifts_result = pyro_client.invoke(
            GetSavedStarGifts(
                peer=peer,
                exclude_unsaved=True,
                exclude_saved=False,
                exclude_upgradable=False,
                exclude_unupgradable=True
            )
        )
        
        upgradable_count = 0
        if gifts_result and hasattr(gifts_result, 'gifts'):
            for gift in gifts_result.gifts:
                if hasattr(gift, 'can_upgrade') and gift.can_upgrade:
                    upgradable_count += 1
        
        request_timestamps.append(time.time())
        return upgradable_count, None
        
    except FloodWait as e:
        wait_time = e.value
        logger.warning(f"⏳ FloodWait: {wait_time} сек")
        time.sleep(min(wait_time, 60))
        if wait_time < 60:
            return process_account_sync(username, chat_id, user_id)
        return None, f"FloodWait: {wait_time} сек"
        
    except Exception as e:
        logger.error(f"❌ Ошибка {username}: {e}")
        return None, str(e)

def process_batch_sync(chat_id, user_id):
    global pyro_client, user_data
    
    data = user_data.get(user_id)
    if not data:
        return
    
    usernames = data["usernames"]
    total = len(usernames)
    
    for index, username in enumerate(usernames):
        if data.get("status") == "stopped":
            pyro_client.send_message(chat_id, "⏹️ Проверка остановлена.")
            break
        
        data["index"] = index
        
        if index > 0 and index % BATCH_SIZE == 0:
            pyro_client.send_message(
                chat_id,
                f"⏸️ Пауза {BATCH_PAUSE} сек (обработано {index}/{total})"
            )
            time.sleep(BATCH_PAUSE)
        
        progress_msg = pyro_client.send_message(
            chat_id,
            f"⏳ Прогресс: {index + 1}/{total}\n🔄 Проверяю {username}..."
        )
        
        result, error = process_account_sync(username, chat_id, user_id)
        
        if error:
            pyro_client.send_message(chat_id, f"❌ **{username}**\nОшибка: {error}")
        else:
            if result > 0:
                data['total_gifts'] = data.get('total_gifts', 0) + result
                pyro_client.send_message(chat_id, f"✅ **{username}**\n📦 Неулучшенных подарков: **{result}**")
            else:
                pyro_client.send_message(chat_id, f"ℹ️ **{username}**\n📦 Неулучшенных подарков: **0**")
        
        try:
            progress_msg.delete()
        except:
            pass
        
        time.sleep(get_delay())
    
    if data.get("status") != "stopped":
        total_time = int(time.time() - data["start_time"])
        avg_time = total_time / total if total > 0 else 0
        pyro_client.send_message(
            chat_id,
            f"✅ **Проверка завершена!**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📊 Всего проверено: **{total}** аккаунтов\n"
            f"⏱ Время: **{total_time}** сек\n"
            f"🎁 Найдено подарков: **{data.get('total_gifts', 0)}**"
        )
    
    data["status"] = "finished"

def handle_new_message(message_text, chat_id, user_id):
    global pyro_client, user_data
    
    if not pyro_client:
        return
    
    text = message_text.strip()
    
    if text.startswith('/'):
        if text.lower() in ["/stop", "стоп"]:
            if user_id in user_data:
                user_data[user_id]["status"] = "stopped"
                pyro_client.send_message(chat_id, "⏹️ Проверка остановлена.")
            return
        
        if text.lower() in ["/stats", "статистика"]:
            if user_id in user_data and user_data[user_id].get("status") == "active":
                data = user_data[user_id]
                total = len(data["usernames"])
                current = data.get("index", 0)
                gifts = data.get('total_gifts', 0)
                elapsed = int(time.time() - data["start_time"])
                pyro_client.send_message(
                    chat_id,
                    f"📊 **Статистика**\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"📦 Всего: {total}\n"
                    f"🔄 Обработано: {current}/{total}\n"
                    f"🎁 Найдено: {gifts}\n"
                    f"⏱ Прошло: {elapsed} сек"
                )
            else:
                pyro_client.send_message(chat_id, "ℹ️ Нет активной проверки.")
            return
        
        if text.lower() in ["/help", "помощь"]:
            pyro_client.send_message(
                chat_id,
                "🤖 **Помощь**\n\n"
                "Отправь список @username\n"
                "Формат: @username1 - 1\n\n"
                "Команды:\n"
                "/stop - остановить\n"
                "/stats - прогресс\n"
                "/help - справка"
            )
            return
        
        return
    
    # Обработка списка
    if user_id in user_data and user_data[user_id].get("status") == "active":
        data = user_data[user_id]
        pyro_client.send_message(
            chat_id,
            f"⏳ Уже идет проверка.\nПрогресс: {data['index']}/{len(data['usernames'])}"
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
        pyro_client.send_message(
            chat_id,
            "❌ Не найдено @username.\nОтправь список: @username1 - 1"
        )
        return
    
    if len(usernames) > 200:
        pyro_client.send_message(
            chat_id,
            f"⚠️ Слишком много ({len(usernames)}). Максимум 200."
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
    
    pyro_client.send_message(
        chat_id,
        f"✅ Получено {len(usernames)} аккаунтов.\n"
        f"⏱ Время: ~{len(usernames) * 3} сек\n"
        f"Для остановки: /stop"
    )
    
    thread = threading.Thread(target=process_batch_sync, args=(chat_id, user_id))
    thread.daemon = True
    thread.start()

# --- ЗАПУСК PYROGRAM ---

def run_pyrogram_client():
    global pyro_client, is_ready
    
    try:
        pyro_client = init_pyrogram()
        if not pyro_client:
            logger.error("❌ Не удалось создать клиент")
            return
        
        pyro_client.start()
        logger.info("✅ Pyrogram клиент запущен")
        
        me = pyro_client.get_me()
        logger.info(f"👤 Аккаунт: @{me.username}")
        
        is_ready = True
        
        @pyro_client.on_message()
        def message_handler(client, message):
            if message.chat.type.name == "PRIVATE":
                handle_new_message(
                    message.text,
                    message.chat.id,
                    message.from_user.id
                )
        
        pyro_client.idle()
        
    except Exception as e:
        logger.error(f"❌ Ошибка в Pyrogram: {e}")
        is_ready = False

# --- FLASK ЭНДПОИНТЫ ---

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "ready" if is_ready else "starting",
        "service": "Gift Checker",
        "active_checks": len([u for u in user_data.values() if u.get("status") == "active"])
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "alive", "ready": is_ready}), 200

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
        "finished": len([u for u in user_data.values() if u.get("status") == "finished"])
    })

# --- ГЛАВНЫЙ ЗАПУСК ---

def main():
    """Главная функция"""
    logger.info("🚀 Запуск сервиса...")
    
    # Запускаем Pyrogram в фоне
    pyro_thread = threading.Thread(target=run_pyrogram_client, daemon=True)
    pyro_thread.start()
    
    # Flask запускаем СРАЗУ, не ждем Pyrogram
    logger.info(f"🌐 Запуск Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)

if __name__ == "__main__":
    main()
