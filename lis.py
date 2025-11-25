from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio

TOKEN = "5070946103:AAFG8N40n9IPR3APhYxMeD-mB81-D7ss7Es"

# Стартовая клавиатура
main_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Отслеживать цену", callback_data="track_price")]
    ]
)

# Клавиатура с категориями
weapon_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Ножи", callback_data="cat_knives")],
        [InlineKeyboardButton(text="Пистолеты", callback_data="cat_pistols")],
        [InlineKeyboardButton(text="Пистолеты-пулеметы", callback_data="cat_smgs")],
        [InlineKeyboardButton(text="Винтовки", callback_data="cat_rifles")],
        [InlineKeyboardButton(text="Тяжелое оружие", callback_data="cat_heavy")],
    ]
)

# Клавиатура с видами ножей (по скриншоту)
knives_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Керамбит", callback_data="knife_karambit")],
        [InlineKeyboardButton(text="Штык-нож M9", callback_data="knife_m9")],
        [InlineKeyboardButton(text="Нож-бабочка", callback_data="knife_butterfly")],
        [InlineKeyboardButton(text="Коготь", callback_data="knife_talon")],
        [InlineKeyboardButton(text="Скелетный нож", callback_data="knife_skeleton")],
        [InlineKeyboardButton(text="Классический нож", callback_data="knife_classic")],
        [InlineKeyboardButton(text="Штык-нож", callback_data="knife_bayonet")],
        [InlineKeyboardButton(text="Стилет", callback_data="knife_stiletto")],
        [InlineKeyboardButton(text="Медвежий нож", callback_data="knife_ursus")],
        [InlineKeyboardButton(text="Паракорд-нож", callback_data="knife_paracord")],
        [InlineKeyboardButton(text="Нож «Бродяга»", callback_data="knife_nomad")],
        [InlineKeyboardButton(text="Нож выживания", callback_data="knife_survival")],
        [InlineKeyboardButton(text="Охотничий нож", callback_data="knife_huntsman")],
        [InlineKeyboardButton(text="Складной нож", callback_data="knife_flip")],
        [InlineKeyboardButton(text="Нож Боуи", callback_data="knife_bowie")],
        [InlineKeyboardButton(text="Фальшион", callback_data="knife_falchion")],
        [InlineKeyboardButton(text="Нож с лезвием-крюком", callback_data="knife_gut")],
        [InlineKeyboardButton(text="Кукри", callback_data="knife_kukri")],
        [InlineKeyboardButton(text="Наваха", callback_data="knife_navaja")],
        [InlineKeyboardButton(text="Тычковые ножи", callback_data="knife_shadow_daggers")],
    ]
)

async def start_handler(message: types.Message):
    await message.answer(
        "Привет! Я помогаю отслеживать цены на скины. Начать?",
        reply_markup=main_kb
    )

async def callback_handler(callback: types.CallbackQuery):
    if callback.data == "track_price":
        await callback.message.answer("Выберите категорию оружия:", reply_markup=weapon_kb)
    elif callback.data == "cat_knives":
        await callback.message.answer("Выберите тип ножа:", reply_markup=knives_kb)
    elif callback.data.startswith("knife_"):
        knife_name = callback.message.reply_markup.inline_keyboard[
            [btn.callback_data for btn in row][0] == callback.data
            ][0][0].text if any(callback.data == btn.callback_data for row in knives_kb.inline_keyboard for btn in row) else "Нож"
        await callback.message.answer(f"Выбран нож: {knife_name}")
    # Можешь добавить обработку других категорий (пистолеты, винтовки и т.д.) аналогично

async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.message.register(start_handler, Command(commands=["start"]))
    dp.callback_query.register(callback_handler)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
