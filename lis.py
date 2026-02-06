import asyncio
import logging
import re
import os
import sys
import subprocess
import urllib.parse
from collections import Counter
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Авто-установка зависимостей
def install_missing_packages():
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
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Константы
TOKEN = os.getenv("BOT_TOKEN", "5070946103:AAFG8N40n9IPR3APhYxMeD-mB81-D7ss7Es") # Замените на свой токен
APP_ID = 730  # CS2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}

class Registration(StatesGroup):
    waiting_for_steam_link = State()
    selecting_category = State()
    selecting_weapon_type = State()

# Определение категории
def get_item_category(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ["case", "кейс", "пакет", "набор"]): return "📦 Кейсы"
    if any(x in n for x in ["sticker", "наклейка"]): return "🎯 Наклейки"
    if any(x in n for x in ["agent", "агент"]): return "👤 Агенты"
    if any(x in n for x in ["music kit", "музыка"]): return "🎵 Музыка"
    if any(x in n for x in ["graffiti", "граффити"]): return "🎨 Граффити"
    if "|" in name: return "🔫 Оружие"
    return "🛠 Прочее"

# База данных
async def init_db():
    async with aiosqlite.connect("inventory.db") as db:
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
        await db.commit()

# Парсинг цены
def parse_price(price_str):
    if not price_str: return 0.0
    clean = re.sub(r'[^\d.,]', '', price_str)
    clean = clean.strip('.')
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except ValueError:
        try:
            if clean.count('.') > 1:
                parts = clean.rsplit('.', 1)
                clean = parts[0].replace('.', '') + '.' + parts[1]
                return float(clean)
        except: pass
        return 0.0

# Получение цены из Steam Market
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
                elif r.status == 429:
                    logger.warning(f"Steam Rate Limit (429) for {item_name}")
                    return None, "Rate Limit (подождите)"
                else:
                    return None, f"Ошибка Steam: {r.status}"
        except Exception as e:
            logger.error(f"Price fetch error for {item_name}: {e}")
            return None, "Ошибка сети"

# Steam ID
async def resolve_steam_id(text):
    text = text.strip()
    if text.endswith('/'): text = text[:-1] # Убираем слеш в конце если есть
    
    if re.match(r'^\d{17}$', text): return text
    
    match = re.search(r'steamcommunity\.com/profiles/(\d+)', text)
    if match: return match.group(1)
    
    vanity = re.search(r'steamcommunity\.com/id/([^/]+)', text)
    if vanity:
        vanity_name = vanity.group(1)
        url = f"https://steamcommunity.com/id/{vanity_name}/?xml=1"
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as s:
                async with s.get(url, timeout=10) as r:
                    if r.status != 200: return None
                    content = await r.text()
                    res = re.search(r'<steamID64>(\d+)</steamID64>', content)
                    return res.group(1) if res else None
        except: return None
    return None

# Загрузка инвентаря
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

dp = Dispatcher()
bot_instance = None

# === КЛАВИАТУРЫ ===

def get_main_menu_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📦 Инвентарь"), KeyboardButton(text="📈 Отслеживание")]
    ], resize_keyboard=True)

