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
        # Новая таблица для отслеживания
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

# Парсинг цены из строки Steam (например "1 234,50 pуб.")
def parse_price(price_str):
    if not price_str: return 0.0
    # Оставляем только цифры и запятую/точку
    clean = re.sub(r'[^\d.,]', '', price_str)
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except ValueError:
        return 0.0

# Получение цены из Steam Market
async def get_steam_price(item_name):
    encoded_name = urllib.parse.quote(item_name)
    # currency=5 - это Рубли (RUB). 1 - USD.
    url = f"https://steamcommunity.com/market/priceoverview/?appid={APP_ID}&currency=5&market_hash_name={encoded_name}"
    async with aiohttp.ClientSession(headers=HEADERS) as s:
        try:
            async with s.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    # Берем lowest_price (минимальная цена продажи сейчас)
                    price_str = data.get("lowest_price") or data.get("median_price")
                    return parse_price(price_str), price_str # Возвращаем число и исходную строку
        except Exception as e:
            logger.error(f"Price fetch error for {item_name}: {e}")
    return None, None

# Получение Steam ID
async def resolve_steam_id(text):
    text = text.strip()
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
bot_instance = None # Глобальная ссылка на бота для фоновой задачи

# Клавиатура категорий
def get_kb(items, add_back=False):
    items = sorted(list(items))
    btns = []
    for i in range(0, len(items), 2):
        row = [KeyboardButton(text=items[i])]
        if i + 1 < len(items):
            row.append(KeyboardButton(text=items[i+1]))
        btns.append(row)
    if add_back:
        btns.append([KeyboardButton(text="🔙 Назад")])
    else:
        btns.append([KeyboardButton(text="❌ Закрыть")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

# Генератор инлайн-списка предметов с пагинацией
def get_items_inline_kb(items_data, page=0, prefix="item"):
    # items_data: list of (id, name, amount)
    ITEMS_PER_PAGE = 8
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    current_page_items = items_data[start:end]
    
    keyboard = []
    for item_id, name, amount in current_page_items:
        btn_text = f"{name} (x{amount})"
        # Обрезаем имя, если слишком длинное для кнопки
        if len(btn_text) > 40: btn_text = btn_text[:37] + "..."
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_{item_id}")])
    
    # Кнопки навигации
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}_page_{page-1}"))
    if end < len(items_data):
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}_page_{page+1}"))
    
    if nav_row:
        keyboard.append(nav_row)
        
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@dp.message(Command("start"))
async def start(m: Message, state: FSMContext):
    await m.answer("👋 Привет! Пришли ссылку на Steam профиль.\n\nИнвентарь должен быть открыт!")
    await state.set_state(Registration.waiting_for_steam_link)

@dp.message(Registration.waiting_for_steam_link)
async def process_link(m: Message, state: FSMContext):
    sid = await resolve_steam_id(m.text)
    if not sid: return await m.answer("❌ Неверная ссылка или ID, либо профиль не найден.")
    
    wait = await m.answer("⏳ Сканирую инвентарь...")
    items_counts = await fetch_inventory(sid)
    
    if items_counts is None: return await wait.edit_text("❌ Ошибка доступа. Проверь настройки приватности Steam.")
    if not items_counts: return await wait.edit_text("📦 Инвентарь пуст.")

    async with aiosqlite.connect("inventory.db") as db:
        await db.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (m.chat.id, sid))
        await db.execute("DELETE FROM user_items WHERE chat_id = ?", (m.chat.id,))
        cats = set()
        for name, count in items_counts.items():
            cat = get_item_category(name)
            cats.add(cat)
            await db.execute("INSERT OR IGNORE INTO items (name, category) VALUES (?,?)", (name, cat))
            res = await db.execute("SELECT id FROM items WHERE name = ?", (name,))
            item_id = (await res.fetchone())[0]
            await db.execute("INSERT INTO user_items (chat_id, item_id, amount) VALUES (?,?,?)", (m.chat.id, item_id, count))
        await db.commit()

    await wait.delete()
    await m.answer(f"✅ Успех! Найдено предметов: `{len(items_counts)}`.\nВыбери категорию:", reply_markup=get_kb(cats), parse_mode="Markdown")
    await state.set_state(Registration.selecting_category)

