from __future__ import annotations
"""Daily food diary with add / edit / delete."""
from datetime import date

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from db import database

router = Router()

MEAL_ORDER = ["breakfast", "lunch", "dinner", "snack", "other"]
MEAL_ICONS = {
    "breakfast": "Завтрак",
    "lunch":     "Обед",
    "dinner":    "Ужин",
    "snack":     "Перекус",
    "other":     "Другое",
}
MEAL_TYPES = {
    "breakfast": "Завтрак",
    "lunch":     "Обед",
    "dinner":    "Ужин",
    "snack":     "Перекус",
    "other":     "Другое",
}


class EditForm(StatesGroup):
    weight = State()   # ждём новый вес
    name   = State()   # ждём новое название


# ── Keyboards ──────────────────────────────────────────────────────────────────

def _entries_kb(entries: list[dict]) -> InlineKeyboardMarkup:
    """One row per entry: [✏️ name  Xг] [🗑]"""
    rows = []
    for e in entries:
        label = f"{e['food_name'][:20]} {e['weight_g']:.0f}г"
        rows.append([
            InlineKeyboardButton(text=f"✏️  {label}", callback_data=f"edit:{e['id']}"),
            InlineKeyboardButton(text="🗑",             callback_data=f"del:{e['id']}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _edit_options_kb(entry_id: int, food_name: str, weight_g: float) -> InlineKeyboardMarkup:
    short = f"{food_name[:18]} {weight_g:.0f}г"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📝  Название",    callback_data=f"edit_n:{entry_id}"),
         InlineKeyboardButton(text=f"⚖️  Вес",         callback_data=f"edit_w:{entry_id}")],
        [InlineKeyboardButton(text=f"🍽  Приём пищи",  callback_data=f"edit_m:{entry_id}")],
        [InlineKeyboardButton(text="← Назад к дневнику", callback_data="diary_back")],
    ])


def _edit_meal_kb(entry_id: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"set_m:{meal}:{entry_id}")
        for meal, label in MEAL_TYPES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        buttons[:3],
        buttons[3:],
        [InlineKeyboardButton(text="← Назад", callback_data=f"edit:{entry_id}")],
    ])


def _cancel_edit_kb(entry_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отмена", callback_data=f"edit:{entry_id}")]
    ])


# ── Diary text builder ─────────────────────────────────────────────────────────

def _build_diary_text(entries: list[dict], totals: dict, goals: dict, today: str) -> str:
    if not entries:
        return (
            f"<b>Дневник за {today}</b>\n\n"
            "Записей нет.\n"
            "Просто напиши или отправь голосовое — что ты съел."
        )

    groups: dict[str, list] = {m: [] for m in MEAL_ORDER}
    for e in entries:
        groups.setdefault(e["meal_type"], []).append(e)

    lines = [f"<b>Дневник за {today}</b>\n"]
    for meal in MEAL_ORDER:
        meal_entries = groups.get(meal, [])
        if not meal_entries:
            continue
        lines.append(f"\n<b>{MEAL_ICONS[meal]}</b>")
        for e in meal_entries:
            lines.append(
                f"  • {e['food_name']} {e['weight_g']:.0f}г — {e['kcal']:.0f} ккал"
                f"  (Б:{e['protein']:.1f} Ж:{e['fat']:.1f} У:{e['carbs']:.1f})"
            )

    def pct(v, g): return int(v / g * 100) if g else 0
    def bar(v, g, w=8):
        filled = min(int(round(v / g * w)), w) if g else 0
        return "█" * filled + "░" * (w - filled)

    lines.append(
        f"\n<b>Итого:</b>\n"
        f"Калории:  {totals['kcal']:.0f} / {goals['goal_kcal']} ккал  "
        f"{bar(totals['kcal'], goals['goal_kcal'])} {pct(totals['kcal'], goals['goal_kcal'])}%\n"
        f"Белки:    {totals['protein']:.1f} / {goals['goal_protein']} г\n"
        f"Жиры:     {totals['fat']:.1f} / {goals['goal_fat']} г\n"
        f"Углеводы: {totals['carbs']:.1f} / {goals['goal_carbs']} г\n\n"
        f"<i>Нажми ✏️ чтобы изменить запись, 🗑 чтобы удалить.</i>"
    )
    return "\n".join(lines)


# ── Shared diary refresh ───────────────────────────────────────────────────────

