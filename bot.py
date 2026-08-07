import os
import sys
import time
import random
import logging
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

# --- НАСТРОЙКИ ЗАЩИТЫ ОТ ФЛУДА ---
MIN_DELAY = 3.0          # Минимальная задержка между проверками (сек)
MAX_DELAY = 6.0          # Максимальная задержка между проверками (сек)
BATCH_SIZE = 20          # После скольких пользователей делать паузу
BATCH_PAUSE = 45         # Длительность паузы (сек)
MAX_USERS_TO_CHECK = 500 # Максимум пользователей для проверки (защита от перегрузки)

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
client_ready = False
scanning_users = {}

# --- ЭНДПОИНТЫ ---
@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "running",
        "service": "Gift Checker",
        "client_ready": client_ready,
        "active_scans": len(scanning_users)
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "alive", "client_ready": client_ready}), 200

# --- ФУНКЦИЯ ДЛЯ ПРОВЕРКИ, БОТ ЛИ ЭТО ---
def is_bot_user(user):
    try:
        if hasattr(user, 'username') and user.username:
            if 'bot' in user.username.lower():
                return True
        if hasattr(user, 'first_name') and user.first_name:
            if 'bot' in user.first_name.lower():
                return True
        if not hasattr(user, 'first_name') or not user.first_name:
            return True
        return False
    except:
        return False

# --- ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ ДЕЛЕЯ (ЗАЩИТА ОТ ФЛУДА) ---
def get_delay():
    return random.uniform(MIN_DELAY, MAX_DELAY)

# --- ФУНКЦИЯ: ПОЛУЧЕНИЕ УЧАСТНИКОВ ЧАТА ---
async def get_chat_participants(entity):
    """Получает участников чата (без ограничений)"""
    global client
    
    users = []
    offset = 0
    limit = 200
    
    logger.info(f"🔍 Начинаю сбор участников чата...")
    
    try:
        while True:
            try:
                chunk = await client.get_participants(
                    entity,
                    offset=offset,
                    limit=limit
                )
                
                if not chunk:
                    break
                
                for user in chunk:
                    if user.username and not is_bot_user(user):
                        users.append(user)
                
                offset += limit
                logger.info(f"   Собрано {len(users)} пользователей...")
                
                # Пауза между порциями
                await asyncio.sleep(1)
                
            except FloodWaitError as e:
                wait = e.seconds
                logger.warning(f"⏳ FloodWait при сборе: {wait} сек")
                await asyncio.sleep(wait + 1)
                continue
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при сборе участников: {e}")
                break
        
        logger.info(f"✅ Всего собрано {len(users)} пользователей")
        return users
        
    except Exception as e:
        logger.error(f"❌ Ошибка сбора участников: {e}")
        return []

# --- ФУНКЦИЯ: ПРОВЕРКА ПОДАРКОВ У ОДНОГО ПОЛЬЗОВАТЕЛЯ ---
async def check_user_gifts(username):
    """Проверяет неулучшенные подарки у пользователя"""
    global client
    
    try:
        username = str(username).strip()
        if username.startswith('@'):
            username = username[1:]
        
        if not username or username.isdigit():
            return None, "Невалидный username"
        
        try:
            input_peer = await client.get_input_entity(username)
        except Exception as e:
            return None, f"Не найден"
        
        try:
            result = await client(functions.payments.GetSavedStarGiftsRequest(
                peer=input_peer,
                offset="",
                limit=100,
                exclude_unsaved=False,
                exclude_saved=False,
                exclude_upgradable=False,
                exclude_unupgradable=False
            ))
        except FloodWaitError as e:
            wait = e.seconds
            logger.warning(f"⏳ FloodWait {wait} сек для {username}")
            await asyncio.sleep(wait + 1)
            return await check_user_gifts(username)
        except Exception as e:
            return None, f"Ошибка API"
        
        # Считаем неулучшенные подарки
        count = 0
        if result and result.gifts:
            for gift_obj in result.gifts:
                if hasattr(gift_obj, 'upgrade_variants') and gift_obj.upgrade_variants:
                    count += 1
                elif hasattr(gift_obj, 'prepaid_upgrade_hash') and gift_obj.prepaid_upgrade_hash:
                    count += 1
        
        return count, None
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки {username}: {e}")
        return None, str(e)

