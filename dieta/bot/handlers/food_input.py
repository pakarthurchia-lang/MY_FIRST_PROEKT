from __future__ import annotations
"""
Unified voice + text handler with intent detection.

Intents routed here:
  add_food    → nutrition parse → confirmation card → meal type → save
  delete      → confirm → delete entry
  edit_weight → confirm → update weight
  edit_meal   → confirm → update meal type
  edit_name   → confirm → update name
  show_kbju   → quick КБЖУ card
  start_day   → mark day start
  close_day   → close day flow
  show_journal→ journal list
  unknown     → fallback to add_food
"""
import io
import json
import hashlib
from datetime import date

from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.services import stt, nutrition
from bot.services.intent import detect_intent, format_diary_readable
from bot.services import barcode as barcode_svc
from bot.services import fatsecret
from bot.keyboards.menus import confirm_food_kb, meal_type_kb, MEAL_TYPES
from db import database

router = Router()

_pending: dict[str, dict] = {}


class BarcodeForm(StatesGroup):
    weight = State()

MEAL_ICONS = {
    "breakfast": "Завтрак",
    "lunch":     "Обед",
    "dinner":    "Ужин",
    "snack":     "Перекус",
    "other":     "Другое",
}

# Buttons handled by other routers — must be excluded here
_MENU_BUTTONS = {"📊 Дневник", "📈 Статистика", "⚙️ Настройки", "⚡ КБЖУ", "📋 Журнал"}


# ── Formatters ─────────────────────────────────────────────────────────────────

def _make_key(data: dict, user_id: int) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True) + str(user_id)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _format_nutrition_card(data: dict) -> str:
    t = data["total"]
    source_tag = " <i>(FatSecret)</i>" if data.get("source") == "fatsecret" else " <i>(ИИ)</i>"
    return (
        f"<b>{data['food_name']}</b> — {data['weight_g']} г{source_tag}\n\n"
        f"Калории:  <b>{t['kcal']}</b> ккал\n"
        f"Белки:    <b>{t['protein']}</b> г\n"
        f"Жиры:     <b>{t['fat']}</b> г\n"
        f"Углеводы: <b>{t['carbs']}</b> г"
    )


def _format_totals(totals: dict, goals: dict) -> str:
    def bar(val, goal, width=10):
        filled = min(int(round(val / goal * width)), width) if goal else 0
        return "█" * filled + "░" * (width - filled)
    pct = lambda v, g: int(v / g * 100) if g else 0
    return (
        f"\n\n<b>Итого за сегодня:</b>\n"
        f"Калории:  {totals['kcal']:.0f} / {goals['goal_kcal']} ккал  "
        f"{bar(totals['kcal'], goals['goal_kcal'])} {pct(totals['kcal'], goals['goal_kcal'])}%\n"
        f"Белки:    {totals['protein']:.1f} / {goals['goal_protein']} г\n"
        f"Жиры:     {totals['fat']:.1f} / {goals['goal_fat']} г\n"
        f"Углеводы: {totals['carbs']:.1f} / {goals['goal_carbs']} г"
    )


def _action_confirm_kb(key: str, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, подтвердить", callback_data=f"va_ok:{action}:{key}"),
        InlineKeyboardButton(text="Отмена",           callback_data="va_cancel"),
    ]])


# ── Voice handler ──────────────────────────────────────────────────────────────

@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot, state: FSMContext) -> None:
    await database.ensure_user(message.from_user.id, message.from_user.username)
    status = await message.answer("Распознаю голос...")

    voice_file = await bot.get_file(message.voice.file_id)
    buf = io.BytesIO()
    await bot.download_file(voice_file.file_path, destination=buf)

    try:
        text = await stt.transcribe(buf.getvalue())
    except Exception as e:
        await status.edit_text(f"Не удалось распознать голос: {e}")
        return

    if not text or len(text.strip()) < 3:
        await status.edit_text(
            "Не расслышал — запись слишком короткая или тихая.\n"
            "Попробуй ещё раз или напиши текстом."
        )
        return

    await status.edit_text(f"Слышу: «{text}»\nАнализирую...")
    await _process(message, status, text, state)


# ── Text handler ───────────────────────────────────────────────────────────────

