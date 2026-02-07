import asyncio
import logging
import re
import os
import sys
import subprocess
import urllib.parse
from collections import Counter
from datetime import datetime

from aiohttp import web
from supabase import create_client, Client

# === КОНФИГУРАЦИЯ SUPABASE ===
SUPABASE_URL = "https://trufweyemrkdogsszike.supabase.co"
SUPABASE_KEY = "sb_publishable_Ywujm6u8WqyG9Y4K5ANOsA_E8umiZqc"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === API ДЛЯ MINI APP ===
async def get_app_inventory(request):
    chat_id = request.query.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "no_id"}, status=400)

    try:
        # Получаем инвентарь с джоином таблицы предметов
        res = supabase.table("user_items").select(
            "amount, items(name, category)"
        ).eq("chat_id", chat_id).execute()
        
        items = []
        for r in res.data:
            items.append({
                "name": r['items']['name'],
                "amount": r['amount'],
                "category": r['items']['category']
            })
        
        return web.json_response(items, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"API Error: {e}")
        return web.json_response({"error": "db_error"}, status=500)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Авто-установка зависимостей (добавлен supabase)
def install_missing_packages():
    packages = ["supabase", "aiogram", "aiohttp", "python-dotenv"]
    for package in packages:
        try:
            module_name = "supabase" if package == "supabase" else package
            __import__(module_name)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])

install_missing_packages()

import aiohttp
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

# В Supabase context_map реализуем через таблицу или просто храним значения, 
# так как ограничения на callback_data все еще есть.
async def get_ctx_id(val: str) -> int:
    if not val: return 0
    res = supabase.table("context_map").upsert({"val": val}, on_conflict="val").execute()
    return res.data[0]['id']

async def get_ctx_val(ctx_id: int) -> str:
    res = supabase.table("context_map").select("val").eq("id", ctx_id).execute()
    return res.data[0]['val'] if res.data else None

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
                    return None, "Rate Limit (подождите)"
                else:
                    return None, f"Ошибка Steam: {r.status}"
        except Exception as e:
            logger.error(f"Price error: {e}")
            return None, "Ошибка сети"

async def resolve_steam_id(text):
    text = text.strip()
    if text.endswith('/'): text = text[:-1]
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
    # В Supabase это транзакция через цепочку вызовов или последовательно
    supabase.table("users").upsert({"chat_id": chat_id, "steam_id": steam_id}).execute()
    supabase.table("user_items").delete().eq("chat_id", chat_id).execute()
    
    for name, count in items_counts.items():
        cat = get_item_category(name)
        # Сохраняем предмет
        item_res = supabase.table("items").upsert({"name": name, "category": cat}, on_conflict="name").execute()
        item_id = item_res.data[0]['id']
        # Привязываем к юзеру
        supabase.table("user_items").insert({"chat_id": chat_id, "item_id": item_id, "amount": count}).execute()

dp = Dispatcher()
bot_instance = None

# === КЛАВИАТУРЫ ===

def get_main_menu_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📱 Mini App", web_app=WebAppInfo(url="ВАШ_URL_GITHUB_PAGES"))],
        [KeyboardButton(text="📦 Инвентарь"), KeyboardButton(text="📈 Отслеживание")]
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
    for item in current_page_items:
        # Универсальная обработка структуры данных
        iid = item.get('id') or item.get('item_id')
        name = item.get('name') or item.get('item_name') or (item.get('items', {}).get('name'))
        amount = item.get('amount', 1)
        
        btn_text = f"{name} (x{amount})"
        if len(btn_text) > 40: btn_text = btn_text[:37] + "..."
        
        cb_data = f"trv_{iid}" if mode == "trc" else f"view_{iid}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=cb_data)])
    
    nav_row = []
    prefix = "pc" if mode == "cat" else "pw" if mode == "wep" else "pt"
    ctx_id = await get_ctx_id(value) if value else 0
    
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"{prefix}_{page-1}_{ctx_id}"))
    if end < len(items_data):
        nav_row.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"{prefix}_{page+1}_{ctx_id}"))
    
    if nav_row: keyboard.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# === ЛОГИКА ОБНОВЛЕНИЯ ===

