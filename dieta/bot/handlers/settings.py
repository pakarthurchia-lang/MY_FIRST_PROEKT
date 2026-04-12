"""Settings — /settings and ⚙️ Настройки button. FSM for goal input."""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from db import database
from bot.keyboards.menus import cancel_kb

router = Router()


class SettingsForm(StatesGroup):
    kcal = State()
    protein = State()
    fat = State()
    carbs = State()


async def _show_settings(message: Message) -> None:
    user_id = message.from_user.id
    goals = await database.get_user_goals(user_id)
    await message.answer(
        f"<b>Текущие цели:</b>\n"
        f"Калории: {goals['goal_kcal']} ккал\n"
        f"Белки:   {goals['goal_protein']} г\n"
        f"Жиры:    {goals['goal_fat']} г\n"
        f"Углеводы:{goals['goal_carbs']} г\n\n"
        f"Хочешь изменить? Нажми /set_goals",
        parse_mode="HTML",
    )


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    await _show_settings(message)


@router.message(F.text == "⚙️ Настройки")
async def btn_settings(message: Message) -> None:
    await _show_settings(message)


@router.message(Command("set_goals"))
async def cmd_set_goals(message: Message, state: FSMContext) -> None:
    await state.set_state(SettingsForm.kcal)
    await message.answer(
        "Введи целевое количество калорий в день (ккал).\n"
        "Например: 2000",
        reply_markup=cancel_kb(),
    )


@router.message(SettingsForm.kcal)
async def form_kcal(message: Message, state: FSMContext) -> None:
    try:
        val = int(message.text.strip())
        assert 500 <= val <= 10000
    except (ValueError, AssertionError):
        await message.answer("Введи число от 500 до 10000.")
        return
    await state.update_data(kcal=val)
    await state.set_state(SettingsForm.protein)
    await message.answer("Белки (г/день)? Например: 150", reply_markup=cancel_kb())


@router.message(SettingsForm.protein)
async def form_protein(message: Message, state: FSMContext) -> None:
    try:
        val = int(message.text.strip())
        assert 10 <= val <= 500
    except (ValueError, AssertionError):
        await message.answer("Введи число от 10 до 500.")
        return
    await state.update_data(protein=val)
    await state.set_state(SettingsForm.fat)
    await message.answer("Жиры (г/день)? Например: 67", reply_markup=cancel_kb())


@router.message(SettingsForm.fat)
async def form_fat(message: Message, state: FSMContext) -> None:
    try:
        val = int(message.text.strip())
        assert 5 <= val <= 500
    except (ValueError, AssertionError):
        await message.answer("Введи число от 5 до 500.")
        return
    await state.update_data(fat=val)
    await state.set_state(SettingsForm.carbs)
    await message.answer("Углеводы (г/день)? Например: 250", reply_markup=cancel_kb())


@router.message(SettingsForm.carbs)
async def form_carbs(message: Message, state: FSMContext) -> None:
    try:
        val = int(message.text.strip())
        assert 10 <= val <= 1000
    except (ValueError, AssertionError):
        await message.answer("Введи число от 10 до 1000.")
        return

    data = await state.get_data()
    await state.clear()

    await database.update_user_goals(
        user_id=message.from_user.id,
        goal_kcal=data["kcal"],
        goal_protein=data["protein"],
        goal_fat=data["fat"],
        goal_carbs=val,
    )
    await message.answer(
        f"Цели обновлены!\n"
        f"Калории: {data['kcal']} ккал\n"
        f"Белки:   {data['protein']} г\n"
        f"Жиры:    {data['fat']} г\n"
        f"Углеводы:{val} г",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cancel_settings")
async def cb_cancel_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()
