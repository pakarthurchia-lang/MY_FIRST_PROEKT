from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from typing import Optional
from config import OWNER_CHAT_ID
from db.database import get_all_locations, get_location_with_pvzs
from bot.handlers.menu import main_menu

router = Router()

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

EXPENSE_STEPS = [
    ("rent",      "🏠 Аренда",         "50000"),
    ("salary",    "👷 Зарплата / ФОТ", "80000"),
    ("utilities", "💡 Коммуналка",      "8000"),
    ("internet",  "🌐 Интернет",        "1500"),
    ("cleaning",  "🧹 Уборка",          "5000"),
    ("other",     "📦 Прочие расходы",  "0"),
]


class DiagState(StatesGroup):
    entering_rent      = State()
    entering_salary    = State()
    entering_utilities = State()
    entering_internet  = State()
    entering_cleaning  = State()
    entering_other     = State()


class ProfitState(StatesGroup):
    entering_rent      = State()
    entering_salary    = State()
    entering_utilities = State()
    entering_internet  = State()
    entering_cleaning  = State()
    entering_other     = State()


def _cancel_kb(cb: str) -> InlineKeyboardMarkup:
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
            "Локаций пока нет.\n\n"
            "Создай локацию и прикрепи к ней ПВЗ — "
            "тогда можно будет запустить аналитику.",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    loc_buttons = [
        [InlineKeyboardButton(text=f"📍 {loc['name']}", callback_data=f"diag_loc:{loc['id']}")]
        for loc in locations
    ]

    markup = InlineKeyboardMarkup(inline_keyboard=[
        *loc_buttons,
        [InlineKeyboardButton(text="💰 Среднегодовая прибыль", callback_data="profit:pick")],
        [InlineKeyboardButton(text="📍 Управление локациями",  callback_data="loc:list")],
        [InlineKeyboardButton(text="◀️ Назад",                 callback_data="menu:back")],
    ])
    await call.message.answer(
        "🤖 <b>ИИ-аналитика</b>\n\n"
        "Выбери локацию для диагностики или раздел:",
        parse_mode="HTML",
        reply_markup=markup,
    )


