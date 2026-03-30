"""
FSM-аудит ПВЗ по локации.

Флоу:
  1. Выбор локации
  2. Выбор месяца
  3. Шаг 1/4: аренда
  4. Шаг 2/4: ФОТ
  5. Шаг 3/4: коммуналка
  6. Шаг 4/4: оборот Ozon (ЯМ оборот показывается автоматически из XLSX)
  7. Предупреждение если данные Ozon недоступны → подтверждение
  8. Запуск аудита
"""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from typing import Optional
from datetime import date
from config import OWNER_CHAT_ID
from db.database import get_all_locations, get_location_with_pvzs
from bot.handlers.menu import main_menu

router = Router()

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


class AuditState(StatesGroup):
    choosing_month     = State()
    entering_rent      = State()
    entering_salary    = State()
    entering_utilities = State()
    entering_turnover  = State()
    confirm_run        = State()  # ожидание подтверждения при неполных данных


class ValuationState(StatesGroup):
    choosing_month     = State()
    entering_rent      = State()
    entering_salary    = State()
    entering_utilities = State()
    entering_turnover  = State()


def _cancel_keyboard(cancel_data: str = "audit:cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_data)]
    ])


def _parse_amount(text: str) -> Optional[float]:
    try:
        return float(text.strip().replace(" ", "").replace(",", ".").replace("₽", "").replace("руб", ""))
    except (ValueError, TypeError):
        return None


def _month_keyboard(cancel_data: str = "audit:cancel",
                    month_prefix: str = "audit_month") -> InlineKeyboardMarkup:
    today = date.today()
    buttons = []
    m, y = today.month, today.year
    for _ in range(6):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        buttons.append([InlineKeyboardButton(
            text=f"{MONTHS_RU[m]} {y}",
            callback_data=f"{month_prefix}:{m}:{y}"
        )])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Точка входа: menu:analytics ────────────────────────────────────────────

@router.callback_query(F.data == "menu:analytics")
async def cb_analytics_menu(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await state.clear()

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Диагностика проблем", callback_data="analytics:diagnose")],
        [InlineKeyboardButton(text="💰 Оценка бизнеса",      callback_data="analytics:valuation")],
        [InlineKeyboardButton(text="📊 ИИ-аудит месяца",     callback_data="analytics:audit")],
        [InlineKeyboardButton(text="◀️ Назад",               callback_data="menu:back")],
    ])
    await call.message.answer(
        "🤖 <b>ИИ-аналитика</b>\n\nВыбери режим анализа:",
        parse_mode="HTML",
        reply_markup=markup,
    )