@router.message(
    F.text
    & ~F.text.startswith("/")
    & ~F.text.in_(_MENU_BUTTONS)
)
async def handle_text(message: Message, state: FSMContext) -> None:
    await database.ensure_user(message.from_user.id, message.from_user.username)
    status = await message.answer("Анализирую...")
    await _process(message, status, message.text, state)


# ── Shared processing ──────────────────────────────────────────────────────────

async def _process(message: Message, status_msg, text: str, state: FSMContext) -> None:
    # Import here to avoid circular import at module load
    from bot.handlers.journal import handle_show_kbju, handle_start_day, handle_close_day, handle_show_journal

    user_id = message.from_user.id
    today   = date.today().isoformat()
    entries = await database.get_day_entries(user_id, today)

    intent_data = await detect_intent(text, entries)
    intent      = intent_data.get("intent", "unknown")

    if intent == "show_kbju":
        await status_msg.delete()
        await handle_show_kbju(message)

    elif intent == "start_day":
        await status_msg.delete()
        await handle_start_day(message)

    elif intent == "close_day":
        await status_msg.delete()
        await handle_close_day(message, state)

    elif intent == "show_journal":
        await status_msg.delete()
        await handle_show_journal(message)

    elif intent == "delete_all":
        await _handle_delete_all_confirm(message, status_msg, user_id)

    elif intent == "delete":
        await _handle_delete_confirm(message, status_msg, intent_data, user_id)

    elif intent == "edit_weight":
        await _handle_edit_weight_confirm(message, status_msg, intent_data, user_id)

    elif intent == "edit_meal":
        await _handle_edit_meal_confirm(message, status_msg, intent_data, user_id)

    elif intent == "edit_name":
        await _handle_edit_name_confirm(message, status_msg, intent_data, user_id)

    else:
        # add_food or unknown → try nutrition parse
        await _handle_add(message, status_msg, text)


# ── Add food ───────────────────────────────────────────────────────────────────

async def _handle_add(message: Message, status_msg, text: str) -> None:
    try:
        data = await nutrition.parse_food(text)
    except Exception as e:
        await status_msg.edit_text(f"Ошибка при анализе: {e}")
        return

    if not data:
        await status_msg.edit_text(
            f"Не смог распознать продукт из текста:\n«{text}»\n\n"
            "Попробуй точнее, например: «куриная грудка вареная 150г»"
        )
        return

    key = _make_key(data, message.from_user.id)
    _pending[key] = {"type": "add", "data": data, "user_id": message.from_user.id}

    await status_msg.edit_text(
        f"{_format_nutrition_card(data)}\n\nДобавить в дневник?",
        reply_markup=confirm_food_kb(key),
        parse_mode="HTML",
    )


# ── Delete ALL confirm ────────────────────────────────────────────────────────

async def _handle_delete_all_confirm(message: Message, status_msg, user_id: int) -> None:
    today   = date.today().isoformat()
    entries = await database.get_day_entries(user_id, today)
    if not entries:
        await status_msg.edit_text("Дневник за сегодня уже пуст.")
        return
    key = _make_key({"delete_all": today}, user_id)
    _pending[key] = {"type": "delete_all", "date": today, "user_id": user_id}
    diary = format_diary_readable(entries)
    await status_msg.edit_text(
        f"Удалить <b>все {len(entries)} записи</b> за сегодня?\n\n{diary}",
        reply_markup=_action_confirm_kb(key, "da"),
        parse_mode="HTML",
    )


# ── Delete confirm ─────────────────────────────────────────────────────────────

async def _handle_delete_confirm(message: Message, status_msg, intent_data: dict, user_id: int) -> None:
    entry_id = intent_data.get("entry_id")
    entry    = await database.get_entry(entry_id, user_id) if entry_id else None
    if not entry:
        today   = date.today().isoformat()
        entries = await database.get_day_entries(user_id, today)
        diary   = format_diary_readable(entries)
        await status_msg.edit_text(
            f"Не нашёл такую запись. Уточни название.\n\n<b>Сегодня в дневнике:</b>\n{diary}\n\n"
            "Скажи, например: «удали рис» или «убери последнее».",
            parse_mode="HTML",
        )
        return
    key = _make_key(intent_data, user_id)
    _pending[key] = {"type": "delete", "entry_id": entry_id, "user_id": user_id}
    meal = MEAL_ICONS.get(entry["meal_type"], entry["meal_type"])
    await status_msg.edit_text(
        f"Удалить?\n\n<b>{entry['food_name']}</b> {entry['weight_g']:.0f}г — {entry['kcal']:.0f} ккал\nПриём: {meal}",
        reply_markup=_action_confirm_kb(key, "del"),
        parse_mode="HTML",
    )


