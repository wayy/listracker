import asyncio
import logging
import re
import os
import sys
import subprocess
from collections import Counter

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
TOKEN = os.getenv("BOT_TOKEN", "5070946103:AAFG8N40n9IPR3APhYxMeD-mB81-D7ss7Es") # Не забудь заменить или использовать .env
APP_ID = 730  # CS2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}

class Registration(StatesGroup):
    waiting_for_steam_link = State()
    selecting_category = State()
    selecting_weapon_type = State() # Новое состояние для выбора типа оружия

# Определение категории
def get_item_category(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ["case", "кейс", "пакет", "набор"]): return "📦 Кейсы"
    if any(x in n for x in ["sticker", "наклейка"]): return "🎯 Наклейки"
    if any(x in n for x in ["agent", "агент"]): return "👤 Агенты"
    if any(x in n for x in ["music kit", "музыка"]): return "🎵 Музыка"
    if any(x in n for x in ["graffiti", "граффити"]): return "🎨 Граффити"
    if "|" in name: return "🔫 Оружие" # Все что имеет | и не попало выше - обычно оружие или перчатки
    return "🛠 Прочее"

# База данных
async def init_db():
    async with aiosqlite.connect("inventory.db") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, steam_id TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, category TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS user_items (chat_id INTEGER, item_id INTEGER, amount INTEGER, PRIMARY KEY (chat_id, item_id))")
        await db.commit()

# Получение Steam ID
async def resolve_steam_id(text):
    text = text.strip()
    
    # 1. Если скинули чистый SteamID64 (17 цифр)
    if re.match(r'^\d{17}$', text): 
        return text
    
    # 2. Если ссылка вида profiles/123456...
    # Регулярка ищет 'steamcommunity.com/profiles/' и берет цифры после этого
    match = re.search(r'steamcommunity\.com/profiles/(\d+)', text)
    if match: 
        return match.group(1)
    
    # 3. Если ссылка вида id/custom_name (Vanity URL)
    # Регулярка ищет 'steamcommunity.com/id/' и берет всё до следующего слеша
    vanity = re.search(r'steamcommunity\.com/id/([^/]+)', text)
    if vanity:
        vanity_name = vanity.group(1)
        # Делаем запрос к XML API стима для получения ID64
        url = f"https://steamcommunity.com/id/{vanity_name}/?xml=1"
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as s:
                async with s.get(url, timeout=10) as r:
                    if r.status != 200:
                        return None
                    content = await r.text()
                    # Ищем тег <steamID64>
                    res = re.search(r'<steamID64>(\d+)</steamID64>', content)
                    return res.group(1) if res else None
        except Exception as e:
            logger.error(f"Ошибка при резолве Steam ID: {e}")
            return None
            
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
                return Counter(all_items)
        except: return None

dp = Dispatcher()

# Генератор клавиатуры
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
            
            await db.execute("INSERT INTO user_items (chat_id, item_id, amount) VALUES (?,?,?)", 
                             (m.chat.id, item_id, count))
        await db.commit()

    await wait.delete()
    await m.answer(f"✅ Успех! Найдено уникальных предметов: `{len(items_counts)}`.\nВыбери категорию:", 
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

# === ОБНОВЛЕННАЯ ЛОГИКА ОРУЖИЯ ===

@dp.message(Registration.selecting_category)
async def show_cat(m: Message, state: FSMContext):
    if m.text == "❌ Закрыть":
        await m.answer("Меню закрыто. /items для вызова.", reply_markup=ReplyKeyboardRemove())
        return await state.clear()

    # Если выбрали Оружие - включаем под-меню
    if m.text == "🔫 Оружие":
        async with aiosqlite.connect("inventory.db") as db:
            # Достаем все названия оружия пользователя
            query = """
                SELECT i.name 
                FROM items i 
                JOIN user_items ui ON i.id = ui.item_id 
                WHERE ui.chat_id = ? AND i.category = '🔫 Оружие'
            """
            res = await db.execute(query, (m.chat.id,))
            rows = await res.fetchall()
            
            if not rows:
                return await m.answer("В этой категории пусто.")

            # Парсим типы оружия (AK-47, AWP и т.д.) из названий (обычно "Тип | Скин")
            weapon_types = set()
            for row in rows:
                name = row[0]
                if "|" in name:
                    w_type = name.split("|")[0].strip() # Берем часть до черты
                    weapon_types.add(w_type)
            
            await m.answer("🔫 Выбери тип оружия:", reply_markup=get_kb(weapon_types, add_back=True))
            await state.set_state(Registration.selecting_weapon_type) # Меняем состояние
            return

    # Обычная логика для остальных категорий (Кейсы, Наклейки и т.д.)
    await show_items_list(m, state, category=m.text)

# Обработчик выбора конкретного ТИПА оружия (например, нажали AK-47)
@dp.message(Registration.selecting_weapon_type)
async def show_weapon_skins(m: Message, state: FSMContext):
    # Логика кнопки Назад
    if m.text == "🔙 Назад":
        return await items_cmd(m, state) # Возвращаем в меню категорий
        
    async with aiosqlite.connect("inventory.db") as db:
        # Ищем предметы, название которых начинается с выбранного типа (например "AK-47 |")
        # Используем LIKE 'Type | %'
        search_pattern = f"{m.text} | %"
        
        query = """
            SELECT i.name, ui.amount 
            FROM items i 
            JOIN user_items ui ON i.id = ui.item_id 
            WHERE ui.chat_id = ? AND i.category = '🔫 Оружие' AND i.name LIKE ?
            ORDER BY ui.amount DESC
        """
        res = await db.execute(query, (m.chat.id, search_pattern))
        rows = await res.fetchall()
        
        if not rows:
            await m.answer("Не найдено скинов для этого оружия. Возможно, ошибка в названии.", reply_markup=get_kb([], add_back=True))
            return

        # Формируем список
        text = f"🔫 *Оружие:* {m.text}\n\n"
        items_list = []
        for name, amount in rows:
            count_str = f" x{amount}" if amount > 1 else ""
            # Убираем название оружия из строки, чтобы не дублировать (оставляем только скин)
            skin_name = name.replace(f"{m.text} | ", "")
            items_list.append(f"• `{skin_name}`{count_str}")
        
        text += "\n".join(items_list)
        await m.answer(text, parse_mode="Markdown", reply_markup=get_kb([], add_back=True)) # Оставляем кнопку назад

# Вспомогательная функция для вывода списка (для кейсов, наклеек и прочего)
async def show_items_list(m: Message, state: FSMContext, category: str):
    async with aiosqlite.connect("inventory.db") as db:
        query = """
            SELECT i.name, ui.amount 
            FROM items i 
            JOIN user_items ui ON i.id = ui.item_id 
            WHERE ui.chat_id = ? AND i.category = ?
            ORDER BY ui.amount DESC
        """
        res = await db.execute(query, (m.chat.id, category))
        rows = await res.fetchall()
        
        if not rows: 
            return await m.answer("В этой категории ничего не найдено.")
        
        total_items = sum(r[1] for r in rows)
        text = f"📂 *Категория:* {category}\n"
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
