from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from db import database
from bot.keyboards.menus import main_menu

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await database.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "Привет! Я твой дневник питания.\n\n"
        "Просто скажи голосом или напиши что ты съел — я посчитаю КБЖУ и добавлю в дневник.\n\n"
        "Примеры:\n"
        "• Голосовое: «съел вареную куриную грудку 150 грамм»\n"
        "• Текст: творог 5% 200г\n"
        "• Текст: гречка отварная 300 г с маслом 10г\n\n"
        "Команды:\n"
        "/diary — дневник за сегодня\n"
        "/stats — статистика за 7 дней\n"
        "/settings — цели по КБЖУ",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Как пользоваться:\n\n"
        "1. Отправь голосовое или текст — что съел и сколько граммов.\n"
        "2. Я распознаю продукт и посчитаю КБЖУ.\n"
        "3. Подтверди — и запись появится в дневнике.\n\n"
        "/diary — посмотреть дневник\n"
        "/stats — статистика за неделю\n"
        "/settings — изменить цели\n"
    )