def get_categories_kb(items):
    items = sorted(list(items))
    btns = []
    for i in range(0, len(items), 2):
        row = [KeyboardButton(text=items[i])]
        if i + 1 < len(items):
            row.append(KeyboardButton(text=items[i+1]))
        btns.append(row)
    btns.append([KeyboardButton(text="🔙 В главное меню")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def get_weapon_types_kb(items):
    items = sorted(list(items))
    btns = []
    for i in range(0, len(items), 2):
        row = [KeyboardButton(text=items[i])]
        if i + 1 < len(items):
            row.append(KeyboardButton(text=items[i+1]))
        btns.append(row)
    btns.append([KeyboardButton(text="🔙 К категориям")]) # Возврат в категории
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def get_items_inline_kb(items_data, page=0, prefix="item"):
    ITEMS_PER_PAGE = 8
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    current_page_items = items_data[start:end]
    
    keyboard = []
    for item_id, name, amount in current_page_items:
        btn_text = f"{name} (x{amount})"
        if len(btn_text) > 40: btn_text = btn_text[:37] + "..."
        if prefix == "tracklist":
            btn_text = f"{name} (~{amount} руб)"
            if len(btn_text) > 40: btn_text = btn_text[:37] + "..."
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"trackview_{item_id}")])
        else:
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_{item_id}")])
    
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}_page_{page-1}"))
    if end < len(items_data):
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}_page_{page+1}"))
    
    if nav_row:
        keyboard.append(nav_row)
        
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# === ОБЩАЯ ФУНКЦИЯ ОБНОВЛЕНИЯ ИНВЕНТАРЯ ===
async def update_inventory_logic(m: Message, state: FSMContext):
    sid = await resolve_steam_id(m.text)
    if not sid: return await m.answer("❌ Неверная ссылка или ID, либо профиль не найден.")
    
    wait = await m.answer("⏳ Сканирую инвентарь...")
    items_counts = await fetch_inventory(sid)
    
    if items_counts is None: return await wait.edit_text("❌ Ошибка доступа. Проверь настройки приватности Steam.")
    if not items_counts: return await wait.edit_text("📦 Инвентарь пуст.")

    # Безопасное обновление БД в транзакции
    try:
        async with aiosqlite.connect("inventory.db") as db:
            await db.execute("BEGIN TRANSACTION")
            await db.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (m.chat.id, sid))
            # Сначала удаляем старое, но в рамках транзакции
            await db.execute("DELETE FROM user_items WHERE chat_id = ?", (m.chat.id,))
            
            for name, count in items_counts.items():
                cat = get_item_category(name)
                await db.execute("INSERT OR IGNORE INTO items (name, category) VALUES (?,?)", (name, cat))
                res = await db.execute("SELECT id FROM items WHERE name = ?", (name,))
                item_id = (await res.fetchone())[0]
                await db.execute("INSERT INTO user_items (chat_id, item_id, amount) VALUES (?,?,?)", (m.chat.id, item_id, count))
            
            await db.commit()
    except Exception as e:
        logger.error(f"DB Error: {e}")
        return await wait.edit_text("❌ Ошибка при сохранении базы данных.")

    await wait.delete()
    await m.answer(f"✅ Успех! Найдено предметов: `{len(items_counts)}`.\nДанные обновлены!", 
                   reply_markup=get_main_menu_kb(), parse_mode="Markdown")
    await state.clear()

# === ОБРАБОТЧИКИ (ПОРЯДОК ВАЖЕН!) ===

# 1. Ссылки на стим - самый высокий приоритет, чтобы обновлять инвентарь в любой момент
@dp.message(F.text.contains("steamcommunity.com"))
async def global_link_update(m: Message, state: FSMContext):
    await update_inventory_logic(m, state)

# 2. Команда Start
@dp.message(Command("start"))
async def start(m: Message, state: FSMContext):
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT steam_id FROM users WHERE chat_id = ?", (m.chat.id,))
        user = await res.fetchone()
        
    if user:
        await m.answer("🏠 Главное меню:", reply_markup=get_main_menu_kb())
        await state.clear()
    else:
        await m.answer("👋 Привет! Пришли ссылку на Steam профиль для настройки.\n\nИнвентарь должен быть открыт!")
        await state.set_state(Registration.waiting_for_steam_link)

# 3. Явное ожидание ссылки (если нажали /start и бот ждет)
@dp.message(Registration.waiting_for_steam_link)
async def process_link(m: Message, state: FSMContext):
    await update_inventory_logic(m, state)

# === МЕНЮ ИНВЕНТАРЯ ===

@dp.message(F.text == "📦 Инвентарь")
async def open_inventory_menu(m: Message, state: FSMContext):
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT DISTINCT i.category FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ?", (m.chat.id,))
        cats = [r[0] for r in await res.fetchall()]
        if not cats: return await m.answer("Инвентарь пуст или не загружен. Отправьте ссылку на профиль Steam еще раз для обновления.")
        await m.answer("Выберите категорию:", reply_markup=get_categories_kb(cats))
        await state.set_state(Registration.selecting_category)

# Явный обработчик для Оружия (чтобы работало даже при сбое стейта)
@dp.message(F.text == "🔫 Оружие")
async def show_weapon_category_shortcut(m: Message, state: FSMContext):
    await state.set_state(Registration.selecting_category) # Восстанавливаем стейт принудительно
    await show_cat(m, state)

@dp.message(Registration.selecting_category)
async def show_cat(m: Message, state: FSMContext):
    if m.text == "🔙 В главное меню":
        await m.answer("🏠 Главное меню", reply_markup=get_main_menu_kb())
        return await state.clear()

    if m.text == "🔫 Оружие":
        async with aiosqlite.connect("inventory.db") as db:
            query = "SELECT i.name FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ? AND i.category = '🔫 Оружие'"
            res = await db.execute(query, (m.chat.id,))
            rows = await res.fetchall()
            if not rows: return await m.answer("Пусто.")
            
            weapon_types = set()
            for row in rows:
                if "|" in row[0]: weapon_types.add(row[0].split("|")[0].strip())
            
            await m.answer("🔫 Выбери тип оружия:", reply_markup=get_weapon_types_kb(weapon_types))
            await state.set_state(Registration.selecting_weapon_type)
            return

    await send_inline_items(m.chat.id, category=m.text, page=0)

