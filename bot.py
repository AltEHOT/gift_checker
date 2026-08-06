import asyncio
import os
import threading
import logging
import time
from flask import Flask, jsonify
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

if not API_ID or not API_HASH:
    logger.error("❌ API_ID и API_HASH должны быть установлены!")
    exit(1)

# --- FLASK ---
app = Flask(__name__)

# --- PYROGRAM КЛИЕНТ ---
pyro_client = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    workdir="."
)

# --- ХРАНИЛИЩЕ ---
user_data = {}

# --- ОБРАБОТЧИКИ ---

async def process_next_account(chat_id, user_id):
    """Обрабатывает следующий аккаунт"""
    data = user_data.get(user_id)
    if not data or data.get("status") == "finished":
        return

    usernames = data["usernames"]
    index = data["index"]

    if index >= len(usernames):
        await pyro_client.send_message(
            chat_id,
            f"✅ **Проверка завершена!**\nВсего проверено: {len(usernames)} аккаунтов."
        )
        data["status"] = "finished"
        data["end_time"] = time.time()
        return

    username = usernames[index]
    progress = f"⏳ Прогресс: {index + 1}/{len(usernames)}"
    await pyro_client.send_message(chat_id, f"{progress}\n🔄 Проверяю {username}...")

    try:
        entity = await pyro_client.get_users(username)
        peer = await pyro_client.resolve_peer(entity.id)

        gifts_result = await pyro_client.invoke(
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

        if upgradable_count > 0:
            result_text = f"✅ **{username}**\n📦 Неулучшенных подарков: **{upgradable_count}**"
        else:
            result_text = f"ℹ️ **{username}**\n📦 Неулучшенных подарков: **0**"

        await pyro_client.send_message(chat_id, result_text)
        logger.info(f"✅ {username}: {upgradable_count} gifts ({index + 1}/{len(usernames)})")

    except FloodWait as e:
        wait_time = e.value
        logger.warning(f"⏳ FloodWait: ждём {wait_time} секунд")
        await pyro_client.send_message(chat_id, f"⏳ Жду {wait_time} секунд...")
        await asyncio.sleep(wait_time)
        await process_next_account(chat_id, user_id)
        return

    except Exception as e:
        await pyro_client.send_message(chat_id, f"❌ Ошибка {username}: {str(e)}")
        logger.error(f"Ошибка {username}: {e}")

    data["index"] = index + 1
    await asyncio.sleep(1.5)
    await process_next_account(chat_id, user_id)

async def handle_new_message(client, message):
    """Обработчик сообщений"""
    if message.chat.type != ChatType.PRIVATE:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text

    if text.startswith('/'):
        if text.lower() in ["/stop", "стоп"]:
            if user_id in user_data:
                del user_data[user_id]
                await pyro_client.send_message(chat_id, "⏹️ Проверка остановлена.")
            return
        return

    logger.info(f"📩 Сообщение от {user_id}: {text[:100]}...")

    if user_id in user_data and user_data[user_id].get("status") != "finished":
        await pyro_client.send_message(
            chat_id,
            f"⏳ Предыдущий список обрабатывается.\n"
            f"Прогресс: {user_data[user_id]['index']}/{len(user_data[user_id]['usernames'])}"
        )
        return

    lines = text.strip().split('\n')
    usernames = []
    for line in lines:
        if '@' in line:
            if ' - ' in line:
                username = line.split(' - ')[0].strip()
            elif '—' in line:
                username = line.split('—')[0].strip()
            else:
                username = line.strip()
            if username.startswith('@'):
                usernames.append(username)

    if not usernames:
        await pyro_client.send_message(
            chat_id,
            "❌ **Не найдено @username.**\n\n"
            "Отправь список:\n"
            "`@username1 - 1`\n`@username2 - 2`"
        )
        return

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
        f"⏱ Примерно: ~{len(usernames) * 2} секунд\n"
        f"Для остановки: /stop"
    )

    await process_next_account(chat_id, user_id)

# --- FLASK ЭНДПОИНТЫ ---

@app.route('/', methods=['GET'])
def index():
    active = len([u for u in user_data.values() if u.get("status") != "finished"])
    finished = len([u for u in user_data.values() if u.get("status") == "finished"])
    return jsonify({
        "status": "running",
        "service": "Gift Checker (cryptg)",
        "active_checks": active,
        "finished_checks": finished,
        "total": len(user_data)
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
            active.append({
                "user_id": user_id,
                "progress": f"{current}/{total}",
                "status": data.get("status", "unknown")
            })
    return jsonify({"active_checks": active})

# --- ЗАПУСК PYROGRAM ---

def run_pyrogram():
    """Запускает клиент в отдельном потоке"""
    @pyro_client.on_message(filters.private & filters.text)
    async def message_handler(client, message):
        await handle_new_message(client, message)

    try:
        logger.info("🚀 Запуск Pyrogram с cryptg...")
        pyro_client.run()
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

# Запускаем в потоке
pyro_thread = threading.Thread(target=run_pyrogram, daemon=True)
pyro_thread.start()

time.sleep(2)
logger.info("✅ Клиент запущен")

# --- ЗАПУСК FLASK ---

if __name__ == "__main__":
    logger.info(f"🌐 Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT)
