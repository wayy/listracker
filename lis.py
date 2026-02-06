import asyncio
import logging
import re
import urllib.parse
import os
import sys
import subprocess
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ФУНКЦИЯ АВТО-УСТАНОВКИ
def install_missing_packages():
    packages = ["aiosqlite", "aiogram", "aiohttp", "python-dotenv"]
    for package in packages:
        try:
            module_name = "dotenv" if package == "python-dotenv" else package
            __import__(module_name)
        except ImportError:
            logger.info(f"Библиотека {package} не найдена. Пытаюсь установить...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])

install_missing_packages()

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN", "5070946103:AAFG8N40n9IPR3APhYxMeD-mB81-D7ss7Es")
APP_ID = int(os.getenv("APP_ID", 730))
CURRENCY = int(os.getenv("CURRENCY", 5)) # 5 = RUB
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 86400)) 

# Средняя задержка между запросами цен (в секундах) для расчета времени
AVG_PRICE_DELAY = 13 

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://steamcommunity.com/profiles/",
    "Connection": "keep-alive"
}

INVENTORY_BASE_URL = "https://steamcommunity.com/inventory/{steam_id}/{app_id}/2?l=russian&count=1000"
MARKET_BASE_URL = "https://steamcommunity.com/market/inventory/{steam_id}/{app_id}/2?l=russian"
PRICE_URL = "https://steamcommunity.com/market/priceoverview/?appid={app_id}&currency={currency}&market_hash_name={name}"
RESOLVE_ID_URL = "https://steamcommunity.com/id/{vanity_url}/?xml=1"

class Registration(StatesGroup):
    waiting_for_steam_link = State()

async def init_db():
    async with aiosqlite.connect("inventory.db") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, steam_id TEXT NOT NULL)")
        await db.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, market_hash_name TEXT UNIQUE, appid INTEGER)")
        await db.execute("CREATE TABLE IF NOT EXISTS user_items (chat_id INTEGER, item_id INTEGER, PRIMARY KEY (chat_id, item_id))")
        await db.execute("CREATE TABLE IF NOT EXISTS prices (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, lowest_price REAL, timestamp DATETIME)")
        await db.execute("CREATE TABLE IF NOT EXISTS alerts_state (chat_id INTEGER, item_id INTEGER, last_notified_price REAL, PRIMARY KEY (chat_id, item_id))")
        await db.commit()

async def resolve_steam_id(text):
    digit_match = re.search(r'\b(7656119\d{10})\b', text)
    if digit_match: return digit_match.group(1)
    profiles_match = re.search(r'steamcommunity\.com/profiles/(\d+)', text)
    if profiles_match: return profiles_match.group(1)
    vanity_match = re.search(r'steamcommunity\.com/id/([^/?\s]+)', text)
    if vanity_match:
        vanity_url = vanity_match.group(1)
        url = RESOLVE_ID_URL.format(vanity_url=vanity_url)
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            try:
                async with session.get(url) as resp:
                    content = await resp.text()
                    id_match = re.search(r'<steamID64>(\d+)</steamID64>', content)
                    if id_match: return id_match.group(1)
            except Exception as e: logger.error(f"Error resolving vanity: {e}")
    return None

async def fetch_inventory(steam_id: str, app_id: int) -> list[str] | str | None:
    result = await _request_paginated_inventory(INVENTORY_BASE_URL, steam_id, app_id)
    if result is None or (isinstance(result, list) and not result):
        result = await _request_paginated_inventory(MARKET_BASE_URL, steam_id, app_id)
    return result

