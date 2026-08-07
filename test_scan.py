import os
import sys
import asyncio
import base64
import random
import time
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession

# --- НАСТРОЙКА ---
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
PORT = int(os.getenv("PORT", 8080))

# --- НАСТРОЙКИ АНТИ-ФЛУДА ---
MIN_DELAY = 2.0          # Минимальная задержка между запросами (сек)
MAX_DELAY = 4.0          # Максимальная задержка между запросами (сек)
MAX_USERS_TO_DISPLAY = 10  # Сколько пользователей вывести

# --- ПРОВЕРКА API ДАННЫХ ---
if not API_ID or not API_HASH:
    print("❌ API_ID и API_HASH не установлены!")
    print("Задай их в переменных окружения или впиши ниже:")
    # Можно вписать прямо здесь для теста:
    # API_ID = 12345678
    # API_HASH = "ваш_hash"
    sys.exit(1)

# --- ПОДГОТОВКА СЕССИИ (ПОДДЕРЖКА BASE64) ---
print("=" * 60)
print("🔍 ТЕСТ ПОЛУЧЕНИЯ УЧАСТНИКОВ ЧАТА")
print("=" * 60)
print()

# 1. ПРОВЕРЯЕМ SESSION_STRING (из переменных окружения)
if SESSION_STRING and SESSION_STRING not in ["None", "NONE", "none", ""]:
    print("🔑 Использую StringSession")
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# 2. ПРОВЕРЯЕМ ФАЙЛ session.b64 (из Secret Files или локально)
elif os.path.exists("session.b64"):
    print("📁 Декодирую session.b64...")
    try:
        with open("session.b64", "r") as f:
            b64_data = f.read().strip()
        with open("userbot_session.session", "wb") as f:
            f.write(base64.b64decode(b64_data))
        print("✅ Сессия декодирована из Base64")
        client = TelegramClient("userbot_session", API_ID, API_HASH)
    except Exception as e:
        print(f"❌ Ошибка декодирования session.b64: {e}")
        sys.exit(1)

# 3. ПРОВЕРЯЕМ ОБЫЧНЫЙ ФАЙЛ СЕССИИ
elif os.path.exists("userbot_session.session"):
    print("📁 Использую файл сессии userbot_session.session")
    client = TelegramClient("userbot_session", API_ID, API_HASH)

else:
    print("❌ Не найдена сессия!")
    print()
    print("Добавь один из вариантов:")
    print("  1. SESSION_STRING в переменные окружения")
    print("  2. session.b64 в папке проекта")
    print("  3. userbot_session.session в папке проекта")
    print()
    print("Для создания сессии используй login.py")
    sys.exit(1)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_delay():
    """Возвращает случайную задержку между запросами"""
    return random.uniform(MIN_DELAY, MAX_DELAY)

async def safe_get_participants(entity, offset=0, limit=200, retry_count=0):
    """Безопасно получает участников с защитой от флуда"""
    global client
    
    try:
        # Случайная задержка перед запросом (имитация человека)
        await asyncio.sleep(get_delay())
        
        chunk = await client.get_participants(
            entity,
            offset=offset,
            limit=limit
        )
        return chunk, None
        
    except FloodWaitError as e:
        wait_time = e.seconds
        print(f"⏳ FloodWait: жду {wait_time} секунд...")
        await asyncio.sleep(wait_time + 1)
        if retry_count < 3:
            return await safe_get_participants(entity, offset, limit, retry_count + 1)
        else:
            return None, f"Превышено количество попыток: {wait_time} сек"
            
    except Exception as e:
        return None, str(e)

