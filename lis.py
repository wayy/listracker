import asyncio
import logging
import re
import os
import sys
import subprocess
import urllib.parse
import hmac
import hashlib
import json
from collections import Counter
from datetime import datetime
from urllib.parse import parse_qs

# Авто-установка зависимостей
def install_missing_packages():
    # Добавлены зависимости для работы API и безопасности
    packages = ["aiosqlite", "aiogram", "aiohttp", "python-dotenv"]
    for package in packages:
        try:
            module_name = "dotenv" if package == "python-dotenv" else package
            __import__(module_name)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])

install_missing_packages()

import aiohttp
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, WebAppInfo
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Константы
TOKEN = os.getenv("BOT_TOKEN", "5070946103:AAFG8N40n9IPR3APhYxMeD-mB81-D7ss7Es")
APP_ID = 730  # CS2
DB_PATH = os.path.join(os.getcwd(), "inventory.db")
WEB_APP_URL = "https://wayy.github.io/listracker/" # Ссылка на GitHub Pages

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === БЕЗОПАСНОСТЬ (ВАЛИДАЦИЯ MINI APP) ===

def verify_telegram_data(init_data: str) -> bool:
    """Проверяет, что данные присланы именно из Telegram и не подделаны"""
    try:
        if not init_data: return False
        parsed_data = parse_qs(init_data)
        received_hash = parsed_data.pop('hash', [None])[0]
        if not received_hash: return False
        
        data_check_string = "\n".join([f"{k}={v[0]}" for k, v in sorted(parsed_data.items())])
        secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        return hmac.compare_digest(calculated_hash, received_hash)
    except Exception as e:
        logger.error(f"Auth error: {e}")
        return False

# === API ХЕНДЛЕРЫ (ДЛЯ MINI APP) ===

async def get_app_inventory(request):
    """Отдает инвентарь пользователя в формате JSON для сайта"""
    init_data = request.headers.get("Authorization")
    
    if not init_data or not verify_telegram_data(init_data):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        parsed = parse_qs(init_data)
        user_info = json.loads(parsed['user'][0])
        chat_id = user_info.get('id')
    except:
        return web.json_response({"error": "Invalid user info"}, status=400)

    async with aiosqlite.connect(DB_PATH) as db:
        query = """
            SELECT i.name, ui.amount, i.category 
            FROM items i JOIN user_items ui ON i.id = ui.item_id 
            WHERE ui.chat_id = ?
        """
        async with db.execute(query, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
            items = [{"name": r[0], "amount": r[1], "category": r[2]} for r in rows]
            
        return web.json_response(items, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization, Content-Type"
        })

async def api_options_handler(request):
    """Обработка предзапросов браузера (CORS)"""
    return web.Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type"
    })

# === ОСНОВНАЯ ЛОГИКА БОТА ===

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}

class Registration(StatesGroup):
    waiting_for_steam_link = State()
    selecting_category = State()
    selecting_weapon_type = State()

