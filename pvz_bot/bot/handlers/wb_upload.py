"""
Обработчики раздела «Прибыль — Wildberries».

Данные берутся напрямую из API point-balance.wb.ru (GET запросы).
Авторизация через X-Token, который автоматически читается из Safari.

Безопасность:
- X-Token хранится только в data/wb_token.json (права 600, в .gitignore)
- Токен никогда не отображается пользователю и не логируется
- Все запросы READ-ONLY

Резервный вариант: загрузка XLSX вручную (команда /wb_xlsx).
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import OWNER_CHAT_ID, get_tax_rate
from bot.handlers.menu import main_menu

router = Router()

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


class WbXlsxState(StatesGroup):
    waiting_file = State()
    waiting_month = State()


# ── Показ прибыли WB за месяц (из API) ──────────────────────────────────────

@router.callback_query(F.data.startswith("wb_profit:"))
async def cb_wb_profit(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    _, month_str, year_str = call.data.split(":")
    month, year = int(month_str), int(year_str)

    await call.message.answer(
        f"⏳ Загружаю выплаты WB за {MONTHS_RU[month]} {year}..."
    )

    try:
        from wildberries.api import get_monthly_data
        data = await get_monthly_data(month, year)
    except RuntimeError as e:
        await call.message.answer(
            f"⚠️ {e}",
            parse_mode="HTML",
            reply_markup=_refresh_kb(),
        )
        return
    except Exception as e:
        await call.message.answer(f"❌ Ошибка WB API: {e}", reply_markup=main_menu())
        return

    if not data:
        await call.message.answer(
            f"⚠️ Нет данных WB за {MONTHS_RU[month]} {year}.\n"
            "Возможно выплаты за этот период ещё не сформированы.",
            reply_markup=main_menu(),
        )
        return

    await _send_wb_report(call.message, data, month, year)


async def _send_wb_report(target, data: dict, month: int, year: int):
    TAX_RATE = get_tax_rate(year)
    tax_pct = int(TAX_RATE * 100)

    revenue = data["revenue"]
    fines = data["fines"]
    net = data["net"]
    turnover = data.get("turnover", 0)
    orders = data.get("orders", 0)
    address = data.get("address", "")

    tax = round(revenue * TAX_RATE, 2)
    profit = round(revenue - tax - fines, 2)

    orders_str = f"\n📦 Выдач за месяц: {orders:,} шт." if orders else ""
    turnover_str = f"\n🔄 Товарооборот: {turnover:,.0f} руб." if turnover else ""
    fines_str = f"\n⚠️ Удержания WB: -{fines:,.2f} руб." if fines > 0 else ""
    address_str = f"\n📍 {address}" if address else ""

    text = (
        f"🟣 <b>Wildberries — {MONTHS_RU[month]} {year}</b>"
        f"{address_str}\n"
        f"{'─' * 28}"
        f"{orders_str}"
        f"{turnover_str}\n\n"
        f"💰 Вознаграждение WB: {revenue:,.2f} руб.\n"
        f"🏛 Налог {tax_pct}%: -{tax:,.2f} руб."
        f"{fines_str}\n"
        f"✅ <b>Прибыль: {profit:,.2f} руб.</b>\n\n"
        f"{'─' * 28}\n"
        f"🏦 Итого выплачено WB: <b>{net:,.2f} руб.</b>"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить из WB", callback_data=f"wb_profit:{month}:{year}")],
        [InlineKeyboardButton(text="↩️ К месяцам WB",  callback_data="profit_platform:wb:0")],
        [InlineKeyboardButton(text="🏠 Главное меню",   callback_data="menu:back")],
    ])
    await target.answer(text, parse_mode="HTML", reply_markup=markup)


def _refresh_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Как обновить токен", callback_data="wb:token_help")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:back")],
    ])


# ── Статус токена ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "wb:token_help")
async def cb_wb_token_help(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await call.message.answer(
        "🔑 <b>Обновление WB токена</b>\n\n"
        "Токен живёт <b>24 часа</b> и обновляется автоматически если "
        "pvz-lk.wb.ru открыт в Safari.\n\n"
        "Если автообновление не сработало:\n"
        "1. Открой pvz-lk.wb.ru в <b>Safari</b> и залогинься\n"
        "2. В терминале запусти:\n"
        "<code>cd pvz_bot && python wildberries/setup_token.py</code>\n\n"
        "Или отправь команду /wb_token с новым токеном.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


@router.message(Command("wb_token"))
async def cmd_wb_token(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование: <code>/wb_token eyJ...</code>\n\n"
            "Токен берётся из DevTools → partner-payments → Request Headers → X-Token",
            parse_mode="HTML",
        )
        return

    token = parts[1].strip()
    if not token.startswith("eyJ"):
        await message.answer("❌ Токен должен начинаться с 'eyJ' (JWT формат)")
        return

    try:
        from wildberries.safari_token import _save_token
        _save_token(token)
    except Exception as e:
        await message.answer(f"❌ Ошибка сохранения: {e}")
        return

    # Показываем только время жизни, не сам токен
    from wildberries.http_client import get_token_status
    status = get_token_status()
    # Сбрасываем кэш
    from wildberries import http_client
    http_client._token_cache.clear()

    await message.answer(
        f"✅ WB токен сохранён\n"
        f"⏳ Действует ещё ~{status.get('remaining_hours', '?')}ч\n"
        f"🏪 ID ПВЗ: {status.get('pickpoint_id', '?')}",
    )
    # Удаляем сообщение с токеном из чата для безопасности
    try:
        await message.delete()
    except Exception:
        pass


# ── Резервная загрузка XLSX ──────────────────────────────────────────────────

@router.message(Command("wb_xlsx"))
async def cmd_wb_xlsx(message: Message, state: FSMContext):
    """Резервный вариант: загрузить XLSX отчёт WB вручную."""
    if message.from_user.id != OWNER_CHAT_ID:
        return
    await state.set_state(WbXlsxState.waiting_file)
    await message.answer(
        "📤 <b>Загрузка WB отчёта (резервный режим)</b>\n\n"
        "Отправь XLSX из pvz-lk.wb.ru → Финансы → Отчёт о начислениях\n\n"
        "/cancel для отмены.",
        parse_mode="HTML",
    )


@router.message(WbXlsxState.waiting_file, F.document)
async def fsm_wb_file(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith((".xlsx", ".xls")):
        await message.answer("⚠️ Нужен XLSX файл.")
        return

    await message.answer("⏳ Обрабатываю...")
    try:
        file = await message.bot.get_file(doc.file_id)
        raw = await message.bot.download_file(file.file_path)
        file_bytes = raw.read() if hasattr(raw, "read") else bytes(raw)
    except Exception as e:
        await message.answer(f"❌ Ошибка скачивания: {e}")
        return

    try:
        from wildberries.xlsx_parser import parse_wb_xlsx, extract_period_from_wb_xlsx
        parsed = parse_wb_xlsx(file_bytes)
        period = extract_period_from_wb_xlsx(file_bytes)
    except Exception as e:
        await message.answer(f"❌ Не удалось разобрать файл: {e}")
        return

    if not parsed:
        await message.answer("⚠️ Файл пустой или формат не распознан.")
        return

    if period:
        await _save_xlsx_and_show(message, state, parsed, period["month"], period["year"])
    else:
        await state.update_data(parsed=parsed)
        await state.set_state(WbXlsxState.waiting_month)
        await message.answer(
            "📅 Введи период файла в формате <b>ММ.ГГГГ</b> (например: 03.2026)",
            parse_mode="HTML",
        )


@router.message(WbXlsxState.waiting_month)
async def fsm_wb_month(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    try:
        m, y = message.text.strip().split(".")
        month, year = int(m), int(y)
        assert 1 <= month <= 12 and 2020 <= year <= 2099
    except Exception:
        await message.answer("⚠️ Формат: ММ.ГГГГ, например 03.2026")
        return
    data = await state.get_data()
    await _save_xlsx_and_show(message, state, data.get("parsed", {}), month, year)


async def _save_xlsx_and_show(message, state, parsed, month, year):
    from db.database import upsert_wb_report
    for pvz_name, d in parsed.items():
        await upsert_wb_report(
            pvz_name=pvz_name, month=month, year=year,
            revenue=d["revenue"], fines=d["fines"], orders=d.get("orders", 0),
        )
    await state.clear()
    # Формируем единый словарь для отображения
    total_rev = sum(d["revenue"] for d in parsed.values())
    total_fines = sum(d["fines"] for d in parsed.values())
    total_orders = sum(d.get("orders", 0) for d in parsed.values())
    combined = {
        "revenue": total_rev, "fines": total_fines,
        "net": total_rev - total_fines, "orders": total_orders,
        "address": ", ".join(parsed.keys()),
    }
    await message.answer(f"✅ Сохранено {len(parsed)} ПВЗ за {MONTHS_RU[month]} {year}.")
    await _send_wb_report(message, combined, month, year)