# --- ГЛАВНАЯ ФУНКЦИЯ ---
async def main():
    print("📎 Введи ссылку на чат (например, t.me/gift_chat или @gift_chat): ", end="")
    chat_input = input().strip()
    
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
    
    print()
    print(f"🔍 Обрабатываю чат: @{chat_username}")
    print()
    
    try:
        # --- 1. ПОЛУЧАЕМ ЧАТ ---
        print("📡 Получаю информацию о чате...")
        try:
            entity = await client.get_entity(chat_username)
        except Exception as e:
            print(f"❌ Не могу найти чат: {e}")
            print()
            print("Возможные причины:")
            print("  1. Неправильная ссылка")
            print("  2. Чат приватный (нужно быть участником)")
            print("  3. Аккаунт заблокирован или ограничен")
            return
        
        try:
            chat_title = entity.title
            print(f"✅ Название чата: {chat_title}")
        except:
            print(f"✅ ID чата: {entity.id}")
        
        print()
        
        # --- 2. ПОЛУЧАЕМ УЧАСТНИКОВ С ЗАЩИТОЙ ---
        print("👥 Получаю список участников...")
        print("⏳ Это может занять время...")
        print()
        
        all_users = []
        offset = 0
        limit = 200
        total_loaded = 0
        
        print("🔄 Загружаю участников...")
        
        while len(all_users) < MAX_USERS_TO_DISPLAY:
            chunk, error = await safe_get_participants(entity, offset, limit)
            
            if error:
                print(f"❌ Ошибка: {error}")
                break
            
            if not chunk:
                break
            
            # Фильтруем: только пользователи с юзернеймом, не боты
            for user in chunk:
                if not user.is_bot and user.username:
                    all_users.append(user)
                    # Если набрали нужное количество — выходим
                    if len(all_users) >= MAX_USERS_TO_DISPLAY:
                        break
            
            total_loaded += len(chunk)
            offset += limit
            
            # Показываем прогресс
            print(f"   Загружено: {total_loaded} участников, найдено с юзернеймами: {len(all_users)}")
            
            # Если загрузили много, но пользователей с юзернеймами мало
            if total_loaded >= 1000 and len(all_users) == 0:
                print()
                print("⚠️ В чате нет пользователей с юзернеймами (только скрытые профили)")
                break
        
        # --- 3. ВЫВОДИМ РЕЗУЛЬТАТ ---
        print()
        print("=" * 60)
        print(f"📊 РЕЗУЛЬТАТЫ СКАНИРОВАНИЯ")
        print("=" * 60)
        print()
        
        if not all_users:
            print("❌ В чате не найдено пользователей с юзернеймами")
            print()
            print("Возможные причины:")
            print("  1. В чате нет активных пользователей с юзернеймами")
            print("  2. Чат имеет ограничения на просмотр участников")
            return
        
        # Выводим первых MAX_USERS_TO_DISPLAY
        display_count = min(MAX_USERS_TO_DISPLAY, len(all_users))
        
        print(f"✅ Найдено пользователей с юзернеймами: {len(all_users)}")
        print()
        print(f"📋 Первые {display_count} юзернеймов:")
        print()
        
        for i, user in enumerate(all_users[:display_count], 1):
            username = f"@{user.username}"
            first_name = user.first_name or ""
            last_name = user.last_name or ""
            name = f"{first_name} {last_name}".strip()
            
            if name:
                print(f"{i:2}. {username} — {name}")
            else:
                print(f"{i:2}. {username}")
        
        if len(all_users) > display_count:
            print()
            print(f"... и еще {len(all_users) - display_count} пользователей (не показаны)")
        
        print()
        print("=" * 60)
        print("✅ СКАНИРОВАНИЕ ЗАВЕРШЕНО!")
        print("=" * 60)
        print()
        print("💡 Чтобы проверить подарки у этих пользователей,")
        print("   скопируй их юзернеймы и отправь в бот списком.")
        
    except FloodWaitError as e:
        wait = e.seconds
        print(f"⏳ Ошибка: Telegram просит подождать {wait} секунд")
        print("Попробуй позже или используй другой чат")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        print()
        print("Полный текст ошибки для диагностики:")
        import traceback
        traceback.print_exc()
    
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
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