def get_item_category(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ["case", "кейс", "пакет", "набор"]): return "📦 Кейсы"
    if any(x in n for x in ["sticker", "наклейка"]): return "🎯 Наклейки"
    if any(x in n for x in ["agent", "агент"]): return "👤 Агенты"
    if any(x in n for x in ["music kit", "музыка"]): return "🎵 Музыка"
    if any(x in n for x in ["graffiti", "граффити"]): return "🎨 Граффити"
    if "|" in name: return "🔫 Оружие"
    return "🛠 Прочее"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL") 
        await db.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, steam_id TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, category TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS user_items (chat_id INTEGER, item_id INTEGER, amount INTEGER, PRIMARY KEY (chat_id, item_id))")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                item_name TEXT,
                last_price REAL,
                UNIQUE(chat_id, item_name)
            )
        """)
        await db.execute("CREATE TABLE IF NOT EXISTS context_map (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT UNIQUE)")
        await db.commit()

async def get_ctx_id(val: str) -> int:
    if not val: return 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO context_map (val) VALUES (?)", (val,))
        await db.commit()
        res = await db.execute("SELECT id FROM context_map WHERE val = ?", (val,))
        row = await res.fetchone()
        return row[0] if row else 0

async def get_ctx_val(ctx_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        res = await db.execute("SELECT val FROM context_map WHERE id = ?", (ctx_id,))
        row = await res.fetchone()
        return row[0] if row else None

def parse_price(price_str):
    if not price_str: return 0.0
    clean = re.sub(r'[^\d.,]', '', price_str).replace(',', '.')
    try: return float(clean)
    except: return 0.0

async def get_steam_price(item_name):
    encoded_name = urllib.parse.quote(item_name)
    url = f"https://steamcommunity.com/market/priceoverview/?appid={APP_ID}&currency=5&market_hash_name={encoded_name}"
    async with aiohttp.ClientSession(headers=HEADERS) as s:
        try:
            async with s.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    price_str = data.get("lowest_price") or data.get("median_price")
                    if not price_str: return 0.0, "Нет лотов"
                    return parse_price(price_str), price_str
                return None, f"Ошибка Steam: {r.status}"
        except: return None, "Ошибка сети"

async def resolve_steam_id(text):
    text = text.strip()
    if text.endswith('/'): text = text[:-1]
    if re.match(r'^\d{17}$', text): return text
    match = re.search(r'steamcommunity\.com/profiles/(\d+)', text)
    if match: return match.group(1)
    vanity = re.search(r'steamcommunity\.com/id/([^/]+)', text)
    if vanity:
        url = f"https://steamcommunity.com/id/{vanity.group(1)}/?xml=1"
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as s:
                async with s.get(url, timeout=10) as r:
                    content = await r.text()
                    res = re.search(r'<steamID64>(\d+)</steamID64>', content)
                    return res.group(1) if res else None
        except: return None
    return None

async def fetch_inventory(steam_id):
    url = f"https://steamcommunity.com/inventory/{steam_id}/{APP_ID}/2?l=russian&count=2000"
    async with aiohttp.ClientSession(headers=HEADERS) as s:
        try:
            async with s.get(url, timeout=20) as r:
                if r.status != 200: return None
                data = await r.json()
                if not data or "descriptions" not in data: return []
                all_items = [d["market_hash_name"] for d in data["descriptions"] if d.get("marketable")]
                return Counter(all_items)
        except: return None

async def save_inventory_to_db(chat_id, steam_id, items_counts):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN TRANSACTION")
        await db.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (chat_id, steam_id))
        await db.execute("DELETE FROM user_items WHERE chat_id = ?", (chat_id,))
        for name, count in items_counts.items():
            cat = get_item_category(name)
            await db.execute("INSERT OR IGNORE INTO items (name, category) VALUES (?,?)", (name, cat))
            res = await db.execute("SELECT id FROM items WHERE name = ?", (name,))
            item_id = (await res.fetchone())[0]
            await db.execute("INSERT INTO user_items (chat_id, item_id, amount) VALUES (?,?,?)", (chat_id, item_id, count))
        await db.commit()

# === КЛАВИАТУРЫ ===

def get_main_menu_kb():
    # Обновленная кнопка Инвентаря (WebApp)
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📦 Инвентарь", web_app=WebAppInfo(url=WEB_APP_URL))],
        [KeyboardButton(text="📈 Отслеживание")]
    ], resize_keyboard=True)

def get_categories_kb(items):
    items = sorted(list(items))
    btns = []
    for i in range(0, len(items), 2):
        row = [KeyboardButton(text=items[i])]
        if i + 1 < len(items): row.append(KeyboardButton(text=items[i+1]))
        btns.append(row)
    btns.append([KeyboardButton(text="🏠 В главное меню")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def get_weapon_types_kb(items):
    items = sorted(list(items))
    btns = []
    for i in range(0, len(items), 2):
        row = [KeyboardButton(text=items[i])]
        if i + 1 < len(items): row.append(KeyboardButton(text=items[i+1]))
        btns.append(row)
    btns.append([KeyboardButton(text="🔙 К категориям")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

async def get_items_inline_kb(items_data, page=0, mode="cat", value=""):
    ITEMS_PER_PAGE = 10
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    current_page_items = items_data[start:end]
    keyboard = []
    for item_id, name, amount in current_page_items:
        btn_text = f"{name} (x{amount})"
        if len(btn_text) > 40: btn_text = btn_text[:37] + "..."
        if mode == "trc":
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"trv_{item_id}")])
        else:
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_{item_id}")])
    nav_row = []
    prefix = "pc" if mode == "cat" else "pw" if mode == "wep" else "pt"
    ctx_id = await get_ctx_id(value) if value else 0
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"{prefix}_{page-1}_{ctx_id}"))
    if end < len(items_data):
        nav_row.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"{prefix}_{page+1}_{ctx_id}"))
    if nav_row: keyboard.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# === HANDLERS ===

dp = Dispatcher()
bot_instance = None

async def update_inventory_logic(m: Message, state: FSMContext, silent=False):
    sid = await resolve_steam_id(m.text)
    if not sid: 
        if not silent: await m.answer("❌ Неверная ссылка или ID.")
        return False
    if not silent: wait = await m.answer("⏳ Сканирую инвентарь...")
    items_counts = await fetch_inventory(sid)
    if items_counts is None: 
        if not silent: await wait.edit_text("❌ Ошибка доступа.")
        return False
    await save_inventory_to_db(m.chat.id, sid, items_counts or {})
    if not silent:
        await wait.delete()
        await m.answer(f"✅ Успех! Найдено предметов: `{len(items_counts)}`", reply_markup=get_main_menu_kb(), parse_mode="Markdown")
    await state.clear()
    return True

@dp.message(F.text.contains("steamcommunity.com"))
async def global_link_update(m: Message, state: FSMContext):
    await update_inventory_logic(m, state)

@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        res = await db.execute("SELECT steam_id FROM users WHERE chat_id = ?", (m.chat.id,))
        user = await res.fetchone()
    if user:
        await m.answer("🏠 Главное меню:", reply_markup=get_main_menu_kb())
        await state.clear()
    else:
        await m.answer("👋 Привет! Пришли ссылку на Steam профиль.", reply_markup=ReplyKeyboardRemove())
        await state.set_state(Registration.waiting_for_steam_link)

@dp.callback_query(F.data.startswith("view_"))
async def handle_view_item(call: CallbackQuery):
    item_id = int(call.data.split("_")[1])
    await call.answer("🔎 Ищу цену...")
    async with aiosqlite.connect(DB_PATH) as db:
        res = await db.execute("SELECT name FROM items WHERE id = ?", (item_id,))
        name = (await res.fetchone())[0]
    price_val, price_str = await get_steam_price(name)
    text = f"📦 *Предмет:* `{name}`\n" + (f"💰 *Цена:* `{price_str}`" if price_val else f"⚠️ *Ошибка:* {price_str}")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📈 Отслеживать", callback_data=f"track_{item_id}")]]) if price_val else None
    await call.message.answer(text, parse_mode="Markdown", reply_markup=kb)

# ... (Остальные хендлеры: Registration.waiting_for_steam_link, cmd_tracking, handle_pagination и т.д. остаются без изменений) ...

@dp.message(F.text == "📈 Отслеживание")
async def cmd_tracking(m: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        res = await db.execute("SELECT id, item_name, last_price FROM tracking WHERE chat_id = ? ORDER BY item_name ASC", (m.chat.id,))
        rows = await res.fetchall()
    if not rows: return await m.answer("Список пуст.")
    kb = await get_items_inline_kb(rows, mode="trc", value="tracking_list")
    await m.answer("*📈 Отслеживание:*", reply_markup=kb, parse_mode="Markdown")

async def monitor_prices_task():
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT id, chat_id, item_name, last_price FROM tracking")
                tracks = await cursor.fetchall()
                for tid, chat_id, name, last_price in tracks:
                    await asyncio.sleep(2)
                    current_price, price_str = await get_steam_price(name)
                    if current_price and current_price > last_price:
                        await bot_instance.send_message(chat_id, f"📈 *Цена выросла!*\n`{name}`: `{price_str}`", parse_mode="Markdown")
                        await db.execute("UPDATE tracking SET last_price = ? WHERE id = ?", (current_price, tid))
                        await db.commit()
        except: pass
        await asyncio.sleep(3600)

@dp.message()
async def handle_fallback(m: Message, state: FSMContext):
    if m.text == "🏠 В главное меню":
        await m.answer("🏠 Главное меню", reply_markup=get_main_menu_kb())
        return await state.clear()
    await m.answer("Выберите пункт меню 👇", reply_markup=get_main_menu_kb())

# === ЗАПУСК ===

async def main():
    global bot_instance
    await init_db()
    
    bot = Bot(token=TOKEN)
    bot_instance = bot

    # Настройка API
    app = web.Application()
    app.router.add_get('/api/inventory', get_app_inventory)
    app.router.add_options('/api/inventory', api_options_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8080).start()
    logger.info("API сервер запущен на порту 8080")

    asyncio.create_task(monitor_prices_task())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
