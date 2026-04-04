from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from typing import Optional
from config import OWNER_CHAT_ID
from db.database import (
    get_all_locations, get_location_with_pvzs,
    get_location_expenses, save_location_expenses,
)
from bot.handlers.menu import main_menu

router = Router()

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

# field, label, пример
EXPENSE_STEPS = [
    ("rent",      "🏠 Аренда",         "50000"),
    ("salary",    "👷 Зарплата / ФОТ", "80000"),
    ("utilities", "💡 Коммуналка",      "8000"),
    ("internet",  "🌐 Интернет",        "1500"),
    ("cleaning",  "🧹 Уборка",          "5000"),
    ("other",     "📦 Прочие расходы",  "0"),
]

# flow: "diag" | "profit" | "save"
class ExpensesState(StatesGroup):
    entering_rent      = State()
    entering_salary    = State()
    entering_utilities = State()
    entering_internet  = State()
    entering_cleaning  = State()
    entering_other     = State()


def _cancel_kb(cb: str = "exp:cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=cb)]
    ])


def _parse_amount(text: str) -> Optional[float]:
    try:
        return float(
            (text or "").strip()
            .replace(" ", "").replace(",", ".").replace("₽", "").replace("руб", "")
        )
    except (ValueError, TypeError):
        return None


def _expenses_text(expenses: dict) -> str:
    total = sum(expenses.get(f, 0) for f, _, _ in EXPENSE_STEPS)
    lines = "\n".join(
        f"  {label}: <b>{expenses.get(field, 0):,.0f} руб.</b>"
        for field, label, _ in EXPENSE_STEPS
    )
    return f"{lines}\n\n  <b>Итого: {total:,.0f} руб./мес.</b>"


# ── Точка входа: menu:analytics ────────────────────────────────────────────

@router.callback_query(F.data == "menu:analytics")
async def cb_analytics_menu(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await state.clear()

    locations = await get_all_locations()
    if not locations:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📍 Добавить локации", callback_data="loc:list")],
            [InlineKeyboardButton(text="◀️ Назад",            callback_data="menu:back")],
        ])
        await call.message.answer(
            "🤖 <b>ИИ-аналитика</b>\n\n"
            "Локаций пока нет.\n\nСоздай локацию и прикрепи к ней ПВЗ.",
            parse_mode="HTML", reply_markup=markup,
        )
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Анализ локаций",        callback_data="diag:pick")],
        [InlineKeyboardButton(text="💰 Среднегодовая прибыль", callback_data="ann_profit:pick")],
        [InlineKeyboardButton(text="💳 Расходы",               callback_data="expenses:pick")],
        [InlineKeyboardButton(text="📍 Управление локациями",  callback_data="loc:list")],
        [InlineKeyboardButton(text="◀️ Назад",                 callback_data="menu:back")],
    ])
    await call.message.answer(
        "🤖 <b>ИИ-аналитика</b>\n\nВыбери раздел:",
        parse_mode="HTML", reply_markup=markup,
    )


# ── Выбор локации (общий picker) ───────────────────────────────────────────