# ── Edit weight confirm ────────────────────────────────────────────────────────

async def _handle_edit_weight_confirm(message: Message, status_msg, intent_data: dict, user_id: int) -> None:
    entry_id   = intent_data.get("entry_id")
    new_weight = intent_data.get("new_weight_g")
    entry      = await database.get_entry(entry_id, user_id) if entry_id else None
    if not entry or not new_weight:
        today   = date.today().isoformat()
        entries = await database.get_day_entries(user_id, today)
        diary   = format_diary_readable(entries)
        await status_msg.edit_text(
            f"Не понял запись или новый вес.\n\n<b>Сегодня в дневнике:</b>\n{diary}\n\n"
            "Скажи, например: «рис было не 200 а 150 грамм».",
            parse_mode="HTML",
        )
        return
    ratio = float(new_weight) / entry["weight_g"]
    key = _make_key(intent_data, user_id)
    _pending[key] = {"type": "edit_weight", "entry_id": entry_id, "new_weight_g": float(new_weight), "user_id": user_id}
    await status_msg.edit_text(
        f"Изменить вес?\n\n<b>{entry['food_name']}</b>\n"
        f"{entry['weight_g']:.0f}г → <b>{float(new_weight):.0f}г</b>\n"
        f"Калории: {entry['kcal']:.0f} → <b>{entry['kcal']*ratio:.0f} ккал</b>",
        reply_markup=_action_confirm_kb(key, "ew"),
        parse_mode="HTML",
    )


# ── Edit meal confirm ──────────────────────────────────────────────────────────

async def _handle_edit_meal_confirm(message: Message, status_msg, intent_data: dict, user_id: int) -> None:
    entry_id = intent_data.get("entry_id")
    new_meal = intent_data.get("new_meal_type")
    entry    = await database.get_entry(entry_id, user_id) if entry_id else None
    if not entry or not new_meal:
        today   = date.today().isoformat()
        entries = await database.get_day_entries(user_id, today)
        diary   = format_diary_readable(entries)
        await status_msg.edit_text(
            f"Не понял запись или приём пищи.\n\n<b>Сегодня в дневнике:</b>\n{diary}\n\n"
            "Скажи, например: «рис перенеси на завтрак» или «это был перекус».",
            parse_mode="HTML",
        )
        return
    key = _make_key(intent_data, user_id)
    _pending[key] = {"type": "edit_meal", "entry_id": entry_id, "new_meal_type": new_meal, "user_id": user_id}
    await status_msg.edit_text(
        f"Изменить приём?\n\n<b>{entry['food_name']}</b> {entry['weight_g']:.0f}г\n"
        f"{MEAL_ICONS.get(entry['meal_type'], '')} → <b>{MEAL_ICONS.get(new_meal, new_meal)}</b>",
        reply_markup=_action_confirm_kb(key, "em"),
        parse_mode="HTML",
    )


# ── Edit name confirm ──────────────────────────────────────────────────────────

async def _handle_edit_name_confirm(message: Message, status_msg, intent_data: dict, user_id: int) -> None:
    entry_id = intent_data.get("entry_id")
    new_name = intent_data.get("new_name", "").strip()
    entry    = await database.get_entry(entry_id, user_id) if entry_id else None
    if not entry or not new_name:
        today   = date.today().isoformat()
        entries = await database.get_day_entries(user_id, today)
        diary   = format_diary_readable(entries)
        await status_msg.edit_text(
            f"Не понял запись или новое название.\n\n<b>Сегодня в дневнике:</b>\n{diary}\n\n"
            "Скажи, например: «переименуй рис в бурый рис».",
            parse_mode="HTML",
        )
        return
    key = _make_key(intent_data, user_id)
    _pending[key] = {"type": "edit_name", "entry_id": entry_id, "new_name": new_name, "user_id": user_id}
    await status_msg.edit_text(
        f"Переименовать?\n\n<b>{entry['food_name']}</b> → <b>{new_name}</b>",
        reply_markup=_action_confirm_kb(key, "en"),
        parse_mode="HTML",
    )