@dp.message(Command("items"))
async def items_cmd(m: Message, state: FSMContext):
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT DISTINCT i.category FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ?", (m.chat.id,))
        cats = [r[0] for r in await res.fetchall()]
        if not cats: return await m.answer("Сначала привяжи профиль через /start")
        await m.answer("Выбери категорию:", reply_markup=get_kb(cats))
        await state.set_state(Registration.selecting_category)

# === ОБРАБОТКА КАТЕГОРИЙ ===

@dp.message(Registration.selecting_category)
async def show_cat(m: Message, state: FSMContext):
    if m.text == "❌ Закрыть":
        await m.answer("Меню закрыто. /items для вызова.", reply_markup=ReplyKeyboardRemove())
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
            
            await m.answer("🔫 Выбери тип оружия:", reply_markup=get_kb(weapon_types, add_back=True))
            await state.set_state(Registration.selecting_weapon_type)
            return

    # Показываем инлайн список для других категорий
    await send_inline_items(m.chat.id, category=m.text, page=0)

@dp.message(Registration.selecting_weapon_type)
async def show_weapon_skins(m: Message, state: FSMContext):
    if m.text == "🔙 Назад": return await items_cmd(m, state)
    
    # Сохраняем выбранный тип оружия в state data, чтобы использовать при пагинации
    await state.update_data(current_weapon_type=m.text)
    await send_inline_items(m.chat.id, weapon_type=m.text, page=0)

# Функция отправки/редактирования списка
async def send_inline_items(chat_id, category=None, weapon_type=None, page=0, message_id=None):
    async with aiosqlite.connect("inventory.db") as db:
        if weapon_type:
            query = """SELECT i.id, i.name, ui.amount FROM items i JOIN user_items ui ON i.id = ui.item_id 
                       WHERE ui.chat_id = ? AND i.category = '🔫 Оружие' AND i.name LIKE ? ORDER BY ui.amount DESC"""
            args = (chat_id, f"{weapon_type} | %")
            title = f"🔫 {weapon_type}"
            cb_prefix = "wskin" # weapon skin
        else:
            query = """SELECT i.id, i.name, ui.amount FROM items i JOIN user_items ui ON i.id = ui.item_id 
                       WHERE ui.chat_id = ? AND i.category = ? ORDER BY ui.amount DESC"""
            args = (chat_id, category)
            title = f"📂 {category}"
            cb_prefix = "catitem" # category item
            
        res = await db.execute(query, args)
        rows = await res.fetchall() # [(id, name, amount), ...]

    if not rows:
        if message_id: return # Нечего обновлять
        await bot_instance.send_message(chat_id, "Ничего не найдено.")
        return

    kb = get_items_inline_kb(rows, page, prefix=cb_prefix)
    
    # Хак: сохраняем контекст в "memory" через замыкание или просто передаем параметры в callback
    # Но для простоты, если мы меняем страницу, нам нужно знать категорию.
    # Поэтому мы временно храним это в тексте сообщения или используем state, но в callback у нас нет state proxy так легко.
    # Упрощение: используем глобальный FSM или просто пересобираем данные.
    
    text = f"{title}\nСтраница {page+1}"
    
    if message_id:
        try:
            await bot_instance.edit_message_text(text, chat_id, message_id, reply_markup=kb)
        except: pass
    else:
        await bot_instance.send_message(chat_id, text, reply_markup=kb)

# === CALLBACK HANDLERS ===

# Пагинация
@dp.callback_query(F.data.contains("_page_"))
async def paginate_items(call: CallbackQuery, state: FSMContext):
    prefix, page_str = call.data.rsplit("_page_", 1)
    page = int(page_str)
    
    # Пытаемся понять контекст
    if prefix == "catitem":
        # Получаем категорию из текста сообщения
        cat_line = call.message.text.split("\n")[0]
        category = cat_line.replace("📂 ", "")
        await send_inline_items(call.message.chat.id, category=category, page=page, message_id=call.message.message_id)
    elif prefix == "wskin":
        data = await state.get_data()
        w_type = data.get("current_weapon_type")
        if w_type:
            await send_inline_items(call.message.chat.id, weapon_type=w_type, page=page, message_id=call.message.message_id)
        else:
            await call.answer("Сессия истекла, выберите оружие заново.")
    
    await call.answer()

