"""
Ввод товарооборота вручную.
"""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import OWNER_CHAT_ID
from db.database import upsert_turnover, get_turnover
from ozon.scraper import _get_all_stores

router = Router()

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


class TurnoverState(StatesGroup):
    choosing_month = State()
    choosing_pvz = State()
    entering_amount = State()


def _month_keyboard() -> InlineKeyboardMarkup:
    from datetime import date
    today = date.today()
    buttons = []
    for i in range(6):
        m = today.month - i
        y = today.year
        if m <= 0:
            m += 12
            y -= 1
        buttons.append([InlineKeyboardButton(
            text=f"{MONTHS_RU[m]} {y}",
            callback_data=f"to_month:{m}:{y}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Отмена", callback_data="to_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "menu:turnover")
async def cb_turnover_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await state.set_state(TurnoverState.choosing_month)
    await call.message.answer(
        "📝 <b>Ввод товарооборота</b>\n\nВыбери месяц:",
        parse_mode="HTML",
        reply_markup=_month_keyboard()
    )


@router.callback_query(F.data.startswith("to_month:"), TurnoverState.choosing_month)
async def cb_turnover_month(call: CallbackQuery, state: FSMContext):
    await call.answer()
    _, month_str, year_str = call.data.split(":")
    month, year = int(month_str), int(year_str)
    await state.update_data(month=month, year=year)

    stores = await _get_all_stores()
    buttons = [
        [InlineKeyboardButton(
            text=s.get("name", str(s["id"])),
            callback_data=f"to_pvz:{s['id']}"
        )]
        for s in stores
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="to_back_month")])

    await state.set_state(TurnoverState.choosing_pvz)
    await call.message.answer(
        f"📅 {MONTHS_RU[month]} {year}\n\nВыбери ПВЗ:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data == "to_back_month", TurnoverState.choosing_pvz)
async def cb_back_to_month(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TurnoverState.choosing_month)
    await call.message.answer("Выбери месяц:", reply_markup=_month_keyboard())


@router.callback_query(F.data.startswith("to_pvz:"), TurnoverState.choosing_pvz)
async def cb_turnover_pvz(call: CallbackQuery, state: FSMContext):
    await call.answer()
    store_id = int(call.data.split(":")[1])

    stores = await _get_all_stores()
    pvz_name = next((s.get("name", str(s["id"])) for s in stores if s["id"] == store_id), str(store_id))

    data = await state.get_data()
    month, year = data["month"], data["year"]

    # Показываем текущее значение если есть
    existing = await get_turnover(pvz_name, month, year)
    existing_str = f"\nТекущее значение: <b>{existing:,.2f} руб.</b>" if existing else ""

    await state.update_data(pvz_name=pvz_name)
    await state.set_state(TurnoverState.entering_amount)
    await call.message.answer(
        f"🏪 <b>{pvz_name}</b> — {MONTHS_RU[month]} {year}{existing_str}\n\n"
        f"Введи товарооборот в рублях (только цифры, например: <code>1500000</code>):",
        parse_mode="HTML"
    )


@router.message(TurnoverState.entering_amount)
async def cb_turnover_amount(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    text = message.text.strip().replace(" ", "").replace(",", ".").replace("₽", "")
    try:
        amount = float(text)
    except ValueError:
        await message.answer("❌ Не могу распознать сумму. Введи число, например: <code>1500000</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    pvz_name = data["pvz_name"]
    month = data["month"]
    year = data["year"]

    await upsert_turnover(pvz_name, month, year, amount)
    await state.clear()

    from bot.handlers.menu import main_menu
    await message.answer(
        f"✅ Сохранено!\n"
        f"🏪 {pvz_name} — {MONTHS_RU[month]} {year}\n"
        f"💼 Товарооборот: <b>{amount:,.2f} руб.</b>",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


@router.callback_query(F.data == "to_cancel")
async def cb_turnover_cancel(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    from bot.handlers.menu import main_menu
    await call.message.answer("Отменено.", reply_markup=main_menu())