# ── Confirmation callbacks ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("va_ok:"))
async def cb_voice_action_ok(callback: CallbackQuery) -> None:
    _, action, key = callback.data.split(":", 2)
    pending = _pending.pop(key, None)
    if not pending or pending["user_id"] != callback.from_user.id:
        await callback.answer("Действие устарело.", show_alert=True)
        return

    user_id = callback.from_user.id
    today   = date.today().isoformat()

    if action == "da":
        count = await database.delete_all_entries(user_id, today)
        await callback.answer("Дневник очищен.")
        await callback.message.edit_text(f"Удалено записей: {count}. Дневник за сегодня пуст.")

    elif action == "del":
        await database.delete_entry(pending["entry_id"], user_id)
        await callback.answer("Удалено.")
        totals = await database.get_day_totals(user_id, today)
        goals  = await database.get_user_goals(user_id)
        await callback.message.edit_text("Запись удалена." + _format_totals(totals, goals), parse_mode="HTML")

    elif action == "ew":
        await database.update_entry_weight(pending["entry_id"], user_id, pending["new_weight_g"])
        await callback.answer("Вес обновлён.")
        totals = await database.get_day_totals(user_id, today)
        goals  = await database.get_user_goals(user_id)
        await callback.message.edit_text(
            f"Вес изменён на {pending['new_weight_g']:.0f}г. КБЖУ пересчитан."
            + _format_totals(totals, goals), parse_mode="HTML")

    elif action == "em":
        await database.update_entry_meal_type(pending["entry_id"], user_id, pending["new_meal_type"])
        label = MEAL_ICONS.get(pending["new_meal_type"], pending["new_meal_type"])
        await callback.answer(f"Приём: {label}")
        await callback.message.edit_text(f"Приём пищи изменён на {label}.", parse_mode="HTML")

    elif action == "en":
        await database.update_entry_name(pending["entry_id"], user_id, pending["new_name"])
        await callback.answer("Название обновлено.")
        await callback.message.edit_text(f"Название: «{pending['new_name']}».", parse_mode="HTML")


@router.callback_query(F.data == "va_cancel")
async def cb_voice_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Отменено.")
    await callback.answer()


