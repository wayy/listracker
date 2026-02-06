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
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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
    selecting_category = State()

def get_item_category(name: str) -> str:
    name_lower = name.lower()
    if any(x in name_lower for x in ["case", "кейс", "пакет", "набор"]):
        return "📦 Кейсы"
    if "sticker |" in name_lower or "наклейка |" in name_lower:
        return "🎯 Наклейки"
    if any(x in name_lower for x in ["agent", "агент", "sir ", "professional"]):
        return "👤 Агенты"
    if any(x in name_lower for x in ["music kit", "набор музыки"]):
        return "🎵 Музыка"
    if any(x in name_lower for x in ["graffiti", "граффити"]):
        return "🎨 Граффити"
    if any(x in name_lower for x in ["patch", "нашивка"]):
        return "🧵 Нашивки"
    if any(x in name_lower for x in ["medal", "медаль", "coin", "монета"]):
        return "🏅 Медали"
    if "|" in name:
        return "🔫 Оружие"
    return "🛠 Прочее"

async def init_db():
    async with aiosqlite.connect("inventory.db") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, steam_id TEXT NOT NULL)")
        await db.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, market_hash_name TEXT UNIQUE, appid INTEGER, category TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS user_items (chat_id INTEGER, item_id INTEGER, PRIMARY KEY (chat_id, item_id))")
        await db.execute("CREATE TABLE IF NOT EXISTS prices (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, lowest_price REAL, timestamp DATETIME)")
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
                        await db.execute("INSERT INTO prices (item_id, lowest_price, timestamp) VALUES (?, ?, ?)", 
                                       (item_id, current_price, datetime.now()))
                        await db.commit()
                        await asyncio.sleep(AVG_PRICE_DELAY)
                    else:
                        await asyncio.sleep(5)
                        
        except Exception as e: logger.error(f"Loop error: {e}")
        await asyncio.sleep(30)

dp = Dispatcher()