async def _location_picker(call: CallbackQuery, title: str, cb_prefix: str):
    locations = await get_all_locations()
    buttons = [
        [InlineKeyboardButton(text=f"📍 {loc['name']}", callback_data=f"{cb_prefix}:{loc['id']}")]
        for loc in locations
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:analytics")])
    await call.message.answer(
        f"{title}\n\nВыбери локацию:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "diag:pick")
async def cb_diag_pick(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID: return
    await call.answer()
    await _location_picker(call, "🔍 <b>Анализ локаций</b>", "diag_loc")


@router.callback_query(F.data == "ann_profit:pick")
async def cb_profit_pick(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID: return
    await call.answer()
    await _location_picker(call, "💰 <b>Среднегодовая прибыль</b>", "ann_profit_loc")


@router.callback_query(F.data == "expenses:pick")
async def cb_expenses_pick(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID: return
    await call.answer()
    await _location_picker(call, "💳 <b>Расходы</b>", "expenses_loc")


# ── Просмотр/редактирование расходов ──────────────────────────────────────

@router.callback_query(F.data.startswith("expenses_loc:"))
async def cb_expenses_show(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID: return
    try:
        location_id = int(call.data.split(":")[1])
    except (IndexError, ValueError):
        return
    await call.answer()

    location = await get_location_with_pvzs(location_id)
    if not location:
        await call.message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    saved = await get_location_expenses(location_id)
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"exp_edit:{location_id}")],
        [InlineKeyboardButton(text="◀️ Назад",          callback_data="expenses:pick")],
    ])

    if saved:
        from datetime import datetime
        updated = saved.get("updated_at", "")[:10]
        text = (
            f"💳 <b>Расходы: {location['name']}</b>\n"
            f"<i>Обновлено: {updated}</i>\n\n"
            + _expenses_text(saved)
        )
    else:
        text = (
            f"💳 <b>Расходы: {location['name']}</b>\n\n"
            "Расходы ещё не заданы."
        )

    await call.message.answer(text, parse_mode="HTML", reply_markup=markup)


# ── Запуск FSM редактирования расходов ────────────────────────────────────


@router.callback_query(F.data.startswith("exp_edit:"))
async def cb_exp_edit(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID: return
    try:
        location_id = int(call.data.split(":")[1])
    except (IndexError, ValueError):
        return
    await call.answer()

    location = await get_location_with_pvzs(location_id)
    if not location:
        await call.message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    saved = await get_location_expenses(location_id) or {}
    await state.update_data(location_id=location_id, **{
        f: saved.get(f, 0.0) for f, _, _ in EXPENSE_STEPS
    })
    await state.set_state(ExpensesState.entering_rent)

    _, label, _ = EXPENSE_STEPS[0]
    current = saved.get("rent", 0)
    hint = f" <i>(сейчас: {current:,.0f})</i>" if current else ""
    await call.message.answer(
        f"📍 <b>{location['name']}</b>\n\n"
        f"Шаг 1/{len(EXPENSE_STEPS)}\n{label}{hint} (руб.):",
        parse_mode="HTML",
        reply_markup=_cancel_kb(),
    )


@router.callback_query(F.data.startswith("diag_loc:"))
async def cb_diag_loc(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID: return
    try:
        location_id = int(call.data.split(":")[1])
    except (IndexError, ValueError):
        return
    await call.answer()

    expenses = await get_location_expenses(location_id)
    if not expenses:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Заполнить расходы", callback_data=f"exp_edit:{location_id}")],
            [InlineKeyboardButton(text="◀️ Назад",              callback_data="diag:pick")],
        ])
        await call.message.answer(
            "⚠️ Для анализа нужны расходы по этой локации.\n\n"
            "Перейди в раздел <b>💳 Расходы</b> и заполни данные — "
            "после этого анализ будет доступен.",
            parse_mode="HTML", reply_markup=markup,
        )
        return

    await _run_diagnostics(call.message, location_id, expenses)


@router.callback_query(F.data.startswith("ann_profit_loc:"))
async def cb_profit_loc(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID: return
    try:
        location_id = int(call.data.split(":")[-1])
    except (IndexError, ValueError):
        return
    await call.answer()

    expenses = await get_location_expenses(location_id)
    if not expenses:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Заполнить расходы", callback_data=f"exp_edit:{location_id}")],
            [InlineKeyboardButton(text="◀️ Назад",              callback_data="ann_profit:pick")],
        ])
        await call.message.answer(
            "⚠️ Для расчёта прибыли нужны расходы по этой локации.\n\n"
            "Перейди в раздел <b>💳 Расходы</b> и заполни данные — "
            "после этого расчёт будет доступен.",
            parse_mode="HTML", reply_markup=markup,
        )
        return

    await _run_profit(call.message, location_id, expenses)


# ── FSM шаги ──────────────────────────────────────────────────────────────

async def _exp_step(message: Message, state: FSMContext,
                    step_idx: int, field: str,
                    next_state: Optional[State]):
    amount = _parse_amount(message.text)
    if amount is None:
        _, label, example = EXPENSE_STEPS[step_idx]
        await message.answer(
            f"❌ Введи число, например: <code>{example}</code>",
            parse_mode="HTML", reply_markup=_cancel_kb(),
        )
        return

    await state.update_data(**{field: amount})

    if next_state is None:
        await _finish_expenses(message, state)
        return

    await state.set_state(next_state)
    data = await state.get_data()
    _, nl, _ = EXPENSE_STEPS[step_idx + 1]
    current = data.get(EXPENSE_STEPS[step_idx + 1][0], 0.0)
    hint = f" <i>(сейчас: {current:,.0f})</i>" if current else ""
    await message.answer(
        f"Шаг {step_idx + 2}/{len(EXPENSE_STEPS)}\n{nl}{hint} (руб.):",
        parse_mode="HTML", reply_markup=_cancel_kb(),
    )


@router.message(ExpensesState.entering_rent)
async def exp_rent(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    await _exp_step(message, state, 0, "rent", ExpensesState.entering_salary)

@router.message(ExpensesState.entering_salary)
async def exp_salary(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    await _exp_step(message, state, 1, "salary", ExpensesState.entering_utilities)

@router.message(ExpensesState.entering_utilities)
async def exp_utilities(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    await _exp_step(message, state, 2, "utilities", ExpensesState.entering_internet)

@router.message(ExpensesState.entering_internet)
async def exp_internet(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    await _exp_step(message, state, 3, "internet", ExpensesState.entering_cleaning)

@router.message(ExpensesState.entering_cleaning)
async def exp_cleaning(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    await _exp_step(message, state, 4, "cleaning", ExpensesState.entering_other)

@router.message(ExpensesState.entering_other)
async def exp_other(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    await _exp_step(message, state, 5, "other", None)


# ── Завершение FSM — сохранение и диспетч ──────────────────────────────────

async def _finish_expenses(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    location_id = data["location_id"]
    expenses = {field: data.get(field, 0.0) for field, _, _ in EXPENSE_STEPS}

    # Сохраняем расходы в БД
    await save_location_expenses(location_id, expenses)

    location = await get_location_with_pvzs(location_id)
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К расходам",  callback_data=f"expenses_loc:{location_id}")],
        [InlineKeyboardButton(text="◀️ К аналитике", callback_data="menu:analytics")],
    ])
    await message.answer(
        f"✅ <b>Расходы сохранены: {location['name']}</b>\n\n"
        + _expenses_text(expenses),
        parse_mode="HTML", reply_markup=back_kb,
    )


# ── Диагностика ────────────────────────────────────────────────────────────

async def _run_diagnostics(message: Message, location_id: int, expenses: dict):
    location = await get_location_with_pvzs(location_id)
    if not location:
        await message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    monthly_expenses = sum(float(expenses.get(f, 0)) for f, _, _ in EXPENSE_STEPS)
    exp_lines = "\n".join(
        f"  {label}: {float(expenses.get(field, 0)):,.0f} руб."
        for field, label, _ in EXPENSE_STEPS if float(expenses.get(field, 0)) > 0
    )
    await message.answer(
        f"⏳ Анализирую <b>{location['name']}</b>...\n\n"
        f"📊 Расходы ({monthly_expenses:,.0f} руб./мес.):\n{exp_lines}\n\n"
        "Собираю данные по претензиям, штрафам и рейтингу.",
        parse_mode="HTML",
    )

    try:
        from ozon.ai_audit import get_pvz_diagnostics
        result = await get_pvz_diagnostics(location, expenses)
    except Exception as e:
        await message.answer(f"❌ Ошибка при диагностике: {e}", reply_markup=main_menu())
        return

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К аналитике", callback_data="menu:analytics")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:back")],
    ])
    await message.answer(
        f"🔍 <b>Диагностика: {location['name']}</b>\n\n{result}",
        parse_mode="HTML", reply_markup=back_kb,
    )


# ── Среднегодовая прибыль ──────────────────────────────────────────────────

async def _run_profit(message: Message, location_id: int, expenses: dict):
    location = await get_location_with_pvzs(location_id)
    if not location:
        await message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    monthly_expenses = sum(float(expenses.get(f, 0)) for f, _, _ in EXPENSE_STEPS)
    await message.answer(
        f"⏳ Собираю данные о выручке <b>{location['name']}</b> за последний год...",
        parse_mode="HTML",
    )

    monthly_revenue: dict = {}

    ozon_pvzs = [p for p in location["pvzs"] if p["platform"] == "ozon"]
    wb_pvzs   = [p for p in location["pvzs"] if p["platform"] == "wb"]
    ym_pvzs   = [p for p in location["pvzs"] if p["platform"] == "ym"]

    if ozon_pvzs:
        try:
            from ozon.scraper import get_available_reports, get_monthly_stats
            reports = await get_available_reports()
            for r in sorted(reports, key=lambda x: (x["year"], x["month"]), reverse=True)[:12]:
                try:
                    stats = await get_monthly_stats(r["month"], r["year"])
                    rev = sum(stats.get("pvz_revenue", {}).get(p["pvz_name"], 0) for p in ozon_pvzs)
                    if rev > 0:
                        key = (r["month"], r["year"])
                        monthly_revenue[key] = monthly_revenue.get(key, 0) + rev
                except Exception:
                    pass
        except Exception:
            pass

    if wb_pvzs:
        try:
            from wildberries.http_client import get_pickpoint_id
            from wildberries.api import fetch_all_payments, aggregate_by_month
            pid = get_pickpoint_id()
            if pid:
                payments = await fetch_all_payments(pid)
                for (m, y), mdata in aggregate_by_month(payments).items():
                    key = (m, y)
                    monthly_revenue[key] = monthly_revenue.get(key, 0) + mdata["net"]
        except Exception:
            pass

    if ym_pvzs:
        try:
            from yandex.reports import download_report_xlsx, available_months_for_menu
            from yandex.xlsx_parser import parse_ym_xlsx
            for m in available_months_for_menu(12):
                try:
                    xlsx = await download_report_xlsx(m["month"], m["year"])
                    ym_data = parse_ym_xlsx(xlsx)
                    rev = sum(ym_data.get(p["pvz_name"], 0) for p in ym_pvzs)
                    if rev > 0:
                        key = (m["month"], m["year"])
                        monthly_revenue[key] = monthly_revenue.get(key, 0) + rev
                except Exception:
                    pass
        except Exception:
            pass

    if not monthly_revenue:
        await message.answer(
            "❌ Нет данных о выручке за последний год.\n"
            "Проверь авторизацию Ozon / WB / ЯМ.",
            reply_markup=main_menu(),
        )
        return

    sorted_months = sorted(monthly_revenue.keys(), key=lambda k: (k[1], k[0]), reverse=True)[:12]
    n_months = len(sorted_months)

    rows = []
    total_revenue = 0.0
    total_profit  = 0.0
    for (m, y) in sorted_months:
        rev    = monthly_revenue[(m, y)]
        profit = rev - monthly_expenses
        total_revenue += rev
        total_profit  += profit
        sign = "+" if profit >= 0 else ""
        rows.append(
            f"  {MONTHS_RU[m][:3]} {y}: {rev:,.0f} − {monthly_expenses:,.0f} = "
            f"<b>{sign}{profit:,.0f}</b>"
        )

    avg_monthly = total_profit / n_months

    # Формула: перечисляем ПВЗ по платформам
    pvz_parts = []
    for p in location["pvzs"]:
        platform = p["platform"].upper()
        pvz_parts.append(f"{p['pvz_name']} ({platform})")
    formula = " + ".join(pvz_parts) + f" − расходы = прибыль"

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К аналитике", callback_data="menu:analytics")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:back")],
    ])
    await message.answer(
        f"💰 <b>Прибыль: {location['name']}</b>\n"
        f"<i>{formula}</i>\n\n"
        f"📊 Расходы: <b>{monthly_expenses:,.0f} руб./мес.</b>\n\n"
        f"📅 По месяцам ({n_months} мес.):\n" + "\n".join(rows) + "\n\n"
        f"📈 <b>Прибыль за год: {total_profit:,.0f} руб.</b>\n"
        f"📊 <b>Среднемесячная: {avg_monthly:,.0f} руб.</b>",
        parse_mode="HTML", reply_markup=back_kb,
    )


# ── Отмена ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "exp:cancel")
async def cb_exp_cancel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID: return
    await call.answer()
    await state.clear()
    await call.message.answer("Отменено.", reply_markup=main_menu())
