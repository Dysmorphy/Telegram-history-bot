import random

import os
from dotenv import load_dotenv

import logging
import aiogram.types as types

import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram import F
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
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

async def register_subscriber(message: CallbackQuery | Message):
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
async def unsubscribe(message: CallbackQuery | Message):
    ws = await get_worksheet("subscribers")
    rows = await ws.get_all_records()
    user_id = str(message.from_user.id)

    for idx, row in enumerate(rows, start=2):
        if str(row.get("chat_id", "")).strip() == user_id:
            await ws.update_acell(f"C{idx}", "FALSE")
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


class Form(StatesGroup):
    waiting_for_date = State()


@dp.message(Command("calendar"))
async def calendar(message:Message,state: FSMContext):
    await message.answer("Введите дату в формате ДД.ММ (например: 03.04)")
    await state.set_state(Form.waiting_for_date)

@dp.message(Form.waiting_for_date)
async def process_date(message: Message, state: FSMContext):
    chosen_date = message.text
    chosen_month = int(chosen_date[3:])


    ws = await get_worksheet("mailings")
    rows = await ws.get_all_records()

    title_prefix = "Вот событие, произошедшее в данную дату:"
    failure_text = "По заданной вами дате ничего не найдено"
    len_date = 8
    found = False

    for row in rows:
        if not row["date"] or len(row["date"]) != len_date:

            if len(row["date"]) != len_date and len(row["date"]):
                logging.warning("Неправильный формат даты")
            continue
        day_month = row["date"][:-3]
        curr_month = int(day_month [3:])
        if day_month == chosen_date:
            title = row["title"]
            body = row["body"]
            media_url = row["media_url"]
            video_url = row["video_url"]
            answer = f"{title_prefix} \n \n{form_message(title,body,media_url,video_url)}"
            await message.answer(answer,reply_markup=keyboard)
            found = True
            break
    if not found:
        await message.answer(failure_text,reply_markup=keyboard)
    await state.clear()

            


    

def form_message(title,body,media_url,video_url):
    url_message = f"Вот еще информация по этому событию: {media_url}"

    if video_url:
        video_message = f"Ссылка на видео: {video_url}"
        message = f"{title} \n \n{body} \n \n{url_message} \n \n{video_message}"
    else:
        message = f"{title} \n \n{body} \n \n{url_message}"
    return message

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
        video_url = str(row.get("video_url", "")).strip()
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
                    message_text = form_message(title,body,media_url,video_url)
                    await bot.send_message(subscriber_chat_id,message_text)
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

#adding a keyboard for future use in callback and start functions
kb = [
        [types.InlineKeyboardButton(text="🚀 Случайное событие", callback_data="random")],
        [types.InlineKeyboardButton(text="📡 Подписаться", callback_data="subscribe")],
        [types.InlineKeyboardButton(text="🔕 Отписаться", callback_data="unsubscribe")],
        [types.InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="help")]
    ]
keyboard = types.InlineKeyboardMarkup(inline_keyboard=kb)


@dp.message(CommandStart())
async def cmd_start(message: Message,keyboard = keyboard):

    entry_text= """Привет! 👋
Это бот про космическую гонку 1955–1975 годов.

Я присылаю реальные события в те дни, когда они произошли — запуски, аварии, первые полёты и т.д.

👇 Что можно сделать:
• получить случайное событие
• включить или отключить ежедневные сообщения
• узнать подробнее, как работает бот"""

    await message.answer(entry_text,reply_markup=keyboard)


@dp.callback_query(F.data == "subscribe")
async def subscribe_response(callback: CallbackQuery,keyboard = keyboard):
    await register_subscriber(callback)
    text = """Готово 👍
Теперь ты будешь получать события космической гонки в реальные даты
"""
    await callback.message.answer(text,reply_markup=keyboard)


@dp.callback_query(F.data == "unsubscribe")
async def unsubscribe_response(callback: CallbackQuery,keyboard = keyboard):
    await unsubscribe(callback)
    text = """Ты отписался.
Жаль. Впереди ещё много интересных событий.
Если передумаешь — всегда можно вернуться
"""
    await callback.message.answer(text,reply_markup=keyboard)


@dp.callback_query(F.data == "random")
async def random_response(callback: CallbackQuery,keyboard = keyboard):
    article = await get_random_message()
    await callback.message.answer(article, reply_markup=keyboard)


@dp.callback_query(F.data == "help")
async def help_response(callback: CallbackQuery,keyboard = keyboard):
    text = """ℹ️ Как работает бот

Бот показывает события космической гонки (1955–1975) по датам.

📅 Если включена подписка — ты получаешь сообщения в те дни, когда события реально произошли.
Иногда их может быть несколько, иногда — ни одного.

📡 Формат простой:
короткое описание события + иногда дополнительный контекст и медиа.
"""
    await callback.message.answer(text,reply_markup=keyboard)




async def get_random_message():
    sheet = await get_worksheet("mailings")
    rows = await sheet.get_all_records()
    filtered_rows = [row for row in rows if row["title"]]
    num_elements = len(filtered_rows)
    chosen_article_idx = random.randint(0,num_elements-1)

    title_prefix = "Конечно, вот случайная новость из космической гонки: \n \n"

    title = f"{title_prefix}{filtered_rows[chosen_article_idx]["title"]}"
    body = filtered_rows[chosen_article_idx]["body"] 
    media_url = filtered_rows[chosen_article_idx]["media_url"]
    video_url = filtered_rows[chosen_article_idx]["video_url"]

    message_text = form_message(title,body,media_url,video_url)
    return message_text


@dp.message()
async def handle_unknown_command(message: Message):
    await message.answer("Неизвестная команда")






async def test():
    sheet = await get_worksheet("mailings")
    rows = await sheet.get_all_records()
    for dict in rows:
        if dict["date"]:
            print(dict)


async def main():
    scheduler = setup_scheduler(bot)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())