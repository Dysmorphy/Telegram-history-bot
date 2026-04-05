import os
from dotenv import load_dotenv

import logging

import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import gspread_asyncio
from google.oauth2.service_account import Credentials

from datetime import date
from zoneinfo import ZoneInfo


load_dotenv("credentials/.env")
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_CREDS_FILE = "credentials/service_acc.json"
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME")
TIMEZONE = os.getenv("TIMEZONE")

logging.basicConfig(level=logging.INFO)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_creds():
    return Credentials.from_service_account_file(
        GOOGLE_CREDS_FILE,
        scopes=SCOPES
    )

agcm = gspread_asyncio.AsyncioGspreadClientManager(get_creds)

async def get_worksheet(sheet_name: str):
    client = await agcm.authorize()
    spreadsheet = await client.open(SPREADSHEET_NAME)
    worksheet = await spreadsheet.worksheet(sheet_name)
    return worksheet

def normalize_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "да"}

async def register_subscriber(message: Message):
    ws = await get_worksheet("subscribers")
    records = await ws.get_all_records()

    user_id = str(message.from_user.id)
    username = message.from_user.username or ""

    for idx, row in enumerate(records, start=2):
        if str(row.get("chat_id", "")).strip() == user_id:
            await ws.update_acell(f"C{idx}", "TRUE")  # active
            return

    await ws.append_row([user_id, username, "TRUE"])

@dp.message(Command("unsubscribe"))
async def unsubscribe(message: Message):
    ws = await get_worksheet("subscribers")
    rows = await ws.get_all_records()
    user_id = str(message.from_user.id)

    for idx, row in enumerate(rows, start=2):
        if str(row.get("chat_id", "")).strip() == user_id:
            await ws.update_acell(f"C{idx}", "FALSE")
            await message.answer("Вы отписаны от рассылки.")
            return

    await message.answer("Вы не были подписаны.")

async def get_active_subscribers():
    ws = await get_worksheet("subscribers")
    rows = await ws.get_all_records()

    subscribers = []
    for row in rows:
        if normalize_bool(row.get("active", True)):
            chat_id = str(row.get("chat_id", "")).strip()
            if chat_id:
                subscribers.append(int(chat_id))
    return subscribers

async def send_today_mailings(bot: Bot):
    today_str = date.today().strftime("%d.%m.%Y")
    ws = await get_worksheet("mailings")
    rows = await ws.get_all_records()
    for row_index, row in enumerate(rows, start=2):
        row_date = str(row.get("date", "")).strip()
        title = str(row.get("title", "")).strip()
        body = str(row.get("body", "")).strip()
        media_url = str(row.get("media_url", "")).strip()
        tag = str(row.get("tag", "")).strip()
        sent = normalize_bool(row.get("sent", False))
        if sent:
            continue

        if row_date != today_str:
            continue

        if not title or not body:
            logging.warning("Пустой текст сообщения в строке %s", row_index)
            continue

        try:
            subscribers = await get_active_subscribers()
            for subscriber_chat_id in subscribers:
                try:
                    # TODO: сделать функцию-шаблон для формирования сообщения
                    await bot.send_message(subscriber_chat_id, title + "\n" + body)
                except Exception as e:
                    logging.exception(
                        "Ошибка отправки пользователю %s: %s",
                        subscriber_chat_id,
                        e
                    )
            logging.info("Отправлено всем активным подписчикам: row=%s", row_index)

            # отключено до деплоя (мб можно вообще убрать обработку уже отправленных)
            # await ws.update_acell(f"F{row_index}", "TRUE")  # sent

        except Exception as e:
            logging.exception("Ошибка обработки строки %s: %s", row_index, e)


def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(TIMEZONE))
    scheduler.add_job(
        send_today_mailings,
        CronTrigger(hour=13, minute=59, timezone=ZoneInfo(TIMEZONE)),
        args=[bot],
        id="daily_mailing_job",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await register_subscriber(message)
    await message.answer("Welcome")

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