async def _location_picker(call: CallbackQuery, state: FSMContext,
                            callback_prefix: str, title: str):
    """Общий picker локаций с заданным callback_prefix для кнопок."""
    locations = await get_all_locations()
    if not locations:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Настроить локации", callback_data="loc:list")],
            [InlineKeyboardButton(text="◀️ Назад",             callback_data="menu:analytics")],
        ])
        await call.message.answer(
            f"🤖 <b>{title}</b>\n\nСначала настрой локации ПВЗ.",
            parse_mode="HTML", reply_markup=markup,
        )
        return

    buttons = [
        [InlineKeyboardButton(text=f"📍 {loc['name']}",
                               callback_data=f"{callback_prefix}:{loc['id']}")]
        for loc in locations
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:analytics")])
    await call.message.answer(
        f"🤖 <b>{title}</b>\n\nВыбери локацию:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "analytics:audit")
async def cb_analytics_audit(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await _location_picker(call, state, "audit", "ИИ-аудит месяца")


@router.callback_query(F.data == "analytics:diagnose")
async def cb_analytics_diagnose(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await _location_picker(call, state, "diag", "Диагностика проблем")


@router.callback_query(F.data == "analytics:valuation")
async def cb_analytics_valuation(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await _location_picker(call, state, "val", "Оценка бизнеса")


# ── Диагностика: выбор локации → прямой запуск ─────────────────────────────

@router.callback_query(F.data.startswith("diag:"))
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

    await call.message.answer(
        f"⏳ Анализирую проблемы <b>{location['name']}</b>...\n"
        "Собираю данные по претензиям, штрафам и рейтингу.",
        parse_mode="HTML",
    )

    try:
        from ozon.ai_audit import get_pvz_diagnostics
        result = await get_pvz_diagnostics(location)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка при диагностике: {e}", reply_markup=main_menu())
        return

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К аналитике", callback_data="menu:analytics")],
        [InlineKeyboardButton(text="🏠 Главное меню",  callback_data="menu:back")],
    ])
    await call.message.answer(
        f"🔍 <b>Диагностика: {location['name']}</b>\n\n{result}",
        parse_mode="HTML",
        reply_markup=back_kb,
    )


# ── Оценка бизнеса: выбор локации → выбор месяца ───────────────────────────

@router.callback_query(F.data.startswith("val:") & ~F.data.in_({"val:cancel"}))
async def cb_val_pick_location(call: CallbackQuery, state: FSMContext):
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
    await state.set_state(ValuationState.choosing_month)

    await call.message.answer(
        f"📍 <b>{location['name']}</b>\n\nВыбери базовый месяц для расчёта:",
        parse_mode="HTML",
        reply_markup=_month_keyboard(cancel_data="val:cancel", month_prefix="val_month"),
    )


# ── Валюация: выбор месяца ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("val_month:"), ValuationState.choosing_month)
async def cb_val_month(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    _, m_str, y_str = call.data.split(":")
    await call.answer()
    await state.update_data(month=int(m_str), year=int(y_str))
    await state.set_state(ValuationState.entering_rent)

    await call.message.answer(
        f"💰 <b>Оценка бизнеса</b> — {MONTHS_RU[int(m_str)]} {y_str}\n\n"
        "Шаг 1/4\n"
        "💳 Введи <b>аренду</b> за месяц (руб.):",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(cancel_data="val:cancel"),
    )


@router.message(ValuationState.entering_rent)
async def val_entering_rent(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Введи число, например: <code>50000</code>",
                             parse_mode="HTML", reply_markup=_cancel_keyboard("val:cancel"))
        return
    await state.update_data(rent=amount)
    await state.set_state(ValuationState.entering_salary)
    await message.answer("Шаг 2/4\n👷 Введи <b>ФОТ</b> за месяц (руб.):",
                         parse_mode="HTML", reply_markup=_cancel_keyboard("val:cancel"))


@router.message(ValuationState.entering_salary)
async def val_entering_salary(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Введи число, например: <code>80000</code>",
                             parse_mode="HTML", reply_markup=_cancel_keyboard("val:cancel"))
        return
    await state.update_data(salary=amount)
    await state.set_state(ValuationState.entering_utilities)
    await message.answer("Шаг 3/4\n💡 Введи <b>коммуналку</b> за месяц (руб.):",
                         parse_mode="HTML", reply_markup=_cancel_keyboard("val:cancel"))


@router.message(ValuationState.entering_utilities)
async def val_entering_utilities(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Введи число, например: <code>10000</code>",
                             parse_mode="HTML", reply_markup=_cancel_keyboard("val:cancel"))
        return
    await state.update_data(utilities=amount)
    await state.set_state(ValuationState.entering_turnover)
    await message.answer("Шаг 4/4\n💼 Введи <b>общий товарооборот</b> за месяц (руб.):",
                         parse_mode="HTML", reply_markup=_cancel_keyboard("val:cancel"))


@router.message(ValuationState.entering_turnover)
async def val_entering_turnover(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Введи число, например: <code>1500000</code>",
                             parse_mode="HTML", reply_markup=_cancel_keyboard("val:cancel"))
        return

    data = await state.get_data()
    expenses = {
        "rent": data["rent"], "salary": data["salary"],
        "utilities": data["utilities"], "turnover": amount,
    }
    await state.clear()

    location = await get_location_with_pvzs(data["location_id"])
    if not location:
        await message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    month, year = data["month"], data["year"]
    await message.answer(
        f"⏳ Рассчитываю стоимость бизнеса за {MONTHS_RU[month]} {year}...\n"
        "Собираю данные за все доступные месяцы."
    )

    try:
        from ozon.ai_audit import get_pvz_valuation
        result = await get_pvz_valuation(location, expenses, month, year)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}", reply_markup=main_menu())
        return

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К аналитике", callback_data="menu:analytics")],
        [InlineKeyboardButton(text="🏠 Главное меню",  callback_data="menu:back")],
    ])
    await message.answer(
        f"💰 <b>Оценка бизнеса: {location['name']}</b>\n\n{result}",
        parse_mode="HTML",
        reply_markup=back_kb,
    )


@router.callback_query(F.data == "val:cancel")
async def cb_val_cancel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await state.clear()
    await call.message.answer("Отменено.", reply_markup=main_menu())


# ── Аудит: выбор локации → выбор месяца ────────────────────────────────────

@router.callback_query(F.data.startswith("audit:") & ~F.data.in_({"audit:cancel", "audit:run"}))
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
    location = await get_location_with_pvzs(location_id)
    if not location:
        await call.message.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    await state.update_data(location_id=location_id)
    await state.set_state(AuditState.choosing_month)

    await call.message.answer(
        f"📍 <b>{location['name']}</b>\n\nВыбери месяц для аудита:",
        parse_mode="HTML",
        reply_markup=_month_keyboard(),
    )


# ── Выбор месяца → Шаг 1/4 ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("audit_month:"), AuditState.choosing_month)
async def cb_audit_month(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    _, m_str, y_str = call.data.split(":")
    await call.answer()
    await state.update_data(month=int(m_str), year=int(y_str))
    await state.set_state(AuditState.entering_rent)

    await call.message.answer(
        f"📊 <b>Аудит ПВЗ</b> — {MONTHS_RU[int(m_str)]} {y_str}\n\n"
        f"Шаг 1/4\n"
        f"💳 Введи <b>аренду</b> за месяц (руб.):",
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
            "❌ Введи число, например: <code>50000</code>",
            parse_mode="HTML", reply_markup=_cancel_keyboard(),
        )
        return
    await state.update_data(rent=amount)
    await state.set_state(AuditState.entering_salary)
    await message.answer(
        "Шаг 2/4\n"
        "👷 Введи <b>ФОТ</b> (зарплаты сотрудников) за месяц (руб.):",
        parse_mode="HTML", reply_markup=_cancel_keyboard(),
    )


# ── Шаг 2: ФОТ ─────────────────────────────────────────────────────────────

@router.message(AuditState.entering_salary)
async def fsm_entering_salary(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "❌ Введи число, например: <code>80000</code>",
            parse_mode="HTML", reply_markup=_cancel_keyboard(),
        )
        return
    await state.update_data(salary=amount)
    await state.set_state(AuditState.entering_utilities)
    await message.answer(
        "Шаг 3/4\n"
        "💡 Введи <b>коммуналку</b> за месяц (руб.):",
        parse_mode="HTML", reply_markup=_cancel_keyboard(),
    )


# ── Шаг 3: коммуналка → Шаг 4 с данными ЯМ ────────────────────────────────

@router.message(AuditState.entering_utilities)
async def fsm_entering_utilities(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "❌ Введи число, например: <code>10000</code>",
            parse_mode="HTML", reply_markup=_cancel_keyboard(),
        )
        return
    await state.update_data(utilities=amount)
    await state.set_state(AuditState.entering_turnover)

    data = await state.get_data()
    month, year = data["month"], data["year"]
    location_id = data["location_id"]
    location = await get_location_with_pvzs(location_id)

    # Пробуем подтянуть оборот ЯМ за выбранный месяц
    ym_pvzs = [p for p in location["pvzs"] if p["platform"] == "ym"] if location else []
    ym_turnover_str = ""
    if ym_pvzs:
        try:
            from yandex.reports import download_report_xlsx
            from yandex.xlsx_parser import parse_ym_turnover
            xlsx = await download_report_xlsx(month, year)
            turnover_data = parse_ym_turnover(xlsx)
            total_ym = sum(turnover_data.get(p["pvz_name"], 0.0) for p in ym_pvzs)
            if total_ym > 0:
                ym_turnover_str = f"\n\n📦 <b>ЯМ оборот за {MONTHS_RU[month]} {year}:</b> {total_ym:,.0f} руб. (из отчёта)"
                await state.update_data(ym_turnover=total_ym)
        except Exception:
            ym_turnover_str = "\n\n⚠️ ЯМ оборот недоступен (нет отчёта)"

    ozon_pvzs = [p for p in location["pvzs"] if p["platform"] == "ozon"] if location else []
    ozon_label = "общий товарооборот" if not ozon_pvzs else "товарооборот Ozon"

    await message.answer(
        f"Шаг 4/4{ym_turnover_str}\n\n"
        f"💼 Введи <b>{ozon_label}</b> за {MONTHS_RU[month]} {year} (руб.):",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )


# ── Шаг 4: оборот → проверка данных → (предупреждение или) запуск ──────────

@router.message(AuditState.entering_turnover)
async def fsm_entering_turnover(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "❌ Введи число, например: <code>1500000</code>",
            parse_mode="HTML", reply_markup=_cancel_keyboard(),
        )
        return

    data = await state.get_data()
    expenses = {
        "rent":     data["rent"],
        "salary":   data["salary"],
        "utilities": data["utilities"],
        "turnover": amount,
        "ym_turnover": data.get("ym_turnover", 0.0),
    }
    await state.update_data(expenses=expenses)

    # Проверяем доступность Ozon данных если есть Ozon ПВЗ
    location = await get_location_with_pvzs(data["location_id"])
    ozon_pvzs = [p for p in location["pvzs"] if p["platform"] == "ozon"] if location else []

    if ozon_pvzs:
        try:
            from ozon.http_client import get_access_token
            await get_access_token()
            ozon_ok = True
        except Exception:
            ozon_ok = False
    else:
        ozon_ok = True  # нет Ozon ПВЗ — не нужно проверять

    if not ozon_ok:
        await state.set_state(AuditState.confirm_run)
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Запустить без Ozon", callback_data="audit:run")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="audit:cancel")],
        ])
        await message.answer(
            "⚠️ <b>Данные Ozon недоступны</b> — токен истёк.\n\n"
            "Аудит будет неполным: ИИ не увидит вознаграждение и трафик Ozon "
            "и может сделать ошибочные выводы.\n\n"
            "Рекомендуется сначала обновить токен Ozon.",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    await _run_audit(message, state)


# ── Подтверждение запуска без полных данных ─────────────────────────────────

@router.callback_query(F.data == "audit:run", AuditState.confirm_run)
async def cb_audit_run_confirmed(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await _run_audit(call.message, state)


# ── Общая функция запуска аудита ────────────────────────────────────────────

async def _run_audit(target, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    location = await get_location_with_pvzs(data["location_id"])
    if not location:
        await target.answer("❌ Локация не найдена.", reply_markup=main_menu())
        return

    expenses = data["expenses"]
    month, year = data["month"], data["year"]

    await target.answer(
        f"⏳ Собираю данные и готовлю аудит за {MONTHS_RU[month]} {year}..."
    )

    try:
        from ozon.ai_audit import get_pvz_audit
        audit_text = await get_pvz_audit(location, expenses, month, year)
    except Exception as e:
        await target.answer(f"❌ Ошибка при генерации аудита: {e}", reply_markup=main_menu())
        return

    await target.answer(
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
