import asyncio
import os
import threading
import logging
import time
import random
from flask import Flask, jsonify
from pyrogram import Client, filters
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.enums import ChatType
from pyrogram.errors import FloodWait, RPCError

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
MIN_DELAY = 2.0          # Минимальная задержка между запросами (сек)
MAX_DELAY = 5.0          # Максимальная задержка между запросами (сек)
MAX_REQUESTS_PER_MINUTE = 20  # Максимум запросов в минуту
BATCH_SIZE = 50          # После скольких аккаунтов делать паузу
BATCH_PAUSE = 30         # Пауза после BATCH_SIZE аккаунтов (сек)
MAX_RETRIES = 3          # Сколько раз повторять при ошибке

# --- ПРОВЕРКА ---
if not API_ID or not API_HASH:
    logger.error("❌ API_ID и API_HASH не установлены!")
    exit(1)

# --- FLASK ---
app = Flask(__name__)

# --- PYROGRAM КЛИЕНТ ---
pyro_client = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    workdir=".",
    # Дополнительные настройки для снижения нагрузки
    sleep_threshold=30,  # Автоматическая задержка при флуде
    no_updates=True,     # Отключаем обработку обновлений (экономит ресурсы)
    in_memory=False      # Храним сессию на диске
)

# --- ХРАНИЛИЩЕ ---
user_data = {}
request_timestamps = []  # Для отслеживания частоты запросов

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def can_make_request():
    """Проверяет, не превышен ли лимит запросов"""
    global request_timestamps
    now = time.time()
    # Удаляем старые записи (старше 60 секунд)
    request_timestamps = [t for t in request_timestamps if now - t < 60]
    
    if len(request_timestamps) >= MAX_REQUESTS_PER_MINUTE:
        wait_time = 60 - (now - request_timestamps[0])
        logger.warning(f"⚠️ Достигнут лимит запросов. Ждём {wait_time:.1f} сек")
        return False, wait_time
    
    return True, 0

def get_delay():
    """Возвращает случайную задержку между запросами"""
    return random.uniform(MIN_DELAY, MAX_DELAY)

# --- ОСНОВНАЯ ЛОГИКА ---

async def safe_request(chat_id, user_id, username, retry_count=0):
    """
    Безопасный запрос с обработкой всех ошибок
    """
    try:
        # 1. Проверяем лимит запросов
        can_request, wait_time = can_make_request()
        if not can_request:
            await pyro_client.send_message(
                chat_id,
                f"⏳ Достигнут лимит запросов. Пауза {wait_time:.0f} сек..."
            )
            await asyncio.sleep(wait_time)
            return await safe_request(chat_id, user_id, username, retry_count)
        
        # 2. Получаем пользователя
        entity = await pyro_client.get_users(username)
        peer = await pyro_client.resolve_peer(entity.id)
        
        # 3. Запрашиваем подарки
        gifts_result = await pyro_client.invoke(
            GetSavedStarGifts(
                peer=peer,
                exclude_unsaved=True,
                exclude_saved=False,
                exclude_upgradable=False,
                exclude_unupgradable=True
            )
        )
        
        # 4. Считаем неулучшенные
        upgradable_count = 0
        if gifts_result and hasattr(gifts_result, 'gifts'):
            for gift in gifts_result.gifts:
                if hasattr(gift, 'can_upgrade') and gift.can_upgrade:
                    upgradable_count += 1
        
        # 5. Запоминаем время запроса
        request_timestamps.append(time.time())
        
        # 6. Возвращаем результат
        return upgradable_count, None
        
    except FloodWait as e:
        wait_time = e.value
        logger.warning(f"⏳ FloodWait: {wait_time} сек для {username}")
        
        # Если ждать больше 60 секунд - уведомляем пользователя
        if wait_time > 60:
            await pyro_client.send_message(
                chat_id,
                f"⚠️ Telegram просит подождать {wait_time} секунд из-за лимитов..."
            )
        
        await asyncio.sleep(wait_time)
        
        # Пробуем снова
        if retry_count < MAX_RETRIES:
            return await safe_request(chat_id, user_id, username, retry_count + 1)
        else:
            return None, f"Превышено количество попыток: {e}"
            
    except RPCError as e:
        logger.error(f"❌ RPC ошибка для {username}: {e}")
        return None, str(e)
        
    except Exception as e:
        logger.error(f"❌ Неизвестная ошибка для {username}: {e}")
        return None, str(e)

