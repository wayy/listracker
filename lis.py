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

# === КОНФИГУРАЦИЯ ===
# 1. Твоя ссылка на GitHub Pages
MY_WEB_APP_URL = "https://wayy.github.io/listracker/"

# 2. Твои ключи Supabase
SUPABASE_URL = "https://trufweyemrkdogsszike.supabase.co"
SUPABASE_KEY = "sb_publishable_Ywujm6u8WqyG9Y4K5ANOsA_E8umiZqc"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 3. Константы бота
TOKEN = "5070946103:AAFG8N40n9IPR3APhYxMeD-mB81-D7ss7Es"
APP_ID = 730  # CS2

# === АВТО-УСТАНОВКА ПАКЕТОВ ===
def install_missing_packages():
    packages = ["supabase", "aiogram", "aiohttp", "python-dotenv"]
    for package in packages:
        try:
            dist_name = "supabase" if package == "supabase" else package
            __import__(dist_name)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])

install_missing_packages()

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, WebAppInfo,
    MenuButtonWebApp
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}

class Registration(StatesGroup):
    waiting_for_steam_link = State()
    selecting_category = State()
    selecting_weapon_type = State()

# === API ДЛЯ MINI APP ===
async def get_app_inventory(request):
    chat_id = request.query.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "no_id"}, status=400)
    try:
        res = supabase.table("user_items").select("amount, items(name, category)").eq("chat_id", chat_id).execute()
        items = [{"name": r['items']['name'], "amount": r['amount'], "category": r['items']['category']} for r in res.data if r.get('items')]
        return web.json_response(items, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"API Error: {e}")
        return web.json_response({"error": "db_error"}, status=500)

# === ВСПОМОГАТЕЛЬНАЯ ЛОГИКА (ПАРСИНГ, КАТЕГОРИИ) ===
def get_item_category(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ["case", "кейс", "пакет", "набор"]): return "📦 Кейсы"
    if any(x in n for x in ["sticker", "наклейка"]): return "🎯 Наклейки"
    if any(x in n for x in ["agent", "агент"]): return "👤 Агенты"
    if any(x in n for x in ["music kit", "музыка"]): return "🎵 Музыка"
    if any(x in n for x in ["graffiti", "граффити"]): return "🎨 Граффити"
    if "|" in name: return "🔫 Оружие"
    return "🛠 Прочее"

async def get_ctx_id(val: str) -> int:
    if not val: return 0
    res = supabase.table("context_map").upsert({"val": val}, on_conflict="val").execute()
    return res.data[0]['id']

async def get_ctx_val(ctx_id: int) -> str:
    res = supabase.table("context_map").select("val").eq("id", ctx_id).execute()
    return res.data[0]['val'] if res.data else None

def parse_price(price_str):
    if not price_str: return 0.0
    clean = re.sub(r'[^\d.,]', '', price_str).strip('.').replace(',', '.')
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
                    return parse_price(price_str), price_str or "N/A"
                return None, f"Steam Error: {r.status}"
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
        async with aiohttp.ClientSession(headers=HEADERS) as s:
            async with s.get(url) as r:
                content = await r.text()
                res = re.search(r'<steamID64>(\d+)</steamID64>', content)
                return res.group(1) if res else None
    return None

async def fetch_inventory(steam_id):
    # Исправленная логика подсчета количества (assets + descriptions)
    url = f"https://steamcommunity.com/inventory/{steam_id}/{APP_ID}/2?l=russian&count=2000"
    async with aiohttp.ClientSession(headers=HEADERS) as s:
        try:
            async with s.get(url, timeout=20) as r:
                if r.status != 200: return None
                data = await r.json()
                if not data or "assets" not in data: return Counter()
                descriptions = {d["classid"]: d["market_hash_name"] for d in data.get("descriptions", []) if d.get("marketable")}
                inventory = Counter()
                for asset in data["assets"]:
                    name = descriptions.get(asset["classid"])
                    if name: inventory[name] += 1
                return inventory
        except: return None

async def save_inventory_to_db(chat_id, steam_id, items_counts):
    supabase.table("users").upsert({"chat_id": chat_id, "steam_id": steam_id}).execute()
    supabase.table("user_items").delete().eq("chat_id", chat_id).execute()
    for name, count in items_counts.items():
        cat = get_item_category(name)
        item_res = supabase.table("items").upsert({"name": name, "category": cat}, on_conflict="name").execute()
        item_id = item_res.data[0]['id']
        supabase.table("user_items").insert({"chat_id": chat_id, "item_id": item_id, "amount": count}).execute()

# === КЛАВИАТУРЫ ===
def get_main_menu_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📦 Инвентарь"), KeyboardButton(text="📈 Отслеживание")]], resize_keyboard=True)

