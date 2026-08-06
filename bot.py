async def handle_new_message(event):
    """Обработчик новых сообщений с полной обработкой ошибок"""
    global client, user_data
    
    try:
        # Только личные сообщения
        if not event.is_private:
            return
        
        user_id = event.sender_id
        chat_id = event.chat_id
        text = event.message.text
        
        if not text:
            return
        
        logger.info(f"📩 Сообщение от {user_id}: {text[:50]}...")
        
        # --- ПОЛУЧАЕМ СУЩНОСТЬ ПОЛЬЗОВАТЕЛЯ (РЕШЕНИЕ ОШИБКИ) ---
        try:
            sender_entity = await client.get_entity(user_id)
        except Exception as e:
            logger.error(f"❌ Не могу получить entity для {user_id}: {e}")
            return
        
        # --- КОМАНДЫ ---
        if text.startswith('/'):
            if text.lower() in ["/stop", "стоп"]:
                if user_id in user_data:
                    user_data[user_id]["status"] = "stopped"
                    await client.send_message(sender_entity, "⏹️ Проверка остановлена.")
                return
            
            if text.lower() in ["/stats", "статистика"]:
                if user_id in user_data and user_data[user_id].get("status") == "active":
                    data = user_data[user_id]
                    total = len(data["usernames"])
                    current = data.get("index", 0)
                    await client.send_message(
                        sender_entity,
                        f"📊 **Прогресс:** {current}/{total}\n"
                        f"🎁 Найдено: {data.get('total_gifts', 0)}"
                    )
                else:
                    await client.send_message(sender_entity, "ℹ️ Нет активной проверки.")
                return
            
            if text.lower() in ["/help", "помощь"]:
                await client.send_message(
                    sender_entity,
                    "🤖 **Помощь**\n\n"
                    "Отправь список @username\n"
                    "Формат: @username1 - 1\n\n"
                    "Команды:\n"
                    "/stop - остановить проверку\n"
                    "/stats - показать прогресс\n"
                    "/help - эта справка"
                )
                return
            
            return
        
        # --- ПАРСИНГ СПИСКА ---
        if user_id in user_data and user_data[user_id].get("status") == "active":
            data = user_data[user_id]
            await client.send_message(
                sender_entity,
                f"⏳ Уже идет проверка: {data['index']}/{len(data['usernames'])}"
            )
            return
        
        # Извлекаем юзернеймы
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
            await client.send_message(
                sender_entity, 
                "❌ Не найдено @username\n\n"
                "Отправь список в формате:\n"
                "@username1 - 1\n"
                "@username2 - 2"
            )
            return
        
        if len(usernames) > 200:
            await client.send_message(
                sender_entity, 
                f"⚠️ Слишком много аккаунтов ({len(usernames)})\n"
                f"Максимум: 200 за раз"
            )
            return
        
        # Сохраняем данные
        user_data[user_id] = {
            "usernames": usernames,
            "index": 0,
            "status": "active",
            "start_time": time.time(),
            "total_gifts": 0,
            "chat_id": chat_id,
            "entity": sender_entity  # ← СОХРАНЯЕМ ENTITY
        }
        
        await client.send_message(
            sender_entity,
            f"✅ Получено {len(usernames)} аккаунтов.\n"
            f"⏱ Примерное время: ~{len(usernames) * 3} сек\n"
            f"🛡️ Защита от флуда: ВКЛ\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Для остановки: /stop\n"
            f"Для статистики: /stats"
        )
        
        # Запускаем обработку в отдельном потоке
        thread = threading.Thread(
            target=run_batch_sync, 
            args=(sender_entity, user_id)  # ← ПЕРЕДАЕМ ENTITY
        )
        thread.daemon = True
        thread.start()
        
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_new_message: {e}")
        logger.error(traceback.format_exc())
        try:
            # Пытаемся отправить ошибку, если есть entity
            if 'sender_entity' in locals():
                await client.send_message(
                    sender_entity,
                    f"❌ Внутренняя ошибка: {str(e)[:100]}"
                )
        except:
            pass