async def _request_paginated_inventory(base_url: str, steam_id: str, app_id: int):
    items = []
    start_assetid = None
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        while True:
            url = base_url.format(steam_id=steam_id, app_id=app_id)
            if start_assetid: url += f"&start_assetid={start_assetid}"
            try:
                async with session.get(url, timeout=25) as resp:
                    if resp.status == 403: return "PRIVATE"
                    if resp.status == 429: return "RATE_LIMIT"
                    if resp.status != 200: return None
                    data = await resp.json()
            except Exception: return None

            assets = data.get("assets", [])
            descriptions = data.get("descriptions", [])
            if not assets or not descriptions: break
            desc_map = {(d["classid"], d["instanceid"]): d for d in descriptions}
            for asset in assets:
                key = (asset["classid"], asset["instanceid"])
                desc = desc_map.get(key)
                if desc and (desc.get("marketable") == 1 or desc.get("marketable") is True):
                    items.append(desc["market_hash_name"])
            if not data.get("more_items"): break
            start_assetid = data.get("last_assetid")
            await asyncio.sleep(1.5)
    return items if items else []

async def get_item_price(name, app_id):
    encoded_name = urllib.parse.quote(name)
    url = PRICE_URL.format(app_id=app_id, currency=CURRENCY, name=encoded_name)
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status == 429: return "RATE_LIMIT"
                if resp.status != 200: return None
                data = await resp.json()
                if data.get('success') and data.get('lowest_price'):
                    p_str = data['lowest_price'].replace(',', '.').replace('\xa0', '').replace(' ', '')
                    p_val = "".join(filter(lambda x: x.isdigit() or x == '.', p_str))
                    return float(p_val) if p_val else None
        except Exception: return None
    return None

async def price_checker_loop(bot: Bot):
    while True:
        try:
            async with aiosqlite.connect("inventory.db") as db:
                query = """
                SELECT i.id, i.market_hash_name 
                FROM items i
                LEFT JOIN prices p ON i.id = p.item_id
                GROUP BY i.id
                ORDER BY MAX(p.timestamp) ASC
                LIMIT 40
                """
                async with db.execute(query) as cursor:
                    items_to_update = await cursor.fetchall()
                
                for item_id, name in items_to_update:
                    current_price = await get_item_price(name, APP_ID)
                    
                    if current_price == "RATE_LIMIT":
                        await asyncio.sleep(60)
                        break 
                    
                    if current_price and isinstance(current_price, float):
                        res = await db.execute("SELECT lowest_price FROM prices WHERE item_id = ? ORDER BY timestamp DESC LIMIT 1", (item_id,))
                        last_price_row = await res.fetchone()
                        
                        await db.execute("INSERT INTO prices (item_id, lowest_price, timestamp) VALUES (?, ?, ?)", 
                                       (item_id, current_price, datetime.now()))
                        await db.commit()

                        if last_price_row and current_price > last_price_row[0]:
                            diff = current_price - last_price_row[0]
                            async with db.execute("SELECT chat_id FROM user_items WHERE item_id = ?", (item_id,)) as u_cursor:
                                user_chats = await u_cursor.fetchall()
                                for (chat_id,) in user_chats:
                                    try:
                                        await bot.send_message(
                                            chat_id, 
                                            f"📈 *Цена выросла!*\n\n📦 `{name}`\n💰 {last_price_row[0]:.2f} -> {current_price:.2f} ₽\n➕ Разница: +{diff:.2f} ₽",
                                            parse_mode="Markdown"
                                        )
                                    except Exception: pass
                        
                        await asyncio.sleep(AVG_PRICE_DELAY)
                    else:
                        await asyncio.sleep(5)
                        
        except Exception as e: logger.error(f"Loop error: {e}")
        await asyncio.sleep(30)

dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await message.answer(
        "👋 Привет! Я бот для отслеживания стоимости инвентаря CS2.\n\n"
        "Пришлите ссылку на профиль или ваш ID.\n"
        "⚠️ *Инвентарь должен быть публичным!*"
    )
    await state.set_state(Registration.waiting_for_steam_link)