def get_categories_kb(items):
    items = sorted(list(items))
    btns = [[KeyboardButton(text=items[i]), KeyboardButton(text=items[i+1])] if i+1 < len(items) else [KeyboardButton(text=items[i])] for i in range(0, len(items), 2)]
    btns.append([KeyboardButton(text="🏠 В главное меню")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def get_weapon_types_kb(items):
    items = sorted(list(items))
    btns = [[KeyboardButton(text=items[i]), KeyboardButton(text=items[i+1])] if i+1 < len(items) else [KeyboardButton(text=items[i])] for i in range(0, len(items), 2)]
    btns.append([KeyboardButton(text="🔙 К категориям")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

async def get_items_inline_kb(items_data, page=0, mode="cat", value=""):
    ITEMS_PER_PAGE = 10
    start, end = page * ITEMS_PER_PAGE, (page + 1) * ITEMS_PER_PAGE
    curr = items_data[start:end]
    keyboard = []
    for item in curr:
        iid = item.get('id') or item.get('item_id')
        name = item.get('name') or item.get('item_name') or (item.get('items', {}).get('name'))
        amount = item.get('amount', 1)
        txt = f"{name} (x{amount})"
        keyboard.append([InlineKeyboardButton(text=txt[:40], callback_data=f"{'trv' if mode=='trc' else 'view'}_{iid}")])
    nav = []
    ctx_id = await get_ctx_id(value) if value else 0
    prefix = "pc" if mode == "cat" else "pw" if mode == "wep" else "pt"
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}_{page-1}_{ctx_id}"))
    if end < len(items_data): nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}_{page+1}_{ctx_id}"))
    if nav: keyboard.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# === ХЕНДЛЕРЫ БОТА ===
dp = Dispatcher()
bot_instance = None

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
    sid = await resolve_steam_id(m.text)
    if not sid: return await m.answer("❌ Ошибка в ссылке.")
    wait = await m.answer("⏳ Сканирую...")
    counts = await fetch_inventory(sid)
    if counts is None: return await wait.edit_text("❌ Профиль закрыт.")
    await save_inventory_to_db(m.chat.id, sid, counts)
    await wait.delete()
    await m.answer(f"✅ Готово! Найдено {len(counts)} позиций.", reply_markup=get_main_menu_kb())
    await state.clear()

@dp.message(F.text == "📦 Инвентарь")
async def open_inventory(m: Message, state: FSMContext):
    res = supabase.table("user_items").select("items(category)").eq("chat_id", m.chat.id).execute()
    cats = sorted(list(set([r['items']['category'] for r in res.data if r.get('items')])))
    if not cats: return await m.answer("Инвентарь пуст.")
    await m.answer("Категория:", reply_markup=get_categories_kb(cats))
    await state.set_state(Registration.selecting_category)

@dp.message(Registration.selecting_category)
async def show_category_items(m: Message, state: FSMContext):
    if m.text == "🏠 В главное меню":
        await m.answer("🏠 Меню", reply_markup=get_main_menu_kb()); return await state.clear()
    if m.text == "🔫 Оружие":
        res = supabase.table("user_items").select("items(name)").eq("chat_id", m.chat.id).execute()
        weapon_types = sorted(list(set([r['items']['name'].split("|")[0].strip() for r in res.data if "|" in r['items']['name']])))
        return await m.answer("Тип оружия:", reply_markup=get_weapon_types_kb(weapon_types))
    await send_paged_items(m.chat.id, category=m.text)

async def send_paged_items(chat_id, category=None, weapon_type=None, page=0):
    if weapon_type:
        res = supabase.table("user_items").select("amount, items(id, name)").eq("chat_id", chat_id).ilike("items.name", f"{weapon_type} | %").execute()
    else:
        res = supabase.table("user_items").select("amount, items(id, name)").eq("chat_id", chat_id).eq("items.category", category).execute()
    rows = [{'id': r['items']['id'], 'name': r['items']['name'], 'amount': r['amount']} for r in res.data if r.get('items')]
    kb = await get_items_inline_kb(rows, page, mode="cat" if category else "wep", value=category or weapon_type)
    await bot_instance.send_message(chat_id, f"Страница {page+1}", reply_markup=kb)

@dp.callback_query(F.data.startswith("view_"))
async def handle_view_item(call: CallbackQuery):
    iid = int(call.data.split("_")[1])
    res = supabase.table("items").select("name").eq("id", iid).execute()
    name = res.data[0]['name']
    await call.answer("🔎 Ищу цену...")
    p_val, p_str = await get_steam_price(name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📈 Трекать", callback_data=f"track_{iid}")]]) if p_val else None
    await call.message.answer(f"📦 {name}\n💰 {p_str}", reply_markup=kb)

@dp.callback_query(F.data.startswith("track_"))
async def handle_add_track(call: CallbackQuery):
    iid = int(call.data.split("_")[1])
    name = supabase.table("items").select("name").eq("id", iid).execute().data[0]['name']
    p_val, _ = await get_steam_price(name)
    try:
        supabase.table("tracking").insert({"chat_id": call.message.chat.id, "item_name": name, "last_price": p_val}).execute()
        await call.message.answer(f"✅ Трекаю {name}")
    except: await call.answer("Уже трекается", show_alert=True)

# === МОНИТОРИНГ ЦЕН (ФОНОВАЯ ЗАДАЧА) ===
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
        except: pass
        await asyncio.sleep(3600)

async def main():
    global bot_instance
    bot = Bot(token=TOKEN); bot_instance = bot
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Инвентарь", web_app=WebAppInfo(url=MY_WEB_APP_URL)))
    app = web.Application()
    app.router.add_get('/api/inventory', get_app_inventory)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8080).start()
    asyncio.create_task(monitor_prices_task())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
