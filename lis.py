import asyncio
import logging
import re
import os
import sys
import subprocess

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
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Константы
TOKEN = os.getenv("BOT_TOKEN", "5070946103:AAFG8N40n9IPR3APhYxMeD-mB81-D7ss7Es")
APP_ID = 730  # CS2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}

class Registration(StatesGroup):
    waiting_for_steam_link = State()
    selecting_category = State()

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
        # Таблица предметов теперь хранит уникальные записи
        await db.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, category TEXT)")
        # Таблица связей теперь позволяет хранить количество (amount)
        await db.execute("CREATE TABLE IF NOT EXISTS user_items (chat_id INTEGER, item_id INTEGER, amount INTEGER, PRIMARY KEY (chat_id, item_id))")
        await db.commit()

# Получение Steam ID
async def resolve_steam_id(text):
    if re.match(r'^\d{17}$', text): return text
    match = re.search(r'profiles/(\d+)', text)
    if match: return match.group(1)
    vanity = re.search(r'id/([^/]+)', text)
    if vanity:
        url = f"https://steamcommunity.com/id/{vanity.group(1)}/?xml=1"
        async with aiohttp.ClientSession(headers=HEADERS) as s:
            async with s.get(url) as r:
                content = await r.text()
                res = re.search(r'<steamID64>(\d+)</steamID64>', content)
                return res.group(1) if res else None
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
                
                # Считаем количество каждого предмета
                all_items = [d["market_hash_name"] for d in data["descriptions"] if d.get("marketable")]
                from collections import Counter
                return Counter(all_items)
        except: return None

dp = Dispatcher()

def get_kb(categories):
    categories = sorted(list(categories))
    btns = []
    for i in range(0, len(categories), 2):
        row = [KeyboardButton(text=categories[i])]
        if i + 1 < len(categories):
            row.append(KeyboardButton(text=categories[i+1]))
        btns.append(row)
    btns.append([KeyboardButton(text="❌ Закрыть")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

@dp.message(Command("start"))
async def start(m: Message, state: FSMContext):
    await m.answer("👋 Привет! Пришли ссылку на Steam профиль.\n\nИнвентарь должен быть открыт!")
    await state.set_state(Registration.waiting_for_steam_link)

@dp.message(Registration.waiting_for_steam_link)
async def process_link(m: Message, state: FSMContext):
    sid = await resolve_steam_id(m.text)
    if not sid: return await m.answer("❌ Неверная ссылка или ID.")
    
    wait = await m.answer("⏳ Сканирую инвентарь...")
    items_counts = await fetch_inventory(sid) # Теперь это словарь {название: количество}
    
    if items_counts is None: return await wait.edit_text("❌ Ошибка доступа. Проверь настройки приватности Steam.")
    if not items_counts: return await wait.edit_text("📦 Инвентарь пуст.")

    async with aiosqlite.connect("inventory.db") as db:
        await db.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (m.chat.id, sid))
        await db.execute("DELETE FROM user_items WHERE chat_id = ?", (m.chat.id,))
        
        cats = set()
        for name, count in items_counts.items():
            cat = get_item_category(name)
            cats.add(cat)
            # Добавляем в общий список предметов
            await db.execute("INSERT OR IGNORE INTO items (name, category) VALUES (?,?)", (name, cat))
            
            # Получаем ID предмета
            res = await db.execute("SELECT id FROM items WHERE name = ?", (name,))
            item_id = (await res.fetchone())[0]
            
            # Привязываем к пользователю с указанием количества
            await db.execute("INSERT INTO user_items (chat_id, item_id, amount) VALUES (?,?,?)", 
                             (m.chat.id, item_id, count))
        await db.commit()

    await wait.delete()
    await m.answer(f"✅ Успех! Найдено уникальных предметов: `{len(items_counts)}`.\nВыбери категорию для просмотра:", 
                   reply_markup=get_kb(cats), 
                   parse_mode="Markdown")
    await state.set_state(Registration.selecting_category)

@dp.message(Command("items"))
async def items_cmd(m: Message, state: FSMContext):
    async with aiosqlite.connect("inventory.db") as db:
        res = await db.execute("SELECT DISTINCT i.category FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ?", (m.chat.id,))
        cats = [r[0] for r in await res.fetchall()]
        if not cats: return await m.answer("Сначала привяжи профиль через /start")
        await m.answer("Выбери категорию:", reply_markup=get_kb(cats))
        await state.set_state(Registration.selecting_category)

@dp.message(Registration.selecting_category)
async def show_cat(m: Message, state: FSMContext):
    if m.text == "❌ Закрыть":
        await m.answer("Меню закрыто. Используй /items для вызова.", reply_markup=ReplyKeyboardRemove())
        return await state.clear()

    async with aiosqlite.connect("inventory.db") as db:
        # Запрос теперь достает имя и количество
        query = """
            SELECT i.name, ui.amount 
            FROM items i 
            JOIN user_items ui ON i.id = ui.item_id 
            WHERE ui.chat_id = ? AND i.category = ?
            ORDER BY ui.amount DESC
        """
        res = await db.execute(query, (m.chat.id, m.text))
        rows = await res.fetchall()
        
        if not rows: 
            return await m.answer("В этой категории ничего не найдено. Попробуй обновить инвентарь через /start")
        
        total_items = sum(r[1] for r in rows)
        text = f"📂 *Категория:* {m.text}\n"
        text += f"📦 *Всего предметов:* `{total_items}`\n\n"
        
        items_list = []
        for name, amount in rows[:60]:
            count_str = f" x{amount}" if amount > 1 else ""
            items_list.append(f"• `{name}`{count_str}")
        
        text += "\n".join(items_list)
        
        if len(rows) > 60: 
            text += f"\n\n...и еще {len(rows) - 60} типов предметов."
        
        await m.answer(text, parse_mode="Markdown")

async def main():
    await init_db()
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
