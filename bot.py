import asyncio
import os
from pyrogram import Client
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ВАШИ ДАННЫЕ (получите их на my.telegram.org)
API_ID = 30993809
API_HASH = "9f8a6194865005795b237ab95b4b0559"
BOT_TOKEN = "8534024087:AAE0MAIsHKoWjPA4cuqSKOubAlm7F0_xpG0"

# СПИСОК АККАУНТОВ ДЛЯ ПРОВЕРКИ (вставьте ваш список)
ACCOUNTS = [
    "@sirkapirkaw", "@sofuuha", "@nuwxkdr", "@mirzzevva", "@nasyaas11",
    "@your_mom_17", "@ttaiisii", "@mklovi", "@pooopssa67", "@Bobby13034",
    "@cf_oiya", "@Angel_ocnek", "@massh_axq69", "@ssba_5", "@Sasha35791",
    "@mn_2304g", "@sstaylo", "@Deis_MN", "@nixie0pixie", "@liizz28",
    "@Jelovek01", "@jdksmsmmsa", "@AlenaSurkova", "@dieu38_q", "@POKRUCHINN",
    "@Jaydg444", "@Anastasia_Sia20", "@mryayuu", "@liksoha", "@Holodilnik8",
    "@dzmiila", "@soomnea", "@polinka_I_I", "@meewQs", "@Sanfreeg",
    "@artiles2", "@vuvtuss", "@twsprkr", "@alm0st_nothing", "@lyubwx",
    "@gri_ksusha", "@brunetka9", "@mariiiela", "@heartsfw", "@lpauchokl",
    "@TonyHM6", "@krisi_st", "@alleeeka", "@Puddin7D", "@untoter",
    "@ystwith", "@FF9O01", "@lumaolq", "@talinix", "@dir2hades",
    "@llpnu", "@oshytik", "@cats2w", "@Karisa2008", "@def1f",
    "@sschaaq", "@darikswx104", "@aalin4iik", "@dka_rinaa", "@Marichk_13",
    "@yer58", "@ZEYT1K", "@Sashunya18", "@YMV_4", "@qwertxlee",
    "@anastasiax1203", "@dollinoll", "@mi_meowww", "@kcuuqwxx", "@sofiachmakina",
    "@Nikyla888", "@pivnamonashka", "@hhhqush", "@xmarihn", "@YuliaTrima",
    "@kkkkhrtuk", "@kimmiinaass", "@WanWany1", "@laurqiv", "@vvulia",
    "@maryashkacrash", "@byrachochok", "@iLkoOk08", "@senokosovaem", "@ww_kl0",
    "@nstxcw", "@kotteeeeewq", "@Vikss709", "@yullx", "@mariiankka",
    "@lasq_wx", "@zz_z_z_zzzz_zz", "@nik_945", "@kriwzxw", "@dzsrt"
]

# Функция проверки подарков у одного аккаунта
async def check_gifts(username):
    try:
        # Создаем временную сессию для проверки
        app = Client(
            "temp_session",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
        
        async with app:
            user = await app.get_users(username)
            gifts = await app.get_gifts(user.id)
            
            # Считаем подарки
            total_gifts = 0
            if gifts:
                total_gifts = len(gifts)
            
            return f"{username} - {total_gifts}"
    except Exception as e:
        return f"{username} - Ошибка: {str(e)}"

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 Начинаю проверку подарков...\n"
        "Это может занять несколько минут.\n"
        "Я проверю все аккаунты из списка!"
    )
    
    # Проверяем каждый аккаунт
    results = []
    for i, username in enumerate(ACCOUNTS, 1):
        result = await check_gifts(username)
        results.append(result)
        
        # Отправляем промежуточный результат каждые 10 аккаунтов
        if i % 10 == 0:
            await update.message.reply_text(f"✅ Проверено {i} из {len(ACCOUNTS)} аккаунтов")
    
    # Формируем финальный отчет
    report = "📊 **Результаты проверки подарков:**\n\n"
    report += "\n".join(results)
    
    # Отправляем результат (если слишком длинно - разбиваем на части)
    if len(report) > 4000:
        for x in range(0, len(report), 4000):
            await update.message.reply_text(report[x:x+4000])
    else:
        await update.message.reply_text(report)

# Запуск бота
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    print("Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()