@dp.message(Command("items"))
async def cmd_items(message: Message):
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute(
            """
            SELECT i.market_hash_name, 
            (SELECT p.lowest_price FROM prices p WHERE p.item_id = i.id ORDER BY p.timestamp DESC LIMIT 1) as price
            FROM items i
            JOIN user_items ui ON i.id = ui.item_id
            WHERE ui.chat_id = ?
            """, (message.chat.id,)
        )
        rows = await res.fetchall()
        if not rows:
            return await message.answer("Список предметов пуст. Сначала привяжите профиль через /start")
        
        count = len(rows)
        items_with_price = [r for r in rows if r[1] is not None]
        total_sum = sum([r[1] for r in items_with_price])
        priced_count = len(items_with_price)
        
        text = f"📦 *Ваши предметы ({count}):*\n"
        text += f"💰 *Оцененная стоимость:* `{total_sum:.2f} ₽` ({priced_count}/{count})\n\n"
        
        if priced_count < count:
            remaining = count - priced_count
            # Расчет оставшегося времени
            seconds = remaining * AVG_PRICE_DELAY
            time_str = str(timedelta(seconds=seconds)).split('.')[0]
            text += f"⏳ *Ожидание загрузки цен:* ~`{time_str}`\n\n"

        items_list = []
        for r in rows[:35]:
            p_text = f"{r[1]:.2f} ₽" if r[1] else "⏳ _загрузка..._"
            items_list.append(f"• `{r[0]}` — {p_text}")
            
        text += "\n".join(items_list)
        if count > 35: text += f"\n\n...и еще {count - 35} предметов."
        
        await message.answer(text, parse_mode="Markdown")

@dp.message(Registration.waiting_for_steam_link)
async def process_link(message: Message, state: FSMContext):
    msg = await message.answer("🔄 Сканирую инвентарь...")
    steam_id = await resolve_steam_id(message.text)
    
    if not steam_id: return await msg.edit_text("❌ Не удалось распознать ID.")

    result = await fetch_inventory(steam_id, APP_ID)
    if result == "PRIVATE": return await msg.edit_text("❌ Ошибка доступа. Проверьте приватность инвентаря.")
    elif result == "RATE_LIMIT": return await msg.edit_text("⚠️ Ошибка 429 (Steam Limit). Подождите немного.")
    elif not result: return await msg.edit_text("⚠️ Предметы не найдены.")

    total_items = len(result)
    seconds = total_items * AVG_PRICE_DELAY
    time_str = str(timedelta(seconds=seconds)).split('.')[0]

    await msg.edit_text(f"✅ Найдено предметов: {total_items}.\nДобавляю в очередь на оценку.")

    async with aiosqlite.connect("inventory.db") as db:
        await db.execute("INSERT OR REPLACE INTO users (chat_id, steam_id) VALUES (?, ?)", (message.chat.id, steam_id))
        await db.execute("DELETE FROM user_items WHERE chat_id = ?", (message.chat.id,))
        for item_name in result:
            await db.execute("INSERT OR IGNORE INTO items (market_hash_name, appid) VALUES (?, ?)", (item_name, APP_ID))
            res = await db.execute("SELECT id FROM items WHERE market_hash_name = ?", (item_name,))
            row = await res.fetchone()
            if row:
                await db.execute("INSERT OR IGNORE INTO user_items (chat_id, item_id) VALUES (?, ?)", (message.chat.id, row[0]))
        await db.commit()
    
    await state.clear()
    await msg.edit_text(
        f"📊 *Инвентарь успешно привязан!*\n\n"
        f"Всего предметов: `{total_items}`.\n"
        f"Приблизительное время полной оценки: ~`{time_str}`.\n\n"
        f"Бот загружает цены в фоновом режиме по 1 предмету каждые 13 секунд (лимит Steam).\n"
        f"Следите за прогрессом через /items."
    , parse_mode="Markdown")

async def main():
    await init_db()
    bot = Bot(token=TOKEN)
    asyncio.create_task(price_checker_loop(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