# ── Add-food confirmation flow ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("confirm:"))
async def cb_confirm_add(callback: CallbackQuery) -> None:
    key     = callback.data.split(":", 1)[1]
    pending = _pending.get(key)
    if not pending or pending["user_id"] != callback.from_user.id:
        await callback.answer("Запись устарела, введи заново.", show_alert=True)
        return
    await callback.message.edit_text(
        _format_nutrition_card(pending["data"]) + "\n\nВыбери приём пищи:",
        reply_markup=meal_type_kb(key),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_food")
async def cb_cancel_add(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.callback_query(F.data.startswith("meal:"))
async def cb_meal_type(callback: CallbackQuery) -> None:
    _, meal_type, key = callback.data.split(":", 2)
    pending = _pending.pop(key, None)
    if not pending or pending["user_id"] != callback.from_user.id:
        await callback.answer("Запись устарела.", show_alert=True)
        return

    data    = pending["data"]
    user_id = callback.from_user.id
    today   = date.today().isoformat()

    await database.add_entry(
        user_id=user_id, entry_date=today, meal_type=meal_type,
        food_name=data["food_name"], weight_g=data["weight_g"],
        kcal=data["total"]["kcal"], protein=data["total"]["protein"],
        fat=data["total"]["fat"], carbs=data["total"]["carbs"],
    )

    totals     = await database.get_day_totals(user_id, today)
    goals      = await database.get_user_goals(user_id)
    meal_label = MEAL_TYPES.get(meal_type, meal_type)

    await callback.message.edit_text(
        f"Добавлено в <b>{meal_label}</b>: {data['food_name']} {data['weight_g']} г"
        + _format_totals(totals, goals),
        parse_mode="HTML",
    )
    await callback.answer("Записано!")


# ── Barcode (photo) handler ────────────────────────────────────────────────────

@router.message(F.photo)
async def handle_photo_barcode(message: Message, bot: Bot, state: FSMContext) -> None:
    await database.ensure_user(message.from_user.id, message.from_user.username)

    # Delete user photo to keep chat clean
    try:
        await message.delete()
    except Exception:
        pass

    status = await message.answer("Читаю штрихкод...")

    photo = message.photo[-1]
    file  = await bot.get_file(photo.file_id)
    buf   = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)

    barcode = barcode_svc.decode_barcode(buf.getvalue())
    if not barcode:
        await status.edit_text(
            "Не удалось распознать штрихкод.\n"
            "Убедись, что код хорошо освещён и не смазан."
        )
        return

    await status.edit_text(f"Штрихкод: <code>{barcode}</code>\nИщу в базе...", parse_mode="HTML")

    nutrition_data = await fatsecret.find_by_barcode(barcode)
    if not nutrition_data:
        await status.edit_text(
            f"Штрихкод <code>{barcode}</code> не найден в базе FatSecret.\n"
            "Попробуй добавить продукт голосом или текстом.",
            parse_mode="HTML",
        )
        return

    await state.set_state(BarcodeForm.weight)
    await state.update_data(nutrition=nutrition_data)
    await status.edit_text(
        f"<b>{nutrition_data['food_name']}</b>\n"
        f"(на 100г: {nutrition_data['kcal']:.0f} ккал, "
        f"Б {nutrition_data['protein']:.1f}г, "
        f"Ж {nutrition_data['fat']:.1f}г, "
        f"У {nutrition_data['carbs']:.1f}г)\n\n"
        "Сколько граммов ты съел? (напиши или скажи голосом)",
        parse_mode="HTML",
    )


def _extract_weight(text: str) -> float | None:
    """Extract first positive number from text (handles '150', '150г', '150.5')."""
    import re
    m = re.search(r'\b(\d+(?:[.,]\d+)?)', text)
    if m:
        val = float(m.group(1).replace(',', '.'))
        return val if val > 0 else None
    return None


async def _save_barcode_entry(weight_g: float, message: Message, state: FSMContext) -> None:
    fsm_data = await state.get_data()
    await state.clear()

    raw   = fsm_data["nutrition"]
    ratio = weight_g / 100.0
    data  = {
        "food_name": raw["food_name"],
        "weight_g":  weight_g,
        "source":    "fatsecret",
        "total": {
            "kcal":    round(raw["kcal"]    * ratio, 1),
            "protein": round(raw["protein"] * ratio, 1),
            "fat":     round(raw["fat"]     * ratio, 1),
            "carbs":   round(raw["carbs"]   * ratio, 1),
        },
    }
    key = _make_key(data, message.from_user.id)
    _pending[key] = {"type": "add", "data": data, "user_id": message.from_user.id}

    await message.answer(
        f"{_format_nutrition_card(data)}\n\nДобавить в дневник?",
        reply_markup=confirm_food_kb(key),
        parse_mode="HTML",
    )


@router.message(BarcodeForm.weight, F.text)
async def handle_barcode_weight_text(message: Message, state: FSMContext) -> None:
    weight_g = _extract_weight(message.text)
    if not weight_g:
        await message.answer("Не понял. Напиши вес в граммах, например: 150")
        return
    await _save_barcode_entry(weight_g, message, state)


@router.message(BarcodeForm.weight, F.voice)
async def handle_barcode_weight_voice(message: Message, bot: Bot, state: FSMContext) -> None:
    status = await message.answer("Распознаю голос...")

    voice_file = await bot.get_file(message.voice.file_id)
    buf = io.BytesIO()
    await bot.download_file(voice_file.file_path, destination=buf)

    try:
        text = await stt.transcribe(buf.getvalue())
    except Exception as e:
        await status.edit_text(f"Не удалось распознать голос: {e}")
        return

    weight_g = _extract_weight(text)
    if not weight_g:
        await status.edit_text(
            f"Слышу: «{text}»\n\nНе нашёл число граммов. Скажи цифру, например: «150»"
        )
        return

    await status.delete()
    await _save_barcode_entry(weight_g, message, state)
