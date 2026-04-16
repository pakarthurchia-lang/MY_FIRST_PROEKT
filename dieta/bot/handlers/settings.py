"""Settings — /settings and ⚙️ Настройки button. FSM for goal input."""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from db import database
from bot.keyboards.menus import cancel_kb, settings_kb

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
        f"<b>Текущие цели на день:</b>\n\n"
        f"🔥 Калории:  <b>{goals['goal_kcal']}</b> ккал\n"
        f"🥩 Белки:    <b>{goals['goal_protein']}</b> г\n"
        f"🧈 Жиры:     <b>{goals['goal_fat']}</b> г\n"
        f"🍞 Углеводы: <b>{goals['goal_carbs']}</b> г",
        reply_markup=settings_kb(),
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
    await _start_goals_form(message, state)


@router.callback_query(F.data == "edit_goals")
async def cb_edit_goals(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _start_goals_form(callback.message, state)


async def _start_goals_form(message: Message, state: FSMContext) -> None:
    await state.set_state(SettingsForm.kcal)
    await message.answer(
        "Введи целевое количество <b>калорий</b> в день (ккал).\n"
        "Например: <code>2000</code>",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
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
    await message.answer(
        "Теперь <b>белки</b> (г/день).\nНапример: <code>150</code>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )


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
    await message.answer(
        "Теперь <b>жиры</b> (г/день).\nНапример: <code>67</code>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )


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
    await message.answer(
        "И последнее — <b>углеводы</b> (г/день).\nНапример: <code>250</code>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )


@router.message(SettingsForm.carbs)
async def form_carbs(message: Message, state: FSMContext) -> None:
    try:
        val = int(message.text.strip())
        assert 10 <= val <= 1000
    except (ValueError, AssertionError):
        await message.answer("Введи число от 10 до 1000.")
        return

    data = await state.get_data()

    # Проверка: сумма калорий из БЖУ должна совпадать с целью
    macro_kcal = data["protein"] * 4 + data["fat"] * 9 + val * 4
    goal_kcal  = data["kcal"]
    diff       = abs(macro_kcal - goal_kcal)

    if diff > 50:  # допуск 50 ккал
        await message.answer(
            f"⚠️ <b>Ошибка в расчётах!</b>\n\n"
            f"Белки {data['protein']}г × 4 = {data['protein']*4} ккал\n"
            f"Жиры  {data['fat']}г × 9 = {data['fat']*9} ккал\n"
            f"Углев {val}г × 4 = {val*4} ккал\n"
            f"<b>Итого: {macro_kcal} ккал</b> — а цель {goal_kcal} ккал "
            f"(расхождение {diff} ккал).\n\n"
            f"Введи углеводы заново так, чтобы сумма сошлась.\n"
            f"Подсказка: <code>{max(0, round((goal_kcal - data['protein']*4 - data['fat']*9) / 4))}</code> г",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )
        return  # остаёмся в состоянии SettingsForm.carbs

    await state.clear()
    await database.update_user_goals(
        user_id=message.from_user.id,
        goal_kcal=goal_kcal,
        goal_protein=data["protein"],
        goal_fat=data["fat"],
        goal_carbs=val,
    )
    await message.answer(
        f"✅ <b>Цели обновлены!</b>\n\n"
        f"🔥 Калории:  <b>{goal_kcal}</b> ккал\n"
        f"🥩 Белки:    <b>{data['protein']}</b> г = {data['protein']*4} ккал\n"
        f"🧈 Жиры:     <b>{data['fat']}</b> г = {data['fat']*9} ккал\n"
        f"🍞 Углеводы: <b>{val}</b> г = {val*4} ккал\n"
        f"<i>Сумма: {macro_kcal} ккал ✓</i>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cancel_settings")
async def cb_cancel_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()