@dp.message(Registration.selecting_weapon_type)
async def show_weapon_skins(m: Message, state: FSMContext):
    if m.text == "🔙 К категориям": 
        return await open_inventory_menu(m, state)
    
    await state.update_data(current_weapon_type=m.text)
    await send_inline_items(m.chat.id, weapon_type=m.text, page=0)

async def send_inline_items(chat_id, category=None, weapon_type=None, page=0, message_id=None):
    async with aiosqlite.connect("inventory.db") as db:
        if weapon_type:
            query = """SELECT i.id, i.name, ui.amount FROM items i JOIN user_items ui ON i.id = ui.item_id 
                       WHERE ui.chat_id = ? AND i.category = '🔫 Оружие' AND i.name LIKE ? ORDER BY ui.amount DESC"""
            args = (chat_id, f"{weapon_type} | %")
            title = f"🔫 {weapon_type}"
            cb_prefix = "wskin"
        else:
            query = """SELECT i.id, i.name, ui.amount FROM items i JOIN user_items ui ON i.id = ui.item_id 
                       WHERE ui.chat_id = ? AND i.category = ? ORDER BY ui.amount DESC"""
            args = (chat_id, category)
            title = f"📂 {category}"
            cb_prefix = "catitem"
            
        res = await db.execute(query, args)
        rows = await res.fetchall()

    if not rows:
        if not message_id: await bot_instance.send_message(chat_id, "Ничего не найдено.")
        return

    kb = get_items_inline_kb(rows, page, prefix=cb_prefix)
    text = f"{title}\nСтраница {page+1}"
    
    if message_id:
        try: await bot_instance.edit_message_text(text, chat_id, message_id, reply_markup=kb)
        except: pass
    else:
        await bot_instance.send_message(chat_id, text, reply_markup=kb)

# === ЛОГИКА ОТСЛЕЖИВАНИЯ (TRACKING) ===

@dp.message(F.text == "📈 Отслеживание")
async def tracking_menu_cmd(m: Message):
    async with aiosqlite.connect("inventory.db") as db:
        query = "SELECT id, item_name, last_price FROM tracking WHERE chat_id = ?"
        res = await db.execute(query, (m.chat.id,))
        rows = await res.fetchall()
        
    if not rows:
        return await m.answer("Вы пока ничего не отслеживаете.\nНайдите предмет в Инвентаре и нажмите 'Отслеживать'.")
        
    kb = get_items_inline_kb(rows, page=0, prefix="tracklist")
    await m.answer("📈 Ваши отслеживаемые предметы:", reply_markup=kb)

@dp.callback_query(F.data.startswith("trackview_"))
async def view_tracked_item(call: CallbackQuery):
    track_id = int(call.data.split("_")[1])
    
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT item_name, last_price FROM tracking WHERE id = ?", (track_id,))
        row = await res.fetchone()
        
    if not row:
        return await call.answer("Запись не найдена (возможно, удалена).", show_alert=True)
        
    name, last_price = row
    current_price, price_str = await get_steam_price(name)
    
    text = f"📦 *{name}*\n\n"
    text += f"📌 Базовая цена: `{last_price}` руб.\n"
    
    if current_price:
        diff = current_price - last_price
        icon = "🟢" if diff > 0 else "🔴" if diff < 0 else "⚪️"
        text += f"💰 Текущая цена: `{price_str}` ({icon} {diff:.2f})"
    else:
        text += f"💰 Текущая цена: `{price_str}`"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Перестать отслеживать", callback_data=f"stoptrack_{track_id}")]
    ])
    
    await call.message.answer(text, parse_mode="Markdown", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("stoptrack_"))
async def stop_tracking_handler(call: CallbackQuery):
    track_id = int(call.data.split("_")[1])
    
    async with aiosqlite.connect("inventory.db") as db:
        await db.execute("DELETE FROM tracking WHERE id = ?", (track_id,))
        await db.commit()
        
    await call.message.edit_text(f"❌ *Отслеживание остановлено.*\nПредмет удален из списка.", parse_mode="Markdown")
    await call.answer()

# === CALLBACK ОБЩИЕ ===