async def update_inventory_logic(m: Message, state: FSMContext, silent=False):
    sid = await resolve_steam_id(m.text)
    if not sid: 
        if not silent: await m.answer("❌ Неверная ссылка или ID.")
        return False
    
    if not silent: wait = await m.answer("⏳ Сканирую инвентарь...")
    items_counts = await fetch_inventory(sid)
    
    if items_counts is None: 
        if not silent: await wait.edit_text("❌ Ошибка доступа. Проверь приватность профиля.")
        return False
    
    try:
        await save_inventory_to_db(m.chat.id, sid, items_counts or {})
    except Exception as e:
        logger.error(f"Supabase Error: {e}")
        if not silent: await wait.edit_text("❌ Ошибка облачной базы данных.")
        return False

    if not silent:
        await wait.delete()
        count = len(items_counts) if items_counts else 0
        await m.answer(f"✅ Найдено предметов: `{count}`.\nОблако обновлено!", reply_markup=get_main_menu_kb(), parse_mode="Markdown")
    await state.clear()
    return True

# === HANDLERS ===

@dp.message(F.text.contains("steamcommunity.com"))
async def global_link_update(m: Message, state: FSMContext):
    await update_inventory_logic(m, state)

@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    res = supabase.table("users").select("steam_id").eq("chat_id", m.chat.id).execute()
    if res.data:
        await m.answer("🏠 Главное меню:", reply_markup=get_main_menu_kb())
        await state.clear()
    else:
        await m.answer("👋 Привет! Пришли ссылку на Steam профиль.", reply_markup=ReplyKeyboardRemove())
        await state.set_state(Registration.waiting_for_steam_link)

@dp.message(Registration.waiting_for_steam_link)
async def process_link_reg(m: Message, state: FSMContext):
    await update_inventory_logic(m, state)

@dp.message(F.text == "📦 Инвентарь")
async def open_inventory(m: Message, state: FSMContext):
    res = supabase.table("user_items").select("items(category)").eq("chat_id", m.chat.id).execute()
    cats = sorted(list(set([r['items']['category'] for r in res.data if r.get('items')])))
    
    if not cats:
        user_res = supabase.table("users").select("steam_id").eq("chat_id", m.chat.id).execute()
        if user_res.data:
            status = await m.answer("🔄 Обновляю из Steam...")
            m.text = user_res.data[0]['steam_id']
            if await update_inventory_logic(m, state, silent=True):
                await status.delete()
                return await open_inventory(m, state)
        return await m.answer("Сначала пришлите ссылку на Steam.")

    await m.answer("Выберите категорию:", reply_markup=get_categories_kb(cats))
    await state.set_state(Registration.selecting_category)

@dp.message(Registration.selecting_category)
async def show_category_items(m: Message, state: FSMContext):
    if m.text == "🏠 В главное меню":
        await m.answer("🏠 Главное меню", reply_markup=get_main_menu_kb())
        return await state.clear()

    if m.text == "🔫 Оружие":
        res = supabase.table("user_items").select("items(name)").eq("chat_id", m.chat.id).execute()
        weapon_types = set()
        for r in res.data:
            name = r['items']['name']
            if "|" in name: weapon_types.add(name.split("|")[0].strip())
        
        if not weapon_types: return await m.answer("В этой категории пусто.")
        await m.answer("🔫 Выбери тип оружия:", reply_markup=get_weapon_types_kb(weapon_types))
        await state.set_state(Registration.selecting_weapon_type)
        return

    await send_paged_items(m.chat.id, category=m.text, page=0)

@dp.message(Registration.selecting_weapon_type)
async def show_weapon_type_items(m: Message, state: FSMContext):
    if m.text == "🔙 К категориям": return await open_inventory(m, state)
    await send_paged_items(m.chat.id, weapon_type=m.text, page=0)

async def send_paged_items(chat_id, category=None, weapon_type=None, page=0):
    mode = "cat"
    val = category
    
    if weapon_type:
        res = supabase.table("user_items").select("amount, items(id, name)").eq("chat_id", chat_id).ilike("items.name", f"{weapon_type} | %").execute()
        title = f"🔫 {weapon_type}"
        mode = "wep"
        val = weapon_type
    else:
        res = supabase.table("user_items").select("amount, items(id, name)").eq("chat_id", chat_id).eq("items.category", category).execute()
        title = f"📂 {category}"
    
    # Фильтруем None (если джоин не сработал) и мапим данные
    rows = []
    for r in res.data:
        if r.get('items'):
            rows.append({'id': r['items']['id'], 'name': r['items']['name'], 'amount': r['amount']})
    
    if not rows:
        await bot_instance.send_message(chat_id, "❌ Предметы не найдены.")
        return

    kb = await get_items_inline_kb(rows, page, mode=mode, value=val)
    await bot_instance.send_message(chat_id, f"*{title}*\nСтраница: {page+1}", reply_markup=kb, parse_mode="Markdown")

# === ОТСЛЕЖИВАНИЕ ===