# ════════════════════════════════════════════════════════════
#  ДИАГНОСТИКА
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("diag_loc:"))
async def cb_diag_pick_location(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    try:
        location_id = int(call.data.split(":")[1])
    except (IndexError, ValueError):
        return

    await call.answer()
    location = await get_location_with_pvzs(location_id)
    if not location:
        await call.message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    await state.update_data(location_id=location_id)
    await state.set_state(DiagState.entering_rent)

    _, label, example = EXPENSE_STEPS[0]
    await call.message.answer(
        f"📍 <b>{location['name']}</b>\n\n"
        f"Для диагностики введи ежемесячные расходы.\n\n"
        f"Шаг 1/{len(EXPENSE_STEPS)}\n{label} (руб.):",
        parse_mode="HTML",
        reply_markup=_cancel_kb("diag:cancel"),
    )


async def _diag_step(message: Message, state: FSMContext, step_idx: int,
                     field: str, next_state: Optional[State],
                     next_label: str = "", next_example: str = ""):
    amount = _parse_amount(message.text)
    if amount is None:
        _, label, example = EXPENSE_STEPS[step_idx]
        await message.answer(
            f"❌ Введи число, например: <code>{example}</code>",
            parse_mode="HTML", reply_markup=_cancel_kb("diag:cancel"),
        )
        return
    await state.update_data(**{field: amount})
    if next_state is None:
        await _run_diagnostics(message, state)
        return
    await state.set_state(next_state)
    await message.answer(
        f"Шаг {step_idx + 2}/{len(EXPENSE_STEPS)}\n{next_label} (руб.):",
        parse_mode="HTML", reply_markup=_cancel_kb("diag:cancel"),
    )


@router.message(DiagState.entering_rent)
async def diag_rent(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    _, nl, ne = EXPENSE_STEPS[1]
    await _diag_step(message, state, 0, "rent", DiagState.entering_salary, nl, ne)

@router.message(DiagState.entering_salary)
async def diag_salary(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    _, nl, ne = EXPENSE_STEPS[2]
    await _diag_step(message, state, 1, "salary", DiagState.entering_utilities, nl, ne)

@router.message(DiagState.entering_utilities)
async def diag_utilities(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    _, nl, ne = EXPENSE_STEPS[3]
    await _diag_step(message, state, 2, "utilities", DiagState.entering_internet, nl, ne)

@router.message(DiagState.entering_internet)
async def diag_internet(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    _, nl, ne = EXPENSE_STEPS[4]
    await _diag_step(message, state, 3, "internet", DiagState.entering_cleaning, nl, ne)

@router.message(DiagState.entering_cleaning)
async def diag_cleaning(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    _, nl, ne = EXPENSE_STEPS[5]
    await _diag_step(message, state, 4, "cleaning", DiagState.entering_other, nl, ne)

@router.message(DiagState.entering_other)
async def diag_other(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    await _diag_step(message, state, 5, "other", None)


async def _run_diagnostics(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    location = await get_location_with_pvzs(data["location_id"])
    if not location:
        await message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    expenses = {field: data.get(field, 0.0) for field, _, _ in EXPENSE_STEPS}
    total_exp = sum(expenses.values())
    exp_lines = "\n".join(
        f"  {label}: {expenses[field]:,.0f} руб."
        for field, label, _ in EXPENSE_STEPS if expenses.get(field, 0) > 0
    )
    await message.answer(
        f"⏳ Анализирую <b>{location['name']}</b>...\n\n"
        f"📊 Расходы ({total_exp:,.0f} руб./мес.):\n{exp_lines}\n\n"
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


@router.callback_query(F.data == "diag:cancel")
async def cb_diag_cancel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID: return
    await call.answer()
    await state.clear()
    await call.message.answer("Отменено.", reply_markup=main_menu())


# ════════════════════════════════════════════════════════════
#  СРЕДНЕГОДОВАЯ ПРИБЫЛЬ
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data == "profit:pick")
async def cb_profit_pick(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    locations = await get_all_locations()
    buttons = [
        [InlineKeyboardButton(text=f"📍 {loc['name']}", callback_data=f"profit_loc:{loc['id']}")]
        for loc in locations
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:analytics")])
    await call.message.answer(
        "💰 <b>Среднегодовая прибыль</b>\n\nВыбери локацию:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("profit_loc:"))
async def cb_profit_pick_location(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    try:
        location_id = int(call.data.split(":")[1])
    except (IndexError, ValueError):
        return

    await call.answer()
    location = await get_location_with_pvzs(location_id)
    if not location:
        await call.message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    await state.update_data(location_id=location_id)
    await state.set_state(ProfitState.entering_rent)

    _, label, example = EXPENSE_STEPS[0]
    await call.message.answer(
        f"📍 <b>{location['name']}</b>\n\n"
        f"Введи ежемесячные расходы для расчёта чистой прибыли.\n\n"
        f"Шаг 1/{len(EXPENSE_STEPS)}\n{label} (руб.):",
        parse_mode="HTML",
        reply_markup=_cancel_kb("profit:cancel"),
    )


async def _profit_step(message: Message, state: FSMContext, step_idx: int,
                       field: str, next_state: Optional[State],
                       next_label: str = "", next_example: str = ""):
    amount = _parse_amount(message.text)
    if amount is None:
        _, label, example = EXPENSE_STEPS[step_idx]
        await message.answer(
            f"❌ Введи число, например: <code>{example}</code>",
            parse_mode="HTML", reply_markup=_cancel_kb("profit:cancel"),
        )
        return
    await state.update_data(**{field: amount})
    if next_state is None:
        await _run_profit(message, state)
        return
    await state.set_state(next_state)
    await message.answer(
        f"Шаг {step_idx + 2}/{len(EXPENSE_STEPS)}\n{next_label} (руб.):",
        parse_mode="HTML", reply_markup=_cancel_kb("profit:cancel"),
    )


@router.message(ProfitState.entering_rent)
async def profit_rent(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    _, nl, ne = EXPENSE_STEPS[1]
    await _profit_step(message, state, 0, "rent", ProfitState.entering_salary, nl, ne)

@router.message(ProfitState.entering_salary)
async def profit_salary(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    _, nl, ne = EXPENSE_STEPS[2]
    await _profit_step(message, state, 1, "salary", ProfitState.entering_utilities, nl, ne)

@router.message(ProfitState.entering_utilities)
async def profit_utilities(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    _, nl, ne = EXPENSE_STEPS[3]
    await _profit_step(message, state, 2, "utilities", ProfitState.entering_internet, nl, ne)

@router.message(ProfitState.entering_internet)
async def profit_internet(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    _, nl, ne = EXPENSE_STEPS[4]
    await _profit_step(message, state, 3, "internet", ProfitState.entering_cleaning, nl, ne)

@router.message(ProfitState.entering_cleaning)
async def profit_cleaning(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    _, nl, ne = EXPENSE_STEPS[5]
    await _profit_step(message, state, 4, "cleaning", ProfitState.entering_other, nl, ne)

@router.message(ProfitState.entering_other)
async def profit_other(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID: return
    await _profit_step(message, state, 5, "other", None)


async def _run_profit(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    location = await get_location_with_pvzs(data["location_id"])
    if not location:
        await message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    expenses = {field: data.get(field, 0.0) for field, _, _ in EXPENSE_STEPS}
    monthly_expenses = sum(expenses.values())

    await message.answer(
        f"⏳ Собираю данные о выручке <b>{location['name']}</b> за последний год...",
        parse_mode="HTML",
    )

    # Собираем выручку по всем платформам за последние 12 месяцев
    monthly_revenue: dict = {}  # (month, year) -> float

    ozon_pvzs = [p for p in location["pvzs"] if p["platform"] == "ozon"]
    wb_pvzs   = [p for p in location["pvzs"] if p["platform"] == "wb"]
    ym_pvzs   = [p for p in location["pvzs"] if p["platform"] == "ym"]

    # Ozon
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

    # WB
    if wb_pvzs:
        try:
            from wildberries.http_client import get_pickpoint_id
            from wildberries.api import fetch_all_payments, aggregate_by_month
            pid = get_pickpoint_id()
            if pid:
                payments = await fetch_all_payments(pid)
                by_month = aggregate_by_month(payments)
                for (m, y), mdata in by_month.items():
                    key = (m, y)
                    monthly_revenue[key] = monthly_revenue.get(key, 0) + mdata["net"]
        except Exception:
            pass

    # ЯМ
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

    # Сортируем по дате (свежие первые), берём до 12 месяцев
    sorted_months = sorted(monthly_revenue.keys(), key=lambda k: (k[1], k[0]), reverse=True)[:12]
    n_months = len(sorted_months)

    # Строим таблицу по месяцам
    rows = []
    total_revenue = 0.0
    total_profit  = 0.0
    for (m, y) in sorted_months:
        rev    = monthly_revenue[(m, y)]
        profit = rev - monthly_expenses
        total_revenue += rev
        total_profit  += profit
        rows.append(
            f"  {MONTHS_RU[m][:3]} {y}: выручка {rev:,.0f} — расходы {monthly_expenses:,.0f} = "
            f"<b>{'%+,.0f' % profit}</b> руб."
        )

    avg_monthly = total_profit / n_months

    exp_lines = "  " + ", ".join(
        f"{label}: {expenses[field]:,.0f}"
        for field, label, _ in EXPENSE_STEPS if expenses.get(field, 0) > 0
    )

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К аналитике", callback_data="menu:analytics")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:back")],
    ])

    text = (
        f"💰 <b>Прибыль: {location['name']}</b>\n\n"
        f"📊 Ежемесячные расходы: <b>{monthly_expenses:,.0f} руб.</b>\n"
        f"{exp_lines}\n\n"
        f"📅 По месяцам ({n_months} мес.):\n"
        + "\n".join(rows) +
        f"\n\n"
        f"📈 <b>Прибыль за год: {total_profit:,.0f} руб.</b>\n"
        f"📊 <b>Среднемесячная прибыль: {avg_monthly:,.0f} руб.</b>"
    )

    await message.answer(text, parse_mode="HTML", reply_markup=back_kb)


@router.callback_query(F.data == "profit:cancel")
async def cb_profit_cancel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID: return
    await call.answer()
    await state.clear()
    await call.message.answer("Отменено.", reply_markup=main_menu())
