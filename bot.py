import asyncio
import os
import threading
from flask import Flask, request, jsonify
from pyrogram import Client, filters
from pyrogram.raw.functions.messages import GetDialogs
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.types import InputPeerUser
from pyrogram.enums import ChatType
import logging
import time

# --- НАСТРОЙКА ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
SESSION_NAME = "userbot_session"
PORT = int(os.getenv("PORT", 5000))

# --- FLASK ПРИЛОЖЕНИЕ ---
app = Flask(__name__)

# --- PYROGRAM КЛИЕНТ ---
pyro_client = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)

# Хранилище: {user_id: {"usernames": [...], "index": 0, "status": "active"}}
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
        await pyro_client.send_message(chat_id, "✅ **Проверка завершена!**")
        data["status"] = "finished"
        return

    username = usernames[index]
    await pyro_client.send_message(chat_id, f"🔄 Проверяю {username}...")

    try:
        # Получаем пользователя по юзернейму
        entity = await pyro_client.get_users(username)
        peer = await pyro_client.resolve_peer(entity.id)

        # Запрашиваем подарки
        gifts_result = await pyro_client.invoke(
            GetSavedStarGifts(
                peer=peer,
                exclude_unsaved=True,
                exclude_saved=False,
                exclude_upgradable=False,
                exclude_unupgradable=True
            )
        )

        # Считаем неулучшенные
        upgradable_count = 0
        if gifts_result and hasattr(gifts_result, 'gifts'):
            for gift in gifts_result.gifts:
                if hasattr(gift, 'can_upgrade') and gift.can_upgrade:
                    upgradable_count += 1

        result_text = (
            f"👤 **{username}**\n"
            f"📦 Неулучшенных подарков: **{upgradable_count}**"
        )
        await pyro_client.send_message(chat_id, result_text)

        # Отправляем в консоль для отслеживания
        logger.info(f"✅ {username}: {upgradable_count} gifts")

    except Exception as e:
        error_msg = f"❌ Ошибка при проверке {username}: {str(e)}"
        await pyro_client.send_message(chat_id, error_msg)
        logger.error(f"Error checking {username}: {e}")

    # Переход к следующему
    data["index"] = index + 1
    await asyncio.sleep(1.5)  # Задержка чтобы не получить бан
    await process_next_account(chat_id, user_id)

async def handle_new_message(client, message):
    """Обработчик новых сообщений"""
    # Только личные сообщения
    if message.chat.type != ChatType.PRIVATE:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text

    # Игнорируем команды, начинающиеся с /
    if text.startswith('/'):
        return

    logger.info(f"📩 Получено сообщение от {user_id}: {text[:50]}...")

    # Если пользователь уже обрабатывается
    if user_id in user_data and user_data[user_id].get("status") != "finished":
        await pyro_client.send_message(
            chat_id,
            "⏳ Предыдущий список еще обрабатывается. Подожди завершения."
        )
        return

    # Парсим список
    lines = text.strip().split('\n')
    usernames = []
    for line in lines:
        if '@' in line:
            # Извлекаем юзернейм (до тире или пробела)
            username = line.split(' - ')[0].strip() if ' - ' in line else line.strip()
            if username.startswith('@'):
                usernames.append(username)

    if not usernames:
        await pyro_client.send_message(
            chat_id,
            "❌ Не найдено ни одного @username.\n"
            "Отправь список в формате:\n"
            "@username1 - 1\n"
            "@username2 - 2"
        )
        return

    # Сохраняем данные пользователя
    user_data[user_id] = {
        "usernames": usernames,
        "index": 0,
        "status": "active",
        "chat_id": chat_id
    }

    await pyro_client.send_message(
        chat_id,
        f"✅ Получено {len(usernames)} аккаунтов.\nНачинаю проверку...\n"
        f"Это может занять ~{len(usernames) * 2} секунд."
    )

    # Запускаем обработку
    await process_next_account(chat_id, user_id)

# --- FLASK ЭНДПОИНТЫ ДЛЯ МОНИТОРИНГА ---

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "running",
        "service": "Telegram Gift Checker (Userbot)",
        "active_users": len([u for u in user_data.values() if u.get("status") != "finished"]),
        "total_users": len(user_data)
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "alive"}), 200

@app.route('/stats', methods=['GET'])
def stats():
    """Статистика по текущим проверкам"""
    active = []
    for user_id, data in user_data.items():
        if data.get("status") != "finished":
            total = len(data["usernames"])
            current = data["index"]
            progress = f"{current}/{total}"
            active.append({
                "user_id": user_id,
                "progress": progress,
                "status": data.get("status", "unknown")
            })
    return jsonify({
        "active_checks": active,
        "total_finished": len([u for u in user_data.values() if u.get("status") == "finished"])
    })

# --- ЗАПУСК PYROGRAM В ФОНОВОМ ПОТОКЕ ---

loop = asyncio.new_event_loop()

def run_pyrogram():
    """Запускает клиент Pyrogram в отдельном потоке"""
    asyncio.set_event_loop(loop)

    @pyro_client.on_message(filters.private & filters.text & ~filters.command("start"))
    async def message_handler(client, message):
        await handle_new_message(client, message)

    # Запускаем клиент
    try:
        logger.info("🚀 Запуск Pyrogram клиента...")
        pyro_client.run()
    except Exception as e:
        logger.error(f"❌ Pyrogram error: {e}")

# Запускаем Pyrogram в отдельном потоке
pyro_thread = threading.Thread(target=run_pyrogram, daemon=True)
pyro_thread.start()

# --- ЗАПУСК FLASK ---

if __name__ == "__main__":
    logger.info(f"🌐 Запуск Flask сервера на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT)