@dp.callback_query(F.data.contains("_page_"))
async def paginate_handler(call: CallbackQuery, state: FSMContext):
    prefix, page_str = call.data.rsplit("_page_", 1)
    page = int(page_str)
    
    if prefix == "catitem":
        cat_line = call.message.text.split("\n")[0]
        category = cat_line.replace("📂 ", "")
        await send_inline_items(call.message.chat.id, category=category, page=page, message_id=call.message.message_id)
    elif prefix == "wskin":
        data = await state.get_data()
        w_type = data.get("current_weapon_type")
        if w_type:
            await send_inline_items(call.message.chat.id, weapon_type=w_type, page=page, message_id=call.message.message_id)
        else:
            await call.answer("Сессия истекла.", show_alert=True)
    elif prefix == "tracklist":
        async with aiosqlite.connect("inventory.db") as db:
            query = "SELECT id, item_name, last_price FROM tracking WHERE chat_id = ?"
            res = await db.execute(query, (call.message.chat.id,))
            rows = await res.fetchall()
        kb = get_items_inline_kb(rows, page=page, prefix="tracklist")
        await call.message.edit_reply_markup(reply_markup=kb)
            
    await call.answer()

@dp.callback_query(F.data.startswith("view_"))
async def view_inventory_item(call: CallbackQuery):
    item_id = int(call.data.split("_")[1])
    await call.answer("🔎 Сканирую цену...")
    
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT name FROM items WHERE id = ?", (item_id,))
        row = await res.fetchone()
        if not row: return await call.answer("Предмет не найден.")
        name = row[0]
    
    price_val, price_str = await get_steam_price(name)
    text = f"📦 *Предмет:* `{name}`\n"
    if price_val:
        text += f"💰 *Цена:* `{price_str}`"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📈 Отслеживать", callback_data=f"track_{item_id}")]])
    else:
        text += f"⚠️ *Ошибка:* {price_str}"
        kb = None
        
    await call.message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("track_"))
async def add_tracking(call: CallbackQuery):
    item_id = int(call.data.split("_")[1])
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT name FROM items WHERE id = ?", (item_id,))
        name = (await res.fetchone())[0]
        price_val, _ = await get_steam_price(name)
        
        if not price_val: return await call.answer("Нет цены - нельзя отслеживать.", show_alert=True)
        try:
            await db.execute("INSERT INTO tracking (chat_id, item_name, last_price) VALUES (?,?,?)", (call.message.chat.id, name, price_val))
            await db.commit()
            await call.message.edit_text(f"{call.message.text}\n\n✅ *Отслеживание запущено!*", parse_mode="Markdown")
        except:
            await call.answer("Уже отслеживается!", show_alert=True)

# === ВОССТАНОВЛЕНИЕ СОСТОЯНИЯ (Режим "Спасатель") ===

@dp.message()
async def handle_unknown(m: Message, state: FSMContext):
    # Если это просто текст, проверяем, не навигация ли это
    if m.text == "🔙 В главное меню":
        await m.answer("🏠 Главное меню", reply_markup=get_main_menu_kb())
        await state.clear()
        return

    # Пытаемся понять контекст (восстановление сессии)
    async with aiosqlite.connect("inventory.db") as db:
        # Проверка на категорию (регистронезависимо)
        res = await db.execute("SELECT 1 FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ? AND i.category = ? LIMIT 1", (m.chat.id, m.text))
        if await res.fetchone():
            await state.set_state(Registration.selecting_category)
            await show_cat(m, state)
            return
        
        # Проверка на оружие (грубый поиск по началу строки)
        # m.text может быть "AK-47"
        res = await db.execute("SELECT 1 FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ? AND i.category = '🔫 Оружие' AND i.name LIKE ? LIMIT 1", (m.chat.id, f"{m.text} | %"))
        if await res.fetchone():
            await state.set_state(Registration.selecting_weapon_type)
            await show_weapon_skins(m, state)
            return

    await m.answer("Выберите пункт меню 👇", reply_markup=get_main_menu_kb())

# === ФОНОВАЯ ЗАДАЧА ===

async def monitor_prices():
    while True:
        try:
            async with aiosqlite.connect("inventory.db") as db:
                cursor = await db.execute("SELECT id, chat_id, item_name, last_price FROM tracking")
                tracks = await cursor.fetchall()
                
                for tid, chat_id, name, last_price in tracks:
                    await asyncio.sleep(2)
                    current_price, price_str = await get_steam_price(name)
                    if not current_price: continue
                    
                    if current_price > last_price:
                        msg = f"📈 *Цена выросла!*\nПредмет: `{name}`\nБыло: `{last_price}` -> Стало: `{price_str}`"
                        try:
                            await bot_instance.send_message(chat_id, msg, parse_mode="Markdown")
                            await db.execute("UPDATE tracking SET last_price = ? WHERE id = ?", (current_price, tid))
                            await db.commit()
                        except: pass
        except Exception as e:
            logger.error(f"Monitor error: {e}")
        await asyncio.sleep(3600)

async def main():
    global bot_instance
    await init_db()
    bot = Bot(token=TOKEN)
    bot_instance = bot
    asyncio.create_task(monitor_prices())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