@dp.message(F.text == "📈 Отслеживание")
async def cmd_tracking(m: Message):
    res = supabase.table("tracking").select("*").eq("chat_id", m.chat.id).order("item_name").execute()
    if not res.data: return await m.answer("Список отслеживания пуст.")
    kb = await get_items_inline_kb(res.data, page=0, mode="trc", value="tracking_list")
    await m.answer("*📈 Ваши отслеживаемые предметы:*", reply_markup=kb, parse_mode="Markdown")

# === ПАГИНАТОР И CALLBACKS ===

@dp.callback_query(F.data.startswith(("pc_", "pw_", "pt_")))
async def handle_pagination(call: CallbackQuery):
    parts = call.data.split("_")
    prefix, page = parts[0], int(parts[1])
    ctx_id = int(parts[2]) if len(parts) > 2 else 0
    value = await get_ctx_val(ctx_id) if ctx_id > 0 else ""

    if prefix == "pc": await send_paged_items(call.message.chat.id, category=value, page=page)
    elif prefix == "pw": await send_paged_items(call.message.chat.id, weapon_type=value, page=page)
    elif prefix == "pt":
        res = supabase.table("tracking").select("*").eq("chat_id", call.message.chat.id).order("item_name").execute()
        kb = await get_items_inline_kb(res.data, page=page, mode="trc", value="tracking_list")
        await bot_instance.send_message(call.message.chat.id, f"📈 Страница: {page+1}", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("view_"))
async def handle_view_item(call: CallbackQuery):
    item_id = int(call.data.split("_")[1])
    res = supabase.table("items").select("name").eq("id", item_id).execute()
    if not res.data: return
    name = res.data[0]['name']
    
    await call.answer("🔎 Ищу цену...")
    price_val, price_str = await get_steam_price(name)
    text = f"📦 *{name}*\n💰 *Цена:* `{price_str}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📈 Отслеживать", callback_data=f"track_{item_id}")]]) if price_val else None
    await call.message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("track_"))
async def handle_add_track(call: CallbackQuery):
    item_id = int(call.data.split("_")[1])
    res = supabase.table("items").select("name").eq("id", item_id).execute()
    name = res.data[0]['name']
    price_val, _ = await get_steam_price(name)
    
    try:
        supabase.table("tracking").insert({"chat_id": call.message.chat.id, "item_name": name, "last_price": price_val}).execute()
        await call.message.answer(f"✅ Отслеживаю: `{name}`")
    except:
        await call.answer("Уже в списке", show_alert=True)

@dp.callback_query(F.data.startswith("trv_"))
async def handle_track_view(call: CallbackQuery):
    track_id = int(call.data.split("_")[1])
    res = supabase.table("tracking").select("*").eq("id", track_id).execute()
    if not res.data: return
    row = res.data[0]
    current_price, price_str = await get_steam_price(row['item_name'])
    text = f"📦 *{row['item_name']}*\n📌 Было: `{row['last_price']}`\n💰 Сейчас: `{price_str}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Удалить", callback_data=f"deltr_{track_id}")]])
    await call.message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("deltr_"))
async def handle_del_track(call: CallbackQuery):
    track_id = int(call.data.split("_")[1])
    supabase.table("tracking").delete().eq("id", track_id).execute()
    await call.message.answer("❌ Удалено.")

@dp.message()
async def handle_fallback(m: Message, state: FSMContext):
    if m.text == "🏠 В главное меню":
        await m.answer("🏠 Главное меню", reply_markup=get_main_menu_kb())
        return await state.clear()
    await m.answer("Выберите пункт меню 👇", reply_markup=get_main_menu_kb())

async def monitor_prices_task():
    while True:
        try:
            res = supabase.table("tracking").select("*").execute()
            for row in res.data:
                await asyncio.sleep(5)
                curr, s_curr = await get_steam_price(row['item_name'])
                if curr and curr > float(row['last_price']):
                    msg = f"📈 *Цена UP!*\n`{row['item_name']}`\n`{row['last_price']}` -> `{s_curr}`"
                    await bot_instance.send_message(row['chat_id'], msg, parse_mode="Markdown")
                    supabase.table("tracking").update({"last_price": curr}).eq("id", row['id']).execute()
        except Exception as e: logger.error(f"Monitor: {e}")
        await asyncio.sleep(3600)

async def main():
    global bot_instance
    bot = Bot(token=TOKEN)
    bot_instance = bot
    
    app = web.Application()
    app.router.add_get('/api/inventory', get_app_inventory)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8080).start()
    
    asyncio.create_task(monitor_prices_task())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
