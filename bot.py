import asyncio
import os
import threading
import logging
import time
from flask import Flask, request, jsonify
from pyrogram import Client, filters
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.enums import ChatType
from pyrogram.errors import FloodWait

# --- НАСТРОЙКА ЛОГОВ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
SESSION_NAME = "userbot_session"
PORT = int(os.getenv("PORT", 5000))

# Проверяем наличие API данных
if not API_ID or not API_HASH:
    logger.error("❌ API_ID и API_HASH должны быть установлены в переменных окружения!")
    exit(1)

# --- FLASK ПРИЛОЖЕНИЕ ---
app = Flask(__name__)

# --- PYROGRAM КЛИЕНТ с cryptg ---
# Важно: cryptg подхватывается автоматически через pyrogram
pyro_client = Client(
    SESSION_NAME, 
    api_id=API_ID, 
    api_hash=API_HASH,
    workdir="."  # Рабочая директория для сессии
)

# Хранилище: {user_id: {"usernames": [...], "index": 0, "status": "active", "chat_id": id}}
user_data = {}

# --- ЛОГИКА ОБРАБОТКИ ---

async def process_next_account(chat_id, user_id):
    """Обрабатывает следующий аккаунт из списка пользователя"""
    data = user_data.get(user_id)
    if not data or data.get("status") == "finished":
        return

    usernames = data["usernames"]
    index = data["index"]

    # Все проверены
    if index >= len(usernames):
        await pyro_client.send_message(
            chat_id, 
            "✅ **Проверка завершена!**\n"
            f"Всего проверено: {len(usernames)} аккаунтов."
        )
        data["status"] = "finished"
        data["end_time"] = time.time()
        return

    username = usernames[index]
    
    # Обновляем прогресс
    progress = f"⏳ Прогресс: {index + 1}/{len(usernames)}"
    await pyro_client.send_message(chat_id, f"{progress}\n🔄 Проверяю {username}...")

    try:
        # Получаем пользователя по юзернейму
        entity = await pyro_client.get_users(username)
        peer = await pyro_client.resolve_peer(entity.id)

        # Запрашиваем подарки через MTProto
        gifts_result = await pyro_client.invoke(
            GetSavedStarGifts(
                peer=peer,
                exclude_unsaved=True,      # Только сохранённые на профиле
                exclude_saved=False,
                exclude_upgradable=False,  # Не исключаем улучшаемые
                exclude_unupgradable=True  # Исключаем неулучшаемые
            )
        )

        # Считаем неулучшенные (can_upgrade = True)
        upgradable_count = 0
        if gifts_result and hasattr(gifts_result, 'gifts'):
            for gift in gifts_result.gifts:
                if hasattr(gift, 'can_upgrade') and gift.can_upgrade:
                    upgradable_count += 1

        # Формируем результат
        if upgradable_count > 0:
            result_text = (
                f"✅ **{username}**\n"
                f"📦 Неулучшенных подарков: **{upgradable_count}**"
            )
        else:
            result_text = (
                f"ℹ️ **{username}**\n"
                f"📦 Неулучшенных подарков: **0**"
            )
        
        await pyro_client.send_message(chat_id, result_text)
        logger.info(f"✅ {username}: {upgradable_count} gifts (прогресс: {index + 1}/{len(usernames)})")

    except FloodWait as e:
        # Обработка ограничений Telegram
        wait_time = e.value
        logger.warning(f"⏳ FloodWait: ждём {wait_time} секунд для {username}")
        await pyro_client.send_message(
            chat_id, 
            f"⏳ Telegram просит подождать {wait_time} секунд из-за ограничений..."
        )
        await asyncio.sleep(wait_time)
        # Повторяем попытку
        await process_next_account(chat_id, user_id)
        return

    except Exception as e:
        error_msg = f"❌ Ошибка при проверке {username}: {str(e)}"
        await pyro_client.send_message(chat_id, error_msg)
        logger.error(f"Ошибка проверки {username}: {e}")

    # Переход к следующему
    data["index"] = index + 1
    
    # Небольшая задержка между запросами (для безопасности)
    await asyncio.sleep(1.5)
    
    # Продолжаем обработку
    await process_next_account(chat_id, user_id)

