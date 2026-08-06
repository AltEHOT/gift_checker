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
from telethon import TelegramClient, events, functions, types
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
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
elif os.path.exists("userbot_session.session"):
    client = TelegramClient("userbot_session", API_ID, API_HASH)
else:
    logger.error("❌ Не найдена сессия!")
    sys.exit(1)

app = Flask(__name__)
user_data = {}
client_ready = False

@app.route('/')
def index():
    return jsonify({"status": "running", "client_ready": client_ready})

@app.route('/health')
def health():
    return jsonify({"status": "alive"}), 200

def is_valid_username(text):
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    return re.match(r'^@[A-Za-z0-9_]{3,}$', text) is not None

# --- ПРОВЕРКА ПОДАРКОВ ---
async def check_gifts(username):
    try:
        username = str(username).strip()
        if username.startswith('@'):
            username = username[1:]
        
        if not username or username.isdigit():
            return None, "Невалидный юзернейм"
        
        # Получаем объект пользователя
        try:
            input_peer = await client.get_input_entity(username)
        except Exception:
            return None, "Не найден / скрыт"
        
        # Запрос списка звездных подарков
        # exclude_unsaved=False позволяет видеть даже те подарки, которые НЕ закреплены в профиле
        try:
            result = await client(functions.payments.GetSavedStarGiftsRequest(
                peer=input_peer,
                offset="", 
                limit=100,
                exclude_unsaved=False,   # Важно: искать везде
                exclude_saved=False,
                exclude_upgradable=False, # Нам нужны те, которые можно улучшить
                exclude_unupgradable=True # Игнорируем те, которые улучшить нельзя
            ))
        except FloodWaitError as e:
            logger.warning(f"⏳ FloodWait {e.seconds} сек")
            await asyncio.sleep(e.seconds + 1)
            return await check_gifts(username)
        except Exception as e:
            return None, f"Ошибка API: {str(e)[:30]}"
        
        count = 0
        if result and hasattr(result, 'gifts'):
            for saved_gift in result.gifts:
                # Проверяем, можно ли улучшить этот конкретный подарок
                # В структуре UserStarGift поле can_upgrade указывает на возможность улучшения
                if getattr(saved_gift, 'can_upgrade', False):
                    count += 1
        
        return count, None
        
    except Exception as e:
        logger.error(f"Ошибка на {username}: {e}")
        return None, "Системная ошибка"

def format_report(results, total_time, total_gifts):
    lines = ["✅ **ОТЧЕТ ПО ПОДАРКАМ**", "━━━━━━━━━━━━━━━━━"]
    # Сортировка: сначала те, у кого больше подарков
    sorted_res = sorted(results, key=lambda x: x[1] if x[1] is not None else -1, reverse=True)
    
    for user, count, err in sorted_res:
        if err:
            lines.append(f"❌ @{user}: {err}")
        elif count > 0:
            lines.append(f"🎁 @{user}: **{count}** неулучшенных")
        else:
            lines.append(f"ℹ️ @{user}: 0")
    
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 Аккаунтов: {len(results)} | Найдено: {total_gifts}")
    lines.append(f"⏱ Время: {total_time} сек")
    return "\n".join(lines)

@client.on(events.NewMessage(incoming=True))
async def handler(event):
    if not event.is_private: return
    user_id = event.sender_id
    text = event.message.text
    if not text: return

    # Простая обработка команд
    if text.startswith('/'):
        cmd = text.lower()
        if cmd == '/start':
            await event.reply("Привет! Пришли мне список @юзернеймов (каждый с новой строки).")
        elif cmd == '/stop':
            if user_id in user_data:
                user_data[user_id]['status'] = 'stopped'
        return

    # Если уже идет процесс
    if user_id in user_data and user_data[user_id].get('status') == 'active':
        await event.reply("⏳ Пожалуйста, дождитесь завершения текущей проверки.")
        return

    # Парсинг юзернеймов
    lines = text.strip().split('\n')
    usernames = []
    for line in lines:
        match = re.search(r'@[A-Za-z0-9_]{3,}', line)
        if match:
            usernames.append(match.group(0))

    if not usernames:
        await event.reply("❌ В сообщении не найдено @юзернеймов.")
        return

    user_data[user_id] = {
        'status': 'active',
        'start_time': time.time(),
        'total_gifts': 0
    }

    status_msg = await event.reply(f"🚀 Начинаю проверку {len(usernames)} профилей...")
    
    results = []
    total_found = 0

    for i, uname in enumerate(usernames):
        if user_data[user_id].get('status') == 'stopped':
            break
        
        # Обновление статуса каждые 5 проверок
        if i > 0 and i % 5 == 0:
            try:
                await status_msg.edit(f"⏳ Прогресс: {i}/{len(usernames)}...")
            except: pass

        count, error = await check_gifts(uname)
        
        if count is not None:
            total_found += count
            results.append((uname.replace('@', ''), count, None))
        else:
            results.append((uname.replace('@', ''), None, error))
        
        # Задержка, чтобы не поймать бан
        await asyncio.sleep(random.uniform(1.5, 3.0))

    report = format_report(results, int(time.time() - user_data[user_id]['start_time']), total_found)
    user_data[user_id]['status'] = 'finished'
    
    # Отправка отчета частями, если он длинный
    if len(report) > 4000:
        for x in range(0, len(report), 4000):
            await event.respond(report[x:x+4000])
    else:
        await event.respond(report)

def start_bot():
    global client_ready
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        client.start()
        client_ready = True
        logger.info("✅ Юзербот запущен!")
        client.run_until_disconnected()
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")

if __name__ == "__main__":
    import threading
    threading.Thread(target=start_bot, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT)