# Просмотр предмета
@dp.callback_query(F.data.startswith("view_"))
async def view_item_details(call: CallbackQuery):
    item_id = int(call.data.split("_")[1])
    
    await call.answer("🔎 Сканирую цену...")
    
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT name FROM items WHERE id = ?", (item_id,))
        row = await res.fetchone()
        if not row: return await call.answer("Предмет не найден в БД.")
        name = row[0]
    
    price_val, price_str = await get_steam_price(name)
    
    text = f"📦 *Предмет:* `{name}`\n"
    if price_val:
        text += f"💰 *Цена:* `{price_str}`"
    else:
        text += "💰 *Цена:* Не удалось получить или предмет не продается."
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Отслеживать", callback_data=f"track_{item_id}")]
    ])
    
    await call.message.answer(text, parse_mode="Markdown", reply_markup=kb)

# Начало отслеживания
@dp.callback_query(F.data.startswith("track_"))
async def start_tracking(call: CallbackQuery):
    item_id = int(call.data.split("_")[1])
    
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT name FROM items WHERE id = ?", (item_id,))
        name = (await res.fetchone())[0]
        
        # Получаем текущую цену как базовую
        price_val, _ = await get_steam_price(name)
        if not price_val:
            return await call.answer("Не могу отслеживать: нет цены.", show_alert=True)
            
        try:
            await db.execute("INSERT INTO tracking (chat_id, item_name, last_price) VALUES (?,?,?)",
                             (call.message.chat.id, name, price_val))
            await db.commit()
            await call.message.edit_text(f"{call.message.text}\n\n✅ *Отслеживание запущено!*\nЯ напишу, если цена вырастет.", parse_mode="Markdown")
        except:
            await call.answer("Вы уже отслеживаете этот предмет!", show_alert=True)

# === ФОНОВАЯ ЗАДАЧА ===

async def monitor_prices():
    while True:
        try:
            logger.info("Starting price check cycle...")
            async with aiosqlite.connect("inventory.db") as db:
                # Получаем все уникальные предметы для отслеживания, чтобы не ддосить стим одинаковыми запросами
                # Но для простоты пройдемся по каждому user-item, т.к. у каждого своя "последняя цена" для уведомления? 
                # Нет, лучше группировать. Но пока сделаем простой цикл по записям.
                
                cursor = await db.execute("SELECT id, chat_id, item_name, last_price FROM tracking")
                tracks = await cursor.fetchall()
                
                for tid, chat_id, name, last_price in tracks:
                    await asyncio.sleep(2) # Задержка 2 сек между запросами чтобы не получить бан
                    
                    current_price, price_str = await get_steam_price(name)
                    if not current_price: continue
                    
                    # Логика: если цена ВЫРОСЛА
                    if current_price > last_price:
                        # Уведомляем
                        msg = (f"📈 *Цена выросла!*\n"
                               f"Предмет: `{name}`\n"
                               f"Было: `{last_price} руб.` -> Стало: `{price_str}`")
                        try:
                            await bot_instance.send_message(chat_id, msg, parse_mode="Markdown")
                            # Обновляем последнюю цену, чтобы не спамить
                            await db.execute("UPDATE tracking SET last_price = ? WHERE id = ?", (current_price, tid))
                            await db.commit()
                        except Exception as e:
                            logger.error(f"Failed to send alert to {chat_id}: {e}")
                            
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
            
        await asyncio.sleep(3600) # Ждем 1 час

async def main():
    global bot_instance
    await init_db()
    bot = Bot(token=TOKEN)
    bot_instance = bot
    
    # Запуск фоновой задачи
    asyncio.create_task(monitor_prices())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