async def handle_new_message(client, message):
    """Обработчик новых сообщений"""
    # Только личные сообщения
    if message.chat.type != ChatType.PRIVATE:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text

    # Пропускаем команды
    if text.startswith('/'):
        # Обработка команды /stop
        if text.lower() == "/stop" or text.lower() == "стоп":
            if user_id in user_data:
                del user_data[user_id]
                await pyro_client.send_message(chat_id, "⏹️ Проверка остановлена.")
                logger.info(f"🛑 Пользователь {user_id} остановил проверку")
            return
        return

    logger.info(f"📩 Получено сообщение от {user_id}: {text[:100]}...")

    # Если пользователь уже обрабатывается
    if user_id in user_data and user_data[user_id].get("status") != "finished":
        await pyro_client.send_message(
            chat_id,
            "⏳ Предыдущий список еще обрабатывается.\n"
            f"Прогресс: {user_data[user_id]['index']}/{len(user_data[user_id]['usernames'])}\n"
            "Дождись завершения или отправь /stop чтобы остановить."
        )
        return

    # Парсим список
    lines = text.strip().split('\n')
    usernames = []
    for line in lines:
        if '@' in line:
            # Извлекаем юзернейм (до тире или пробела)
            if ' - ' in line:
                username = line.split(' - ')[0].strip()
            elif '—' in line:  # Длинное тире
                username = line.split('—')[0].strip()
            else:
                username = line.strip()
            
            if username.startswith('@'):
                usernames.append(username)

    if not usernames:
        await pyro_client.send_message(
            chat_id,
            "❌ **Не найдено ни одного @username.**\n\n"
            "Отправь список в формате:\n"
            "`@username1 - 1`\n"
            "`@username2 - 2`\n\n"
            "Или просто:\n"
            "`@username1`\n"
            "`@username2`"
        )
        return

    # Сохраняем данные пользователя
    user_data[user_id] = {
        "usernames": usernames,
        "index": 0,
        "status": "active",
        "chat_id": chat_id,
        "start_time": time.time()
    }

    await pyro_client.send_message(
        chat_id,
        f"✅ **Получено {len(usernames)} аккаунтов.**\n"
        f"Начинаю проверку...\n\n"
        f"⏱ Примерное время: ~{len(usernames) * 2} секунд\n"
        f"Для остановки отправь /stop"
    )

    # Запускаем обработку
    await process_next_account(chat_id, user_id)

# --- FLASK ЭНДПОИНТЫ ДЛЯ МОНИТОРИНГА ---

@app.route('/', methods=['GET'])
def index():
    """Главная страница с информацией о сервисе"""
    active_users = len([u for u in user_data.values() if u.get("status") != "finished"])
    finished_users = len([u for u in user_data.values() if u.get("status") == "finished"])
    
    return jsonify({
        "status": "running",
        "service": "Telegram Gift Checker (Userbot)",
        "version": "2.0 (with cryptg)",
        "active_checks": active_users,
        "finished_checks": finished_users,
        "total_requests": len(user_data),
        "api_id": API_ID
    })

@app.route('/health', methods=['GET'])
def health():
    """Проверка здоровья сервиса"""
    return jsonify({
        "status": "alive",
        "timestamp": time.time()
    }), 200

@app.route('/stats', methods=['GET'])
def stats():
    """Статистика по текущим проверкам"""
    active = []
    for user_id, data in user_data.items():
        status = data.get("status", "unknown")
        if status != "finished":
            total = len(data["usernames"])
            current = data["index"]
            progress = f"{current}/{total}"
            elapsed = int(time.time() - data.get("start_time", time.time()))
            active.append({
                "user_id": user_id,
                "progress": progress,
                "status": status,
                "elapsed_seconds": elapsed
            })
    
    return jsonify({
        "active_checks": active,
        "total_finished": len([u for u in user_data.values() if u.get("status") == "finished"])
    })

@app.route('/clear', methods=['POST'])
def clear_finished():
    """Очищает завершенные проверки (для администрирования)"""
    finished = [uid for uid, data in user_data.items() if data.get("status") == "finished"]
    for uid in finished:
        del user_data[uid]
    return jsonify({
        "cleared": len(finished),
        "remaining": len(user_data)
    })

# --- ЗАПУСК PYROGRAM В ФОНОВОМ ПОТОКЕ ---

loop = asyncio.new_event_loop()

def run_pyrogram():
    """Запускает клиент Pyrogram в отдельном потоке с cryptg"""
    asyncio.set_event_loop(loop)

    @pyro_client.on_message(filters.private & filters.text)
    async def message_handler(client, message):
        # Обрабатываем все текстовые сообщения (включая команды)
        await handle_new_message(client, message)

    # Запускаем клиент
    try:
        logger.info("🚀 Запуск Pyrogram клиента с cryptg...")
        logger.info(f"📱 API ID: {API_ID}")
        logger.info(f"📁 Сессия: {SESSION_NAME}.session")
        pyro_client.run()
    except Exception as e:
        logger.error(f"❌ Ошибка Pyrogram: {e}")

# Запускаем Pyrogram в отдельном потоке
pyro_thread = threading.Thread(target=run_pyrogram, daemon=True)
pyro_thread.start()

# Даем время на запуск клиента
time.sleep(2)
logger.info("✅ Pyrogram клиент запущен")

# --- ЗАПУСК FLASK ---

if __name__ == "__main__":
    logger.info(f"🌐 Запуск Flask сервера на порту {PORT}")
    logger.info(f"🔗 Health check: http://localhost:{PORT}/health")
    logger.info(f"📊 Stats: http://localhost:{PORT}/stats")
    app.run(host='0.0.0.0', port=PORT)