async def process_next_account(chat_id, user_id):
    """Обрабатывает следующий аккаунт с защитой от флуда"""
    data = user_data.get(user_id)
    if not data or data.get("status") == "finished":
        return

    usernames = data["usernames"]
    index = data["index"]

    if index >= len(usernames):
        # Финальное сообщение
        total_time = int(time.time() - data["start_time"])
        avg_time = total_time / len(usernames) if len(usernames) > 0 else 0
        
        await pyro_client.send_message(
            chat_id,
            f"✅ **Проверка завершена!**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📊 Всего проверено: **{len(usernames)}** аккаунтов\n"
            f"⏱ Время: **{total_time}** сек\n"
            f"📈 Среднее: **{avg_time:.1f}** сек/аккаунт\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🎁 Найдено неулучшенных подарков: **{data.get('total_gifts', 0)}**"
        )
        data["status"] = "finished"
        data["end_time"] = time.time()
        
        # Очищаем данные через 5 минут (чтобы не накапливать)
        asyncio.create_task(clear_data_after_delay(user_id, 300))
        return

    username = usernames[index]
    progress = f"⏳ Прогресс: {index + 1}/{len(usernames)}"
    
    # Проверка на паузу между батчами
    if index > 0 and index % BATCH_SIZE == 0:
        await pyro_client.send_message(
            chat_id,
            f"⏸️ Пауза на {BATCH_PAUSE} сек (обработано {index} аккаунтов)"
        )
        logger.info(f"⏸️ Батч-пауза: {BATCH_PAUSE} сек")
        await asyncio.sleep(BATCH_PAUSE)
    
    # Отправляем статус
    status_msg = await pyro_client.send_message(
        chat_id, 
        f"{progress}\n🔄 Проверяю {username}..."
    )
    
    # Делаем запрос с защитой
    result, error = await safe_request(chat_id, user_id, username)
    
    if error:
        await pyro_client.send_message(
            chat_id,
            f"❌ **{username}**\nОшибка: {error}"
        )
        logger.error(f"❌ {username}: {error}")
    else:
        # Обновляем счетчик найденных подарков
        if result > 0:
            data['total_gifts'] = data.get('total_gifts', 0) + result
            result_text = f"✅ **{username}**\n📦 Неулучшенных подарков: **{result}**"
        else:
            result_text = f"ℹ️ **{username}**\n📦 Неулучшенных подарков: **0**"
        
        await pyro_client.send_message(chat_id, result_text)
        logger.info(f"✅ {username}: {result} gifts ({index + 1}/{len(usernames)})")
    
    # Удаляем статусное сообщение
    try:
        await status_msg.delete()
    except:
        pass
    
    # Задержка между запросами (случайная)
    delay = get_delay()
    await asyncio.sleep(delay)
    
    # Переходим к следующему
    data["index"] = index + 1
    await process_next_account(chat_id, user_id)

async def clear_data_after_delay(user_id, delay_seconds):
    """Очищает данные пользователя через указанное время"""
    await asyncio.sleep(delay_seconds)
    if user_id in user_data and user_data[user_id].get("status") == "finished":
        del user_data[user_id]
        logger.info(f"🧹 Очищены данные пользователя {user_id}")

