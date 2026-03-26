"""
Обработчик раздела «Прибыль — Яндекс Маркет».

Поток:
  1. Пользователь нажимает «📦 ЯМ {месяц}»
  2. Бот сам скачивает XLSX детализации из API ЯМ
  3. Парсит и показывает прибыль по каждому ПВЗ
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery
from config import OWNER_CHAT_ID, get_tax_rate
from yandex.xlsx_parser import parse_ym_xlsx, parse_ym_fines
from yandex.reports import download_report_xlsx
from bot.handlers.menu import main_menu

router = Router()

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


@router.callback_query(F.data.startswith("ym_profit:"))
async def cb_ym_profit(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    _, month_str, year_str = call.data.split(":")
    month, year = int(month_str), int(year_str)

    await call.message.answer(
        f"⏳ Загружаю отчёт Яндекс Маркет за {MONTHS_RU[month]} {year}..."
    )

    try:
        file_bytes = await download_report_xlsx(month, year)
    except RuntimeError as e:
        await call.message.answer(
            f"⚠️ {e}\n\n"
            f"Как получить Session_id:\n"
            f"1. Залогинься в Safari на hubs.market.yandex.ru\n"
            f"2. Запусти: <code>python yandex/setup_token.py</code>",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return
    except Exception as e:
        await call.message.answer(f"❌ Ошибка при скачивании отчёта: {e}", reply_markup=main_menu())
        return

    try:
        pvz_totals = parse_ym_xlsx(file_bytes)
    except Exception as e:
        await call.message.answer(f"❌ Не удалось разобрать файл: {e}", reply_markup=main_menu())
        return

    if not pvz_totals:
        await call.message.answer("⚠️ Отчёт пустой или формат не распознан.", reply_markup=main_menu())
        return

    # Штрафы (баллы к вычету, 1 балл = 1 руб.), фильтруем по выбранному месяцу
    try:
        fines_data = parse_ym_fines(file_bytes)
    except Exception:
        fines_data = {}

    TAX_RATE = get_tax_rate(year)
    tax_pct = int(TAX_RATE * 100)

    pvz_lines = ""
    total_revenue = 0.0
    total_fines = 0.0
    total_tax = 0.0
    total_profit = 0.0

    all_pvzs = sorted(set(list(pvz_totals.keys()) + list(fines_data.keys())))

    for pvz_name in all_pvzs:
        revenue = round(pvz_totals.get(pvz_name, 0.0), 2)
        fines = round(fines_data.get(pvz_name, {}).get("total", 0.0), 2)
        tax = round(revenue * TAX_RATE, 2)
        profit = round(revenue - tax - fines, 2)
        total_revenue += revenue
        total_fines += fines
        total_tax += tax
        total_profit += profit

        fines_str = ""
        if fines > 0:
            items = fines_data[pvz_name]["items"]
            detail = "\n".join(
                f"      • {it['date']} {it['reason']}: -{it['amount']:,.0f} руб."
                for it in items
            )
            fines_str = f"\n   ⚠️ Штрафы ЯМ: -{fines:,.2f} руб.\n{detail}"

        pvz_lines += (
            f"\n\n🏪 <b>{pvz_name}</b>\n"
            f"   Вознаграждение: {revenue:,.2f} руб.\n"
            f"   Налог {tax_pct}%: -{tax:,.2f} руб."
            f"{fines_str}\n"
            f"   ✅ Прибыль: <b>{profit:,.2f} руб.</b>"
        )

    fines_total_str = f"\n⚠️ Штрафы ЯМ: -{total_fines:,.2f} руб." if total_fines > 0 else ""

    text = (
        f"🟡 <b>Яндекс Маркет — {MONTHS_RU[month]} {year}</b>"
        f"{pvz_lines}\n\n"
        f"{'─' * 28}\n"
        f"💰 Общая выручка: {total_revenue:,.2f} руб.\n"
        f"🏛 Налог {tax_pct}%: -{total_tax:,.2f} руб."
        f"{fines_total_str}\n"
        f"✅ <b>Общая прибыль: {total_profit:,.2f} руб.</b>"
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ К месяцам ЯМ", callback_data="profit_platform:ym:0")],
        [InlineKeyboardButton(text="🏠 Главное меню",  callback_data="menu:back")],
    ])
    await call.message.answer(text, parse_mode="HTML", reply_markup=back_kb)
