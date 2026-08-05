from telethon import TelegramClient

api_id = 30993809  # Ваш API_ID
api_hash = "9f8a6194865005795b237ab95b4b0559"  # Ваш API_HASH

client = TelegramClient("my_session", api_id, api_hash)
client.start()
print("✅ Сессия создана!")
client.disconnect()