async def handle_new_message(client, message):
    """Обработчик сообщений"""
    if message.chat.type != ChatType.PRIVATE:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text

    # --- КОМАНДЫ ---
    if text.startswith('/'):
        if text.lower() in ["/stop", "стоп"]:
            if user_id in user_data:
                del user_data[user_id]
                await pyro_client.send_message(chat_id, "⏹️ Проверка остановлена.")
                logger.info(f"🛑 {user_id} остановил проверку")
            return
        
        if text.lower() in ["/stats", "статистика"]:
            if user_id in user_data:
                data = user_data[user_id]
                total = len(data["usernames"])
                current = data["index"]
                gifts = data.get('total_gifts', 0)
                await pyro_client.send_message(
                    chat_id,
                    f"📊 **Статистика проверки**\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"📦 Всего: {total}\n"
                    f"🔄 Обработано: {current}/{total}\n"
                    f"🎁 Найдено подарков: {gifts}\n"
                    f"⏱ Время: {int(time.time() - data['start_time'])} сек"
                )
            else:
                await pyro_client.send_message(chat_id, "ℹ️ Нет активной проверки.")
            return
        
        # Команда /help
        if text.lower() in ["/help", "помощь"]:
            await pyro_client.send_message(
                chat_id,
                "🤖 **Помощь**\n\n"
                "**Как использовать:**\n"
                "1. Отправь список @username\n"
                "2. Формат: @username1 - 1\n"
                "3. Дождись завершения\n\n"
                "**Команды:**\n"
                "/stop - остановить проверку\n"
                "/stats - показать прогресс\n"
                "/help - эта справка"
            )
            return
        
        return

    # --- ОБРАБОТКА СПИСКА ---
    logger.info(f"📩 Сообщение от {user_id}: {text[:50]}...")

    # Проверка на активную обработку
    if user_id in user_data and user_data[user_id].get("status") != "finished":
        data = user_data[user_id]
        await pyro_client.send_message(
            chat_id,
            f"⏳ Предыдущий список обрабатывается.\n"
            f"📊 Прогресс: {data['index']}/{len(data['usernames'])}\n"
            f"⏱ Прошло: {int(time.time() - data['start_time'])} сек"
        )
        return

    # Парсим список
    lines = text.strip().split('\n')
    usernames = []
    for line in lines:
        if '@' in line:
            # Разные форматы разделителей
            for separator in [' - ', '—', ' -', '- ', '\t']:
                if separator in line:
                    username = line.split(separator)[0].strip()
                    break
            else:
                username = line.strip()
            
            if username.startswith('@'):
                usernames.append(username)

    if not usernames:
        await pyro_client.send_message(
            chat_id,
            "❌ **Не найдено @username.**\n\n"
            "Отправь список в формате:\n"
            "`@username1 - 1`\n"
            "`@username2 - 2`\n\n"
            "Используй /help для справки."
        )
        return

    # Проверяем размер списка
    if len(usernames) > 200:
        await pyro_client.send_message(
            chat_id,
            f"⚠️ Список слишком большой ({len(usernames)} аккаунтов).\n"
            f"Максимум: 200 аккаунтов за раз.\n"
            f"Разбей список на части."
        )
        return

    # Сохраняем данные
    estimated_time = len(usernames) * 3  # ~3 секунды на аккаунт с задержками
    user_data[user_id] = {
        "usernames": usernames,
        "index": 0,
        "status": "active",
        "chat_id": chat_id,
        "start_time": time.time(),
        "total_gifts": 0
    }

    await pyro_client.send_message(
        chat_id,
        f"✅ **Получено {len(usernames)} аккаунтов.**\n"
        f"⏱ Примерное время: ~{estimated_time} секунд\n"
        f"🛡️ Защита от флуда: ВКЛ\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Для остановки: /stop\n"
        f"Для статистики: /stats"
    )

    # Запускаем обработку
    await process_next_account(chat_id, user_id)

# --- FLASK ЭНДПОИНТЫ ---

@app.route('/', methods=['GET'])
def index():
    active = len([u for u in user_data.values() if u.get("status") != "finished"])
    finished = len([u for u in user_data.values() if u.get("status") == "finished"])
    return jsonify({
        "status": "running",
        "service": "Gift Checker",
        "active_checks": active,
        "finished_checks": finished,
        "total_requests": len(user_data),
        "requests_per_minute": len(request_timestamps),
        "settings": {
            "min_delay": MIN_DELAY,
            "max_delay": MAX_DELAY,
            "batch_size": BATCH_SIZE,
            "batch_pause": BATCH_PAUSE,
            "max_per_minute": MAX_REQUESTS_PER_MINUTE
        }
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "alive", "timestamp": time.time()}), 200

@app.route('/stats', methods=['GET'])
def stats():
    active = []
    for user_id, data in user_data.items():
        if data.get("status") != "finished":
            total = len(data["usernames"])
            current = data["index"]
            elapsed = int(time.time() - data.get("start_time", time.time()))
            active.append({
                "user_id": user_id,
                "progress": f"{current}/{total}",
                "elapsed_seconds": elapsed,
                "gifts_found": data.get('total_gifts', 0)
            })
    return jsonify({
        "active_checks": active,
        "finished": len([u for u in user_data.values() if u.get("status") == "finished"]),
        "requests_last_minute": len(request_timestamps)
    })

@app.route('/clear', methods=['POST'])
def clear_finished():
    """Очищает завершенные проверки"""
    finished = [uid for uid, data in user_data.items() if data.get("status") == "finished"]
    for uid in finished:
        del user_data[uid]
    return jsonify({
        "cleared": len(finished),
        "remaining": len(user_data)
    })

# --- ЗАПУСК PYROGRAM ---

def run_pyrogram():
    """Запускает клиент в отдельном потоке"""
    @pyro_client.on_message(filters.private & filters.text)
    async def message_handler(client, message):
        await handle_new_message(client, message)

    try:
        logger.info("🚀 Запуск Pyrogram с защитой от флуда...")
        logger.info(f"📊 Настройки: задержка {MIN_DELAY}-{MAX_DELAY}с, батч {BATCH_SIZE}")
        pyro_client.run()
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

# Запускаем Pyrogram в потоке
pyro_thread = threading.Thread(target=run_pyrogram, daemon=True)
pyro_thread.start()

time.sleep(2)
logger.info("✅ Клиент запущен")

# --- ЗАПУСК FLASK ---

if __name__ == "__main__":
    logger.info(f"🌐 Flask на порту {PORT}")
    logger.info("📊 Для статистики: /stats")
    app.run(host='0.0.0.0', port=PORT)
