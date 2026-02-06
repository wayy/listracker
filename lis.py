import asyncio
import logging
import re
import urllib.parse
import os
import sys
from datetime import datetime

# Пытаемся импортировать зависимости, необходимые для работы
try:
    import aiohttp
    import aiosqlite
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import Command
    from aiogram.types import Message
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
except ImportError as e:
    logging.error(f"Критическая ошибка: отсутствует модуль {e.name}. Убедитесь, что requirements.txt заполнен верно.")
    # В некоторых средах деплоя нужно принудительно завершить процесс, чтобы контейнер перезапустился
    sys.exit(1)

# Попытка импорта dotenv для поддержки вашего окружения
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

INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/{app_id}/2?l=english&count=5000"
PRICE_URL = "https://steamcommunity.com/market/priceoverview/?appid={app_id}&currency={currency}&market_hash_name={name}"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

def extract_steam_id(text):
    match = re.search(r'7656119\d{10}', text)
    return match.group(0) if match else None

async def fetch_inventory(steam_id, app_id):
    url = INVENTORY_URL.format(steam_id=steam_id, app_id=app_id)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                if not data or not data.get('descriptions'): return []
                return list(set(item['market_hash_name'] for item in data['descriptions'] if item.get('marketable')))
        except Exception as e:
            logger.error(f"Ошибка парсинга инвентаря: {e}")
            return None

async def get_item_price(name, app_id):
    encoded_name = urllib.parse.quote(name)
    url = PRICE_URL.format(app_id=app_id, currency=CURRENCY, name=encoded_name)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 429:
                await asyncio.sleep(20)
                return None
            if resp.status != 200: return None
            data = await resp.json()
            if data.get('success') and data.get('lowest_price'):
                p_str = data['lowest_price'].replace(',', '.').replace('\xa0', '').replace(' ', '')
                p_val = "".join(filter(lambda x: x.isdigit() or x == '.', p_str))
                return float(p_val) if p_val else None
            return None

async def price_checker_loop(bot: Bot):
    while True:
        logger.info("Запуск фоновой проверки цен...")
        try:
            async with aiosqlite.connect("inventory.db") as db:
                async with db.execute("SELECT chat_id, steam_id FROM users") as u_cursor:
                    users = await u_cursor.fetchall()
                
                for chat_id, steam_id in users:
                    items = await fetch_inventory(steam_id, APP_ID)
                    if items is None: continue

                    for name in items:
                        await db.execute("INSERT OR IGNORE INTO items (market_hash_name, appid) VALUES (?, ?)", (name, APP_ID))
                        await db.commit()
                        res = await db.execute("SELECT id FROM items WHERE market_hash_name = ?", (name,))
                        item_id = (await res.fetchone())[0]
                        await db.execute("INSERT OR IGNORE INTO user_items (chat_id, item_id) VALUES (?, ?)", (chat_id, item_id))
                        
                        current_price = await get_item_price(name, APP_ID)
                        if not current_price: continue

                        res = await db.execute("SELECT lowest_price FROM prices WHERE item_id = ? ORDER BY timestamp DESC LIMIT 1", (item_id,))
                        last_price_row = await res.fetchone()
                        last_price = last_price_row[0] if last_price_row else current_price

                        res = await db.execute("SELECT last_notified_price FROM alerts_state WHERE chat_id = ? AND item_id = ?", (chat_id, item_id))
                        last_notified_row = await res.fetchone()
                        last_notified = last_notified_row[0] if last_notified_row else 0

                        if current_price > last_price and current_price > last_notified:
                            diff = current_price - last_price
                            try:
                                await bot.send_message(
                                    chat_id, 
                                    f"🚀 *Цена выросла!*\n\n📦 *Предмет:* `{name}`\n💰 *Было:* {last_price:.2f} ₽\n📈 *Стало:* {current_price:.2f} ₽\n➕ *Разница:* +{diff:.2f} ₽",
                                    parse_mode="Markdown"
                                )
                                await db.execute("INSERT OR REPLACE INTO alerts_state (chat_id, item_id, last_notified_price) VALUES (?, ?, ?)", (chat_id, item_id, current_price))
                            except Exception as e:
                                logger.error(f"Ошибка отправки: {e}")

                        await db.execute("INSERT INTO prices (item_id, lowest_price, timestamp) VALUES (?, ?, ?)", (item_id, current_price, datetime.now()))
                        await db.commit()
                        await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"Ошибка в цикле проверки: {e}")

        await asyncio.sleep(CHECK_INTERVAL)

dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT steam_id FROM users WHERE chat_id = ?", (message.chat.id,))
        user = await res.fetchone()
    if user:
        await message.answer(f"Ваш профиль `{user[0]}` уже отслеживается.")
    else:
        await message.answer("Привет! Отправьте ссылку на ваш Steam профиль.")
        await state.set_state(Registration.waiting_for_steam_link)

@dp.message(Registration.waiting_for_steam_link)
async def process_link(message: Message, state: FSMContext):
    steam_id = extract_steam_id(message.text)
    if not steam_id: return await message.answer("Ошибка! Не удалось найти цифровой Steam ID.")
    msg = await message.answer("⏳ Проверяю инвентарь...")
    items = await fetch_inventory(steam_id, APP_ID)
    if items is None: return await msg.edit_text("❌ Ошибка доступа.")
    async with aiosqlite.connect("inventory.db") as db:
        await db.execute("INSERT OR REPLACE INTO users (chat_id, steam_id) VALUES (?, ?)", (message.chat.id, steam_id))
        await db.commit()
    await state.clear()
    await msg.edit_text(f"✅ Успешно! Найдено предметов: {len(items)}.")

@dp.message(Command("status"))
async def cmd_status(message: Message):
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT COUNT(*) FROM user_items WHERE chat_id = ?", (message.chat.id,))
        count = (await res.fetchone())[0]
        await message.answer(f"Отслеживается предметов: {count}")

async def main():
    await init_db()
    bot = Bot(token=TOKEN)
    asyncio.create_task(price_checker_loop(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
