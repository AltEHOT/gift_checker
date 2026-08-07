import os
import asyncio
import random
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

# --- КОНФИГУРАЦИЯ ---
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")

if not API_ID or not API_HASH:
    print("❌ API_ID и API_HASH не установлены!")
    print("Задай их в переменных окружения или впиши ниже")
    # Можно вписать прямо здесь для теста:
    # API_ID = 12345678
    # API_HASH = "ваш_hash"
    exit(1)

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

# --- ГЛАВНАЯ ФУНКЦИЯ ---
async def main():
    print("=" * 60)
    print("🔍 ТЕСТ ПОЛУЧЕНИЯ УЧАСТНИКОВ ЧАТА")
    print("=" * 60)
    print()
    
    # --- ВВЕДИ ССЫЛКУ НА ЧАТ ---
    chat_input = input("📎 Введи ссылку на чат (например, t.me/gift_chat или @gift_chat): ").strip()
    
    if not chat_input:
        print("❌ Ссылка не введена!")
        return
    
    # Извлекаем username из ссылки
    if 't.me/' in chat_input:
        chat_username = chat_input.split('t.me/')[-1].strip('/')
    else:
        chat_username = chat_input.strip('/')
    
    if chat_username.startswith('@'):
        chat_username = chat_username[1:]
    
    print(f"🔍 Обрабатываю чат: @{chat_username}")
    print()
    
    try:
        # --- 1. ПОЛУЧАЕМ ЧАТ ---
        print("📡 Получаю информацию о чате...")
        entity = await client.get_entity(chat_username)
        
        # Пытаемся получить название чата
        try:
            chat_title = entity.title
            print(f"✅ Название чата: {chat_title}")
        except:
            print(f"✅ ID чата: {entity.id}")
        
        print()
        
        # --- 2. ПОЛУЧАЕМ УЧАСТНИКОВ ---
        print("👥 Получаю список участников...")
        print("⏳ Это может занять несколько секунд...")
        print()
        
        participants = []
        offset = 0
        limit = 200
        
        # --- НАСТРОЙКА: СКОЛЬКО ПОЛЬЗОВАТЕЛЕЙ ВЫВЕСТИ ---
        MAX_DISPLAY = 10  # ← МЕНЯЙ ЭТО ЧИСЛО ДЛЯ БОЛЬШЕГО КОЛИЧЕСТВА
        
        while len(participants) < MAX_DISPLAY:
            try:
                chunk = await client.get_participants(
                    entity,
                    offset=offset,
                    limit=limit
                )
                
                if not chunk:
                    break
                
                # Фильтруем: только пользователи с юзернеймом, не боты
                for user in chunk:
                    if not user.is_bot and user.username:
                        participants.append(user)
                        # Если набрали нужное количество — выходим
                        if len(participants) >= MAX_DISPLAY:
                            break
                
                offset += limit
                
                # Пауза между порциями
                await asyncio.sleep(1)
                
            except FloodWaitError as e:
                wait = e.seconds
                print(f"⏳ FloodWait: жду {wait} секунд...")
                await asyncio.sleep(wait + 1)
                continue
            except Exception as e:
                print(f"❌ Ошибка получения участников: {e}")
                break
        
        # --- 3. ВЫВОДИМ РЕЗУЛЬТАТ ---
        print()
        print("=" * 60)
        print(f"✅ НАЙДЕНО ПОЛЬЗОВАТЕЛЕЙ: {len(participants)}")
        print("=" * 60)
        print()
        
        if not participants:
            print("❌ В чате не найдено пользователей с юзернеймами")
            return
        
        print("📋 СПИСОК ЮЗЕРНЕЙМОВ:")
        print()
        
        for i, user in enumerate(participants[:MAX_DISPLAY], 1):
            username = f"@{user.username}"
            first_name = user.first_name or ""
            last_name = user.last_name or ""
            name = f"{first_name} {last_name}".strip()
            
            if name:
                print(f"{i:2}. {username} — {name}")
            else:
                print(f"{i:2}. {username}")
        
        if len(participants) > MAX_DISPLAY:
            print()
            print(f"... и еще {len(participants) - MAX_DISPLAY} пользователей (не показаны)")
        
        print()
        print("=" * 60)
        print("✅ ГОТОВО!")
        print("=" * 60)
        
    except FloodWaitError as e:
        wait = e.seconds
        print(f"⏳ Ошибка: Telegram просит подождать {wait} секунд")
        print("Попробуй позже или используй другой чат")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        print()
        print("Возможные причины:")
        print("  1. Неправильная ссылка на чат")
        print("  2. Нет доступа к чату (чат приватный)")
        print("  3. Аккаунт не добавлен в чат")
        print("  4. Превышен лимит запросов (FloodWait)")
    
    finally:
        await client.disconnect()
        print()
        print("👋 Соединение закрыто")

# --- ЗАПУСК ---
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Отменено пользователем")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
