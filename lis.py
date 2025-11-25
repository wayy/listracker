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

# Клавиатура с категориями (замените на категории из скриншота)
weapon_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Ножи", callback_data="cat_knives")],
        [InlineKeyboardButton(text="Пистолеты", callback_data="cat_pistols")],
        [InlineKeyboardButton(text="Пистолеты-пулеметы", callback_data="cat_smgs")],
        [InlineKeyboardButton(text="Винтовки", callback_data="cat_rifles")],
        [InlineKeyboardButton(text="Тяжелое оружие", callback_data="cat_heavy")],
    ]
)

async def start_handler(message: types.Message):
    await message.answer("Привет! Я помогаю отслеживать цены на скины. Начать?", reply_markup=main_kb)

async def callback_handler(callback: types.CallbackQuery):
    if callback.data == "track_price":
        await callback.message.answer("Выберите категорию оружия:", reply_markup=weapon_kb)
    # Добавьте обработку дальнейших кнопок категорий...

async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher()

    dp.message.register(start_handler, Command(commands=["start"]))
    dp.callback_query.register(callback_handler)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