async def _refresh_diary(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    today = date.today().isoformat()
    entries = await database.get_day_entries(user_id, today)
    totals  = await database.get_day_totals(user_id, today)
    goals   = await database.get_user_goals(user_id)
    text = _build_diary_text(entries, totals, goals, today)
    kb   = _entries_kb(entries) if entries else None
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


async def _send_diary(message: Message) -> None:
    user_id = message.from_user.id
    today = date.today().isoformat()
    entries = await database.get_day_entries(user_id, today)
    totals  = await database.get_day_totals(user_id, today)
    goals   = await database.get_user_goals(user_id)
    text = _build_diary_text(entries, totals, goals, today)
    kb   = _entries_kb(entries) if entries else None
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ── Commands ───────────────────────────────────────────────────────────────────

@router.message(Command("diary"))
async def cmd_diary(message: Message) -> None:
    await _send_diary(message)


@router.message(F.text == "📊 Дневник")
async def btn_diary(message: Message) -> None:
    await _send_diary(message)


# ── Delete ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("del:"))
async def cb_delete(callback: CallbackQuery) -> None:
    entry_id = int(callback.data.split(":")[1])
    deleted = await database.delete_entry(entry_id, callback.from_user.id)
    if not deleted:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    await callback.answer("Удалено.")
    await _refresh_diary(callback)


# ── Edit: show options ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("edit:"))
async def cb_edit_entry(callback: CallbackQuery) -> None:
    entry_id = int(callback.data.split(":")[1])
    entry = await database.get_entry(entry_id, callback.from_user.id)
    if not entry:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    meal_label = MEAL_ICONS.get(entry["meal_type"], entry["meal_type"])
    text = (
        f"<b>{entry['food_name']}</b> — {entry['weight_g']:.0f} г\n"
        f"Приём: {meal_label}\n\n"
        f"Калории: {entry['kcal']:.0f} ккал  "
        f"Б:{entry['protein']:.1f} Ж:{entry['fat']:.1f} У:{entry['carbs']:.1f}\n\n"
        f"Что изменить?"
    )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_edit_options_kb(entry_id, entry["food_name"], entry["weight_g"]),
    )
    await callback.answer()


# ── Edit: weight (FSM) ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("edit_w:"))
async def cb_edit_weight_start(callback: CallbackQuery, state: FSMContext) -> None:
    entry_id = int(callback.data.split(":")[1])
    entry = await database.get_entry(entry_id, callback.from_user.id)
    if not entry:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    await state.set_state(EditForm.weight)
    await state.update_data(entry_id=entry_id)

    await callback.message.edit_text(
        f"<b>{entry['food_name']}</b>\n"
        f"Текущий вес: <b>{entry['weight_g']:.0f} г</b>\n\n"
        f"Введи новый вес в граммах:",
        parse_mode="HTML",
        reply_markup=_cancel_edit_kb(entry_id),
    )
    await callback.answer()


@router.message(EditForm.weight)
async def fsm_new_weight(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    entry_id = data.get("entry_id")

    try:
        new_weight = float(message.text.strip().replace(",", "."))
        assert 1 <= new_weight <= 5000
    except (ValueError, AssertionError):
        await message.answer("Введи число от 1 до 5000 (граммы).")
        return

    updated = await database.update_entry_weight(entry_id, message.from_user.id, new_weight)
    await state.clear()

    if not updated:
        await message.answer("Не удалось обновить запись.")
        return

    await message.answer(f"Вес обновлён: {new_weight:.0f} г. КБЖУ пересчитан.")
    await _send_diary(message)


# ── Edit: food name (FSM) ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("edit_n:"))
async def cb_edit_name_start(callback: CallbackQuery, state: FSMContext) -> None:
    entry_id = int(callback.data.split(":")[1])
    entry = await database.get_entry(entry_id, callback.from_user.id)
    if not entry:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    await state.set_state(EditForm.name)
    await state.update_data(entry_id=entry_id)

    await callback.message.edit_text(
        f"Текущее название: <b>{entry['food_name']}</b>\n\n"
        f"Введи новое название:",
        parse_mode="HTML",
        reply_markup=_cancel_edit_kb(entry_id),
    )
    await callback.answer()


@router.message(EditForm.name)
async def fsm_new_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    entry_id = data.get("entry_id")
    new_name = message.text.strip()

    if not new_name or len(new_name) > 100:
        await message.answer("Название должно быть от 1 до 100 символов.")
        return

    updated = await database.update_entry_name(entry_id, message.from_user.id, new_name)
    await state.clear()

    if not updated:
        await message.answer("Не удалось обновить запись.")
        return

    await message.answer(f"Название обновлено: {new_name}")
    await _send_diary(message)


# ── Edit: meal type ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("edit_m:"))
async def cb_edit_meal_start(callback: CallbackQuery) -> None:
    entry_id = int(callback.data.split(":")[1])
    entry = await database.get_entry(entry_id, callback.from_user.id)
    if not entry:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    await callback.message.edit_text(
        f"<b>{entry['food_name']}</b> {entry['weight_g']:.0f}г\n\n"
        f"Выбери приём пищи:",
        parse_mode="HTML",
        reply_markup=_edit_meal_kb(entry_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_m:"))
async def cb_set_meal_type(callback: CallbackQuery) -> None:
    _, meal_type, entry_id_str = callback.data.split(":", 2)
    entry_id = int(entry_id_str)

    updated = await database.update_entry_meal_type(entry_id, callback.from_user.id, meal_type)
    if not updated:
        await callback.answer("Не удалось обновить.", show_alert=True)
        return

    await callback.answer(f"Приём изменён: {MEAL_ICONS.get(meal_type, meal_type)}")
    await _refresh_diary(callback)


# ── Back to diary ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "diary_back")
async def cb_diary_back(callback: CallbackQuery) -> None:
    await _refresh_diary(callback)
    await callback.answer()
