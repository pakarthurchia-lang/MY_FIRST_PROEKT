"""
FSM-аудит ПВЗ по локации.
"""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from config import OWNER_CHAT_ID
from db.database import get_all_locations, get_location_with_pvzs
from bot.handlers.menu import main_menu

router = Router()


class AuditState(StatesGroup):
    entering_rent = State()
    entering_salary = State()
    entering_utilities = State()
    entering_turnover = State()


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="audit:cancel")]
    ])


def _parse_amount(text: str) -> float | None:
    try:
        return float(text.strip().replace(" ", "").replace(",", ".").replace("₽", ""))
    except (ValueError, TypeError):
        return None


# ── Точка входа: menu:analytics ────────────────────────────────────────────

@router.callback_query(F.data == "menu:analytics")
async def cb_analytics_locations(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    locations = await get_all_locations()
    if not locations:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Настроить локации", callback_data="loc:list")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:back")],
        ])
        await call.message.answer(
            "🤖 <b>ИИ-аналитика</b>\n\n"
            "Сначала настрой локации ПВЗ.",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    buttons = [
        [InlineKeyboardButton(text=f"📍 {loc['name']}", callback_data=f"audit:{loc['id']}")]
        for loc in locations
    ]
    buttons.append([InlineKeyboardButton(text="⚙️ Локации", callback_data="loc:list")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:back")])

    await call.message.answer(
        "🤖 <b>ИИ-аналитика</b>\n\nВыбери локацию для аудита:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ── Выбор локации → Шаг 1/4 ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("audit:") & ~F.data.in_({"audit:cancel"}))
async def cb_audit_pick_location(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return

    parts = call.data.split(":")
    if len(parts) < 2:
        return
    try:
        location_id = int(parts[1])
    except ValueError:
        return

    await call.answer()
    await state.update_data(location_id=location_id)
    await state.set_state(AuditState.entering_rent)

    await call.message.answer(
        "📊 <b>Аудит ПВЗ</b> — Шаг 1/4\n\n"
        "💳 Введи <b>аренду</b> за месяц (руб.):",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )


# ── Шаг 1: аренда ──────────────────────────────────────────────────────────

@router.message(AuditState.entering_rent)
async def fsm_entering_rent(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "❌ Не могу распознать сумму. Введи число, например: <code>50000</code>",
            parse_mode="HTML",
            reply_markup=_cancel_keyboard(),
        )
        return
    await state.update_data(rent=amount)
    await state.set_state(AuditState.entering_salary)
    await message.answer(
        "📊 <b>Аудит ПВЗ</b> — Шаг 2/4\n\n"
        "👷 Введи <b>ФОТ</b> (зарплаты сотрудников) за месяц (руб.):",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )


# ── Шаг 2: ФОТ ─────────────────────────────────────────────────────────────

@router.message(AuditState.entering_salary)
async def fsm_entering_salary(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "❌ Не могу распознать сумму. Введи число, например: <code>80000</code>",
            parse_mode="HTML",
            reply_markup=_cancel_keyboard(),
        )
        return
    await state.update_data(salary=amount)
    await state.set_state(AuditState.entering_utilities)
    await message.answer(
        "📊 <b>Аудит ПВЗ</b> — Шаг 3/4\n\n"
        "💡 Введи <b>коммуналку</b> за месяц (руб.):",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )


# ── Шаг 3: коммуналка ──────────────────────────────────────────────────────

@router.message(AuditState.entering_utilities)
async def fsm_entering_utilities(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "❌ Не могу распознать сумму. Введи число, например: <code>10000</code>",
            parse_mode="HTML",
            reply_markup=_cancel_keyboard(),
        )
        return
    await state.update_data(utilities=amount)
    await state.set_state(AuditState.entering_turnover)
    await message.answer(
        "📊 <b>Аудит ПВЗ</b> — Шаг 4/4\n\n"
        "💼 Введи <b>товарооборот</b> за месяц (руб.):",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )


# ── Шаг 4: товарооборот → запуск аудита ────────────────────────────────────

@router.message(AuditState.entering_turnover)
async def fsm_entering_turnover(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "❌ Не могу распознать сумму. Введи число, например: <code>1500000</code>",
            parse_mode="HTML",
            reply_markup=_cancel_keyboard(),
        )
        return

    data = await state.get_data()
    await state.clear()

    location_id = data["location_id"]
    expenses = {
        "rent": data["rent"],
        "salary": data["salary"],
        "utilities": data["utilities"],
        "turnover": amount,
    }

    location = await get_location_with_pvzs(location_id)
    if not location:
        await message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    await message.answer("⏳ Собираю данные и готовлю аудит...")

    try:
        from ozon.ai_audit import get_pvz_audit
        audit_text = await get_pvz_audit(location, expenses)
    except Exception as e:
        await message.answer(f"❌ Ошибка при генерации аудита: {e}", reply_markup=main_menu())
        return

    await message.answer(
        f"🤖 <b>Аудит: {location['name']}</b>\n\n{audit_text}",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ── Отмена ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "audit:cancel")
async def cb_audit_cancel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await state.clear()
    await call.message.answer("Отменено.", reply_markup=main_menu())
