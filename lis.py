import asyncio
import logging
import re
import urllib.parse
import os
import sys
import subprocess
from datetime import datetime

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
CURRENCY = int(os.getenv("CURRENCY", 5))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 86400))

# Заголовки для имитации браузера (Steam это любит)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/{app_id}/2?l=english&count=5000"
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
    digit_match = re.search(r'7656119\d{10}', text)
    if digit_match:
        return digit_match.group(0)

    vanity_match = re.search(r'steamcommunity\.com/id/([^/?\s]+)', text)
    if vanity_match:
        vanity_url = vanity_match.group(1)
        url = RESOLVE_ID_URL.format(vanity_url=vanity_url)
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            try:
                async with session.get(url) as resp:
                    content = await resp.text()
                    id_match = re.search(r'<steamID64>(\d+)</steamID64>', content)
                    if id_match:
                        return id_match.group(1)
            except Exception as e:
                logger.error(f"Ошибка резолвинга: {e}")
    return None

async def fetch_inventory(steam_id, app_id):
    url = INVENTORY_URL.format(steam_id=steam_id, app_id=app_id)
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status == 403:
                    logger.warning(f"Доступ к инвентарю {steam_id} запрещен (403).")
                    return "PRIVATE"
                if resp.status == 429:
                    logger.warning("Steam заблокировал запросы (429 - Too Many Requests).")
                    return "RATE_LIMIT"
                if resp.status != 200:
                    logger.error(f"Ошибка Steam API: статус {resp.status}")
                    return None
                
                data = await resp.json()
                if not data:
                    return []
                
                # Собираем уникальные имена предметов
                items = []
                descriptions = data.get('descriptions', [])
                for item in descriptions:
                    if item.get('marketable') == 1 or item.get('marketable') is True:
                        items.append(item['market_hash_name'])
                
                return list(set(items))
        except Exception as e:
            logger.error(f"Ошибка инвентаря: {e}")
            return None

async def get_item_price(name, app_id):
    encoded_name = urllib.parse.quote(name)
    url = PRICE_URL.format(app_id=app_id, currency=CURRENCY, name=encoded_name)
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            async with session.get(url) as resp:
                if resp.status == 429:
                    await asyncio.sleep(20)
                    return None
                data = await resp.json()
                if data.get('success') and data.get('lowest_price'):
                    p_str = data['lowest_price'].replace(',', '.').replace('\xa0', '').replace(' ', '')
                    p_val = "".join(filter(lambda x: x.isdigit() or x == '.', p_str))
                    return float(p_val) if p_val else None
        except:
            return None
    return None

async def price_checker_loop(bot: Bot):
    while True:
        logger.info("Цикл проверки цен...")
        try:
            async with aiosqlite.connect("inventory.db") as db:
                async with db.execute("SELECT chat_id, steam_id FROM users") as u_cursor:
                    users = await u_cursor.fetchall()
                
                for chat_id, steam_id in users:
                    items = await fetch_inventory(steam_id, APP_ID)
                    if not isinstance(items, list) or len(items) == 0: continue

                    for name in items:
                        await db.execute("INSERT OR IGNORE INTO items (market_hash_name, appid) VALUES (?, ?)", (name, APP_ID))
                        await db.commit()
                        res = await db.execute("SELECT id FROM items WHERE market_hash_name = ?", (name,))
                        item_row = await res.fetchone()
                        if not item_row: continue
                        item_id = item_row[0]
                        
                        await db.execute("INSERT OR IGNORE INTO user_items (chat_id, item_id) VALUES (?, ?)", (chat_id, item_id))
                        
                        current_price = await get_item_price(name, APP_ID)
                        if not current_price: continue

                        res = await db.execute("SELECT lowest_price FROM prices WHERE item_id = ? ORDER BY timestamp DESC LIMIT 1", (item_id,))
                        last_price_row = await res.fetchone()
                        last_price = last_price_row[0] if last_price_row else current_price

                        if current_price > last_price:
                            diff = current_price - last_price
                            try:
                                await bot.send_message(
                                    chat_id, 
                                    f"🚀 *Цена выросла!*\n\n📦 `{name}`\n💰 {last_price:.2f} -> {current_price:.2f} ₽\n📈 +{diff:.2f} ₽",
                                    parse_mode="Markdown"
                                )
                            except: pass

                        await db.execute("INSERT INTO prices (item_id, lowest_price, timestamp) VALUES (?, ?, ?)", (item_id, current_price, datetime.now()))
                        await db.commit()
                        await asyncio.sleep(5) # Задержка между предметами
        except Exception as e:
            logger.error(f"Ошибка цикла: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await message.answer("👋 Привет! Отправьте ссылку на ваш Steam профиль.\n\n⚠️ Убедитесь, что ваш инвентарь **ОТКРЫТ** в настройках приватности Steam.")
    await state.set_state(Registration.waiting_for_steam_link)

@dp.message(Registration.waiting_for_steam_link)
async def process_link(message: Message, state: FSMContext):
    msg = await message.answer("⏳ Проверяю доступ к Steam...")
    steam_id = await resolve_steam_id(message.text)
    
    if not steam_id:
        return await msg.edit_text("❌ Ссылка не распознана. Отправьте ссылку на профиль или SteamID64.")

    result = await fetch_inventory(steam_id, APP_ID)
    
    if result == "PRIVATE":
        return await msg.edit_text(
            "❌ *Steam сообщает, что инвентарь скрыт.*\n\n"
            "1. Зайдите в настройки приватности Steam.\n"
            "2. Проверьте, что 'Инвентарь' стоит **Открытый**.\n"
            "3. Убедитесь, что галочка 'Скрывать подарки' **СНЯТА**.",
            parse_mode="Markdown"
        )
    elif result == "RATE_LIMIT":
        return await msg.edit_text("⚠️ Steam временно ограничил запросы. Пожалуйста, попробуйте еще раз через 5-10 минут.")
    elif result is None:
        return await msg.edit_text("❌ Не удалось получить данные. Steam API может быть временно недоступен.")
    elif len(result) == 0:
        return await msg.edit_text("⚠️ Инвентарь открыт, но в нем не найдено ликвидных предметов из CS2.")

    async with aiosqlite.connect("inventory.db") as db:
        await db.execute("INSERT OR REPLACE INTO users (chat_id, steam_id) VALUES (?, ?)", (message.chat.id, steam_id))
        await db.commit()
    
    await state.clear()
    await msg.edit_text(f"✅ Успех! Профиль привязан. Найдено предметов: {len(result)}.")

async def main():
    await init_db()
    bot = Bot(token=TOKEN)
    asyncio.create_task(price_checker_loop(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