# --- ФУНКЦИЯ: МАССОВАЯ ПРОВЕРКА С ЗАЩИТОЙ ---
async def check_users_batch(users, chat_id, user_id):
    """Проверяет список пользователей с защитой от флуда"""
    global scanning_users
    
    total_users = len(users)
    results = []
    checked = 0
    found_gifts = 0
    
    # Если пользователей больше MAX_USERS_TO_CHECK, берем случайных
    if total_users > MAX_USERS_TO_CHECK:
        random.shuffle(users)
        users = users[:MAX_USERS_TO_CHECK]
        await client.send_message(
            chat_id,
            f"⚠️ В чате {total_users} пользователей. Проверю {MAX_USERS_TO_CHECK} случайных."
        )
    
    await client.send_message(
        chat_id,
        f"🔍 Начинаю проверку {len(users)} пользователей...\n"
        f"⏱ Примерное время: ~{len(users) * 4} сек"
    )
    
    for i, user in enumerate(users):
        # Проверяем, не остановил ли пользователь
        if user_id in scanning_users and scanning_users[user_id].get("stopped"):
            await client.send_message(chat_id, "⏹️ Проверка остановлена.")
            return None
        
        username = f"@{user.username}"
        
        # Показываем прогресс каждые 10 пользователей
        if i % 10 == 0 or i == len(users) - 1:
            await client.send_message(
                chat_id,
                f"⏳ Прогресс: {i+1}/{len(users)} - проверяю..."
            )
        
        # Проверяем подарки
        count, error = await check_user_gifts(username)
        
        if error:
            logger.warning(f"❌ {username}: {error}")
        else:
            checked += 1
            if count and count > 0:
                found_gifts += count
                results.append((username, count))
                logger.info(f"🎁 Найден: {username} - {count} подарков")
        
        # Задержка между проверками
        await asyncio.sleep(get_delay())
        
        # Пауза после батча
        if (i + 1) % BATCH_SIZE == 0 and i < len(users) - 1:
            await client.send_message(
                chat_id,
                f"⏸️ Пауза {BATCH_PAUSE} сек (обработано {i+1}/{len(users)})"
            )
            await asyncio.sleep(BATCH_PAUSE)
    
    return results, checked, found_gifts

# --- ОСНОВНАЯ ФУНКЦИЯ СКАНИРОВАНИЯ ---
async def scan_chat(chat_link, chat_id, user_id):
    """Основная функция: парсинг чата + проверка подарков"""
    global scanning_users
    
    try:
        # --- 1. ПОЛУЧАЕМ ЧАТ ---
        if 't.me/' in chat_link:
            chat_username = chat_link.split('t.me/')[-1].strip('/')
        else:
            chat_username = chat_link.strip('/')
        
        if chat_username.startswith('@'):
            chat_username = chat_username[1:]
        
        await client.send_message(chat_id, f"🔍 Ищу чат: @{chat_username}...")
        
        try:
            entity = await client.get_entity(chat_username)
        except Exception as e:
            await client.send_message(chat_id, f"❌ Не могу найти чат: {e}")
            scanning_users.pop(user_id, None)
            return
        
        try:
            chat_name = entity.title
            await client.send_message(chat_id, f"✅ Найден чат: {chat_name}")
        except:
            await client.send_message(chat_id, f"✅ Чат найден (ID: {entity.id})")
        
        # --- 2. ПОЛУЧАЕМ УЧАСТНИКОВ ---
        await client.send_message(chat_id, "👥 Получаю список участников...")
        
        users = await get_chat_participants(entity)
        
        if not users:
            await client.send_message(chat_id, "❌ В чате нет пользователей с юзернеймами")
            scanning_users.pop(user_id, None)
            return
        
        await client.send_message(
            chat_id,
            f"✅ Найдено {len(users)} пользователей с юзернеймами"
        )
        
        # --- 3. ПРОВЕРЯЕМ ПОДАРКИ ---
        results, checked, found_gifts = await check_users_batch(users, chat_id, user_id)
        
        if results is None:
            return
        
        # --- 4. ФОРМИРУЕМ ОТЧЕТ ---
        lines = []
        lines.append("✅ **СКАНИРОВАНИЕ ЗАВЕРШЕНО!**")
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append(f"📊 Проверено пользователей: **{checked}**")
        lines.append(f"🎁 Найдено неулучшенных подарков: **{found_gifts}**")
        lines.append(f"👤 Найдено пользователей с подарками: **{len(results)}**")
        lines.append("━━━━━━━━━━━━━━━━━")
        
        if results:
            lines.append("")
            lines.append("**Пользователи с неулучшенными подарками:**")
            lines.append("")
            
            # Сортируем по количеству подарков
            results.sort(key=lambda x: x[1], reverse=True)
            
            for username, count in results:
                lines.append(f"✅ {username}: **{count}** 🎁")
        else:
            lines.append("")
            lines.append("ℹ️ Не найдено пользователей с неулучшенными подарками")
        
        await client.send_message(chat_id, "\n".join(lines))
        
    except FloodWaitError as e:
        wait = e.seconds
        await client.send_message(chat_id, f"⏳ Telegram просит подождать {wait} секунд")
    except Exception as e:
        logger.error(f"❌ Ошибка сканирования: {e}")
        logger.error(traceback.format_exc())
        await client.send_message(chat_id, f"❌ Ошибка: {str(e)[:200]}")
    
    finally:
        scanning_users.pop(user_id, None)