def build_category_keyboard(categories_list):
    """Вспомогательная функция для сборки красивой клавиатуры"""
    buttons = []
    # Сортируем и группируем по 2 в ряд
    sorted_cats = sorted(list(categories_list))
    for i in range(0, len(sorted_cats), 2):
        row = [KeyboardButton(text=sorted_cats[i])]
        if i + 1 < len(sorted_cats):
            row.append(KeyboardButton(text=sorted_cats[i+1]))
        buttons.append(row)
    
    buttons.append([KeyboardButton(text="📊 Показать всё")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await message.answer(
        "👋 Привет! Я бот для отслеживания стоимости инвентаря CS2.\n\n"
        "Пришлите ссылку на профиль или ваш ID.\n"
        "⚠️ *Инвентарь должен быть публичным!*"
    )
    await state.set_state(Registration.waiting_for_steam_link)

@dp.message(Command("items"))
async def cmd_items_menu(message: Message, state: FSMContext):
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute(
            "SELECT DISTINCT i.category FROM items i "
            "JOIN user_items ui ON i.id = ui.item_id "
            "WHERE ui.chat_id = ?", (message.chat.id,)
        )
        categories = [c[0] for c in await res.fetchall()]
        
        if not categories:
            return await message.answer("Инвентарь пуст. Используйте /start для привязки.")
            
        markup = build_category_keyboard(categories)
        await message.answer("Выберите категорию предметов для просмотра:", reply_markup=markup)
        await state.set_state(Registration.selecting_category)

@dp.message(Registration.selecting_category)
async def show_category_items(message: Message, state: FSMContext):
    category = message.text
    chat_id = message.chat.id
    
    async with aiosqlite.connect("inventory.db") as db:
        if category == "📊 Показать всё":
            query = """
                SELECT i.market_hash_name, 
                (SELECT p.lowest_price FROM prices p WHERE p.item_id = i.id ORDER BY p.timestamp DESC LIMIT 1) as price,
                i.category
                FROM items i
                JOIN user_items ui ON i.id = ui.item_id
                WHERE ui.chat_id = ?
            """
            params = (chat_id,)
        else:
            query = """
                SELECT i.market_hash_name, 
                (SELECT p.lowest_price FROM prices p WHERE p.item_id = i.id ORDER BY p.timestamp DESC LIMIT 1) as price,
                i.category
                FROM items i
                JOIN user_items ui ON i.id = ui.item_id
                WHERE ui.chat_id = ? AND i.category = ?
            """
            params = (chat_id, category)

        res = await db.execute(query, params)
        rows = await res.fetchall()
        
        if not rows:
            return await message.answer("В этой категории ничего не найдено.", reply_markup=ReplyKeyboardRemove())

        count = len(rows)
        items_with_price = [r for r in rows if r[1] is not None]
        total_sum = sum([r[1] for r in items_with_price])
        priced_count = len(items_with_price)
        
        text = f"📂 *Категория:* {category}\n"
        text += f"📦 *Предметов:* `{count}`\n"
        text += f"💰 *Сумма:* `{total_sum:.2f} ₽` ({priced_count}/{count})\n\n"
        
        if priced_count < count:
            remaining = count - priced_count
            seconds = remaining * AVG_PRICE_DELAY
            time_str = str(timedelta(seconds=seconds)).split('.')[0]
            text += f"⏳ *Ожидание цен:* ~`{time_str}`\n\n"

        items_list = []
        for r in rows[:40]:
            p_text = f"{r[1]:.2f} ₽" if r[1] else "⏳"
            items_list.append(f"• `{r[0]}` — {p_text}")
            
        text += "\n".join(items_list)
        if count > 40: text += f"\n\n...и еще {count - 40} предметов."
        
        await message.answer(text, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        await state.clear()

@dp.message(Registration.waiting_for_steam_link)
async def process_link(message: Message, state: FSMContext):
    msg = await message.answer("🔄 Сканирую инвентарь...")
    steam_id = await resolve_steam_id(message.text)
    
    if not steam_id: return await msg.edit_text("❌ Не удалось распознать ID.")

    result = await fetch_inventory(steam_id, APP_ID)
    if result == "PRIVATE": return await msg.edit_text("❌ Ошибка доступа. Проверьте приватность инвентаря.")
    elif result == "RATE_LIMIT": return await msg.edit_text("⚠️ Ошибка 429 (Steam Limit). Подождите немного.")
    elif not result: return await msg.edit_text("⚠️ Предметы не найдены.")

    found_categories = set()
    async with aiosqlite.connect("inventory.db") as db:
        await db.execute("INSERT OR REPLACE INTO users (chat_id, steam_id) VALUES (?, ?)", (message.chat.id, steam_id))
        await db.execute("DELETE FROM user_items WHERE chat_id = ?", (message.chat.id,))
        
        for item_name in result:
            category = get_item_category(item_name)
            found_categories.add(category)
            
            await db.execute(
                "INSERT INTO items (market_hash_name, appid, category) VALUES (?, ?, ?) "
                "ON CONFLICT(market_hash_name) DO UPDATE SET category=excluded.category", 
                (item_name, APP_ID, category)
            )
            
            res = await db.execute("SELECT id FROM items WHERE market_hash_name = ?", (item_name,))
            row = await res.fetchone()
            if row:
                await db.execute("INSERT OR IGNORE INTO user_items (chat_id, item_id) VALUES (?, ?)", (message.chat.id, row[0]))
        await db.commit()
    
    markup = build_category_keyboard(found_categories)

    await msg.delete()
    await message.answer(
        f"✅ Инвентарь просканирован! Найдено предметов: `{len(result)}`.\n\n"
        f"Выберите категорию для просмотра списка:",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    await state.set_state(Registration.selecting_category)

async def main():
    await init_db()
    bot = Bot(token=TOKEN)
    asyncio.create_task(price_checker_loop(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
