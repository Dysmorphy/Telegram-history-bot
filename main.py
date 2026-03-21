import os
import asyncio
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

load_dotenv()
bot_token = os.getenv("BOT_TOKEN")

bot = Bot(bot_token)
dp = Dispatcher()

async def send_daily(bot):
    # должно быть чтение базы данных с id пользователей, подписанных на рассылку
    # пока просто костыль в виде файла
    with open("id_db.txt") as file:
        chat_ids = [int(file.readline().strip())]
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, "Сообщение по расписанию (09:00).")
        except Exception as e:
            print(f"Failed to send to {chat_id}: {e}")


def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("Europe/Moscow"))
    scheduler.add_job(send_daily, CronTrigger(hour=9, minute=00), args=[bot])
    scheduler.start()
    return scheduler

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("Welcome")
    # когда пользователь отправляет впервые команду /start,
    # его id записывается в базу данных - он "подписывается" на рассылку
    with open("id_db.txt", 'w') as file:
        file.write(str(message.from_user.id))

@dp.message()
async def handle_unknown_command(message: Message):
    await message.answer("Неизвестная команда")


async def main():
    scheduler = setup_scheduler(bot)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())