# --- ОБРАБОТЧИК СООБЩЕНИЙ ---
@client.on(events.NewMessage)
async def handler(event):
    global scanning_users
    
    try:
        if not event.is_private:
            return
        
        user_id = event.sender_id
        chat_id = event.chat_id
        text = event.message.text
        
        if not text:
            return
        
        logger.info(f"📩 Сообщение от {user_id}: {text[:50]}...")
        
        # --- КОМАНДА /help ---
        if text.lower() in ['/help', 'помощь']:
            await client.send_message(
                chat_id,
                "🤖 **Помощь**\n\n"
                "Отправь ссылку на **ГРУППУ** или **КАНАЛ**:\n"
                "`t.me/gift_chat`\n"
                "`@gift_chat`\n\n"
                "Бот соберет участников и проверит их подарки.\n\n"
                "⚠️ Внимание: проверка может занять много времени для больших чатов!\n"
                "Команды:\n"
                "`/stop` - остановить проверку"
            )
            return
        
        # --- КОМАНДА /stop ---
        if text.lower() in ['/stop', 'стоп']:
            if user_id in scanning_users:
                scanning_users[user_id]["stopped"] = True
                await client.send_message(chat_id, "⏹️ Останавливаю проверку...")
            else:
                await client.send_message(chat_id, "ℹ️ Нет активной проверки.")
            return
        
        # --- ПАРСИМ ССЫЛКУ И ЗАПУСКАЕМ СКАНИРОВАНИЕ ---
        chat_input = text.strip()
        
        # Проверяем, похоже ли на ссылку
        if 't.me/' not in chat_input and not chat_input.startswith('@'):
            await client.send_message(
                chat_id,
                "❌ Это не похоже на ссылку.\n\n"
                "Отправь ссылку в формате:\n"
                "`t.me/gift_chat`\n"
                "или `@gift_chat`"
            )
            return
        
        if user_id in scanning_users and scanning_users[user_id].get("status") == "active":
            await client.send_message(chat_id, "⏳ Уже идет сканирование. Дождись завершения.")
            return
        
        # Запускаем сканирование
        scanning_users[user_id] = {"status": "active", "stopped": False}
        asyncio.create_task(scan_chat(chat_input, chat_id, user_id))
        
    except Exception as e:
        logger.error(f"❌ Ошибка в handler: {e}")
        try:
            await client.send_message(event.chat_id, f"❌ Ошибка: {str(e)[:100]}")
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
        
        client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"❌ Ошибка Telethon: {e}")
        client_ready = False

# --- ГЛАВНЫЙ ЗАПУСК ---
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК ЮЗЕРБОТА ДЛЯ ПРОВЕРКИ ПОДАРКОВ")
    logger.info("=" * 60)
    logger.info("📌 Отправь ссылку на чат/канал")
    logger.info("📌 Бот найдет участников и проверит их подарки")
    logger.info("=" * 60)
    
    import threading
    telethon_thread = threading.Thread(target=start_telethon, daemon=True)
    telethon_thread.start()
    
    time.sleep(3)
    logger.info(f"🌐 Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
