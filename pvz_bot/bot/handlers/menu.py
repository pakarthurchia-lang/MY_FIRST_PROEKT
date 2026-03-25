from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from config import OWNER_CHAT_ID
from ozon.scraper import scrape_claims, get_monthly_stats, get_available_reports, _get_all_stores
from yandex.reports import available_months_for_menu as ym_available_months
from ozon.analytics import get_all_pvz_analytics
from ozon.ai_audit import get_pvz_audit
from bot.handlers.claims import format_claim

router = Router()

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Претензии", callback_data="menu:claims"),
            InlineKeyboardButton(text="💰 Прибыль", callback_data="menu:profit"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="menu:stats"),
            InlineKeyboardButton(text="📝 Оборот", callback_data="menu:turnover"),
        ],
        [
            InlineKeyboardButton(text="🤖 Аналитика", callback_data="menu:analytics"),
        ],
    ])


@router.message(CommandStart())
async def cmd_start(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    await message.answer(
        "🏪 <b>ПВЗ Аналитика</b>\n\nВыбери раздел:",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


# ── Претензии ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:claims")
async def cb_claims(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await call.message.answer("🔄 Получаю актуальные претензии...")

    try:
        claims = await scrape_claims()
    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}")
        return

    if not claims:
        await call.message.answer("✅ Активных претензий нет!", reply_markup=main_menu())
        return

    claims.sort(key=lambda c: c.get("deadline") or "9999")
    total = sum(c.get("amount", 0) for c in claims)
    text = f"📋 <b>Претензии Ozon</b> — {len(claims)} шт.\n"
    text += f"💸 Итого: <b>{total:,.2f} руб.</b>\n\n"
    for claim in claims:
        text += format_claim(claim) + "\n\n"

    await call.message.answer(text, parse_mode="HTML", reply_markup=main_menu())


# ── Прибыль — выбор месяца ─────────────────────────────────────────────────

@router.callback_query(F.data == "menu:profit")
async def cb_profit_months(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await call.message.answer("⏳ Загружаю список отчётов...")

    try:
        reports = await get_available_reports()
    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}")
        return

    if not reports:
        await call.message.answer("⚠️ Нет утверждённых отчётов.")
        return

    # Строим объединённый список месяцев из Ozon-отчётов + последние месяцы ЯМ
    ym_months = {(r["month"], r["year"]): r for r in ym_available_months(6)}
    ozon_keys = {(r["month"], r["year"]) for r in reports}
    # Все уникальные периоды, отсортированные по убыванию
    all_keys = sorted(ozon_keys | set(ym_months.keys()), key=lambda x: (x[1], x[0]), reverse=True)

    buttons = []
    for (m, y) in all_keys:
        ozon_label = next((r["label"] for r in reports if r["month"] == m and r["year"] == y), None)
        ym_label = ym_months.get((m, y), {}).get("label") or f"{MONTHS_RU[m][:3]} {y}"
        row = []
        if ozon_label:
            row.append(InlineKeyboardButton(
                text=f"🟠 Ozon  {ozon_label}",
                callback_data=f"profit:{m}:{y}"
            ))
        row.append(InlineKeyboardButton(
            text=f"📦 ЯМ  {ym_label}",
            callback_data=f"ym_profit:{m}:{y}"
        ))
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:back")])

    await call.message.answer(
        "📅 Выбери период:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ── Прибыль — результат ────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("profit:"))
async def cb_profit_result(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    _, month_str, year_str = call.data.split(":")
    month, year = int(month_str), int(year_str)

    await call.message.answer(
        f"⏳ Считаю прибыль за {MONTHS_RU[month]} {year}..."
    )

    try:
        stats = await get_monthly_stats(month=month, year=year)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}")
        return

    if "error" in stats:
        await call.message.answer(f"⚠️ {stats['error']}", reply_markup=main_menu())
        return

    tax_pct = int(stats["tax_rate"] * 100)
    pvz_revenue = stats.get("pvz_revenue", {})
    fines_by_pvz = stats.get("fines_by_pvz", {})

    pvz_lines = ""
    total_profit = 0.0

    total_tax_shortfall = 0.0

    if pvz_revenue and "_error" not in pvz_revenue:
        for pvz_name, rev in pvz_revenue.items():
            tax = round(rev * stats["tax_rate"], 2)
            fines = round(fines_by_pvz.get(pvz_name, 0), 2)
            profit = round(rev - tax - fines, 2)
            total_profit += profit

            # Банк отложил 12% от суммы БЕЗ штрафов, но налог — с полной суммы PDF
            # Недостача = штрафы × ставка налога
            tax_shortfall = round(fines * stats["tax_rate"], 2)
            total_tax_shortfall += tax_shortfall

            fines_str = f"\n   ⚠️ Штрафы/претензии: -{fines:,.2f} руб." if fines > 0 else ""
            shortfall_str = (
                f"\n   🏦 Докинуть в копилку налога: <b>+{tax_shortfall:,.2f} руб.</b>"
                if tax_shortfall > 0 else ""
            )
            pvz_lines += (
                f"\n\n🏪 <b>{pvz_name}</b>\n"
                f"   Вознаграждение (PDF): {rev:,.2f} руб.\n"
                f"   Налог {tax_pct}%: -{tax:,.2f} руб."
                f"{fines_str}\n"
                f"   ✅ Прибыль: <b>{profit:,.2f} руб.</b>"
                f"{shortfall_str}"
            )
    else:
        # PDF не распарсился — показываем общую сумму
        total_profit = stats["profit"]
        total_tax_shortfall = round(stats["fines_total"] * stats["tax_rate"], 2)

    shortfall_total_str = (
        f"\n💡 Итого докинуть в копилку налога: <b>+{total_tax_shortfall:,.2f} руб.</b>"
        if total_tax_shortfall > 0 else ""
    )

    text = (
        f"💰 <b>Прибыль {MONTHS_RU[month]} {year}</b>\n"
        f"📅 {stats['begin_date']} — {stats['end_date']}\n"
        f"{pvz_lines}\n\n"
        f"{'─' * 28}\n"
        f"💰 Общая выручка (PDF): {stats['revenue']:,.2f} руб.\n"
        f"🏛 Общий налог {tax_pct}%: -{stats['tax']:,.2f} руб.\n"
        f"⚠️ Штрафы/претензии: -{stats['fines_total']:,.2f} руб.\n"
        f"✅ <b>Общая прибыль: {stats['profit']:,.2f} руб.</b>"
        f"{shortfall_total_str}"
    )

    await call.message.answer(text, parse_mode="HTML", reply_markup=main_menu())


# ── Статистика (бывшая Аналитика) ──────────────────────────────────────────

@router.callback_query(F.data == "menu:stats")
async def cb_analytics(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await call.message.answer("⏳ Загружаю аналитику...")

    try:
        pvz_list = await get_all_pvz_analytics()
    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}", reply_markup=main_menu())
        return

    from datetime import date, timedelta
    from db.database import get_turnover
    today = date.today()
    week_start = (today - timedelta(days=6)).strftime("%-d %b").lower()
    week_end = today.strftime("%-d %b").lower()
    cur_month, cur_year = today.month, today.year

    if not pvz_list:
        await call.message.answer("⚠️ Список ПВЗ пуст — не удалось получить данные.", reply_markup=main_menu())
        return

    text = f"📊 <b>Статистика ПВЗ</b>  ({week_start} — {week_end})\n"

    for pvz in pvz_list:
        name = pvz["name"]

        # Посылки
        received = pvz.get("received_total")
        received_str = f"{received:,} шт." if received is not None else "—"

        # Уникальные клиенты
        clients = pvz.get("unique_clients_last")
        clients_prev = pvz.get("unique_clients_prev")
        if clients is not None and clients_prev is not None:
            diff = clients - clients_prev
            arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "")
            clients_str = f"{clients} ({arrow}{abs(diff)})"
        else:
            clients_str = str(clients) if clients is not None else "—"

        # Частота заказов
        freq = pvz.get("frequency")
        freq_region = pvz.get("frequency_region")
        if freq is not None and freq_region is not None:
            diff_f = round(freq - freq_region, 2)
            sign = "+" if diff_f >= 0 else ""
            freq_str = f"{freq} (регион: {freq_region}, {sign}{diff_f})"
        else:
            freq_str = str(freq) if freq is not None else "—"

        # Рейтинг
        rating = pvz.get("rating")
        delta = pvz.get("rating_delta")
        if rating is not None:
            sign = "+" if delta and delta >= 0 else ""
            rating_str = f"{rating} ⭐ ({sign}{delta})" if delta is not None else f"{rating} ⭐"
        else:
            rating_str = "—"

        # Товарооборот
        turnover_val = await get_turnover(name, cur_month, cur_year)
        if turnover_val:
            turnover_str = f"{turnover_val:,.2f} руб."
        else:
            turnover_str = "⚠️ не введён  →  нажми <b>📝 Оборот</b>"

        text += (
            f"\n\n🏪 <b>{name}</b>\n"
            f"💼 Товарооборот: {turnover_str}\n"
            f"📦 Принято за неделю: {received_str}\n"
            f"👥 Уникальных клиентов: {clients_str}\n"
            f"🔁 Частота заказов: {freq_str}\n"
            f"⭐ Рейтинг: {rating_str}"
        )

    await call.message.answer(text, parse_mode="HTML", reply_markup=main_menu())


# ── Аналитика (ИИ-аудит) — выбор ПВЗ ──────────────────────────────────────

@router.callback_query(F.data == "menu:analytics")
async def cb_analytics_pick_pvz(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    stores = await _get_all_stores()
    buttons = [
        [InlineKeyboardButton(text=f"🏪 {s['name']}", callback_data=f"audit_pvz:{s['id']}")]
        for s in stores
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:back")])

    await call.message.answer(
        "🤖 <b>ИИ-аналитика ПВЗ</b>\n\nВыбери точку для аудита:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("audit_pvz:"))
async def cb_audit_pvz(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    store_id = int(call.data.split(":")[1])
    stores = await _get_all_stores()
    store_name = next((s["name"] for s in stores if s["id"] == store_id), str(store_id))

    await call.message.answer(f"🤖 Собираю данные и готовлю аудит <b>{store_name}</b>...", parse_mode="HTML")

    try:
        audit_text = await get_pvz_audit(store_id, store_name)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка при генерации аудита: {e}", reply_markup=main_menu())
        return

    await call.message.answer(
        f"🤖 <b>Аудит ПВЗ: {store_name}</b>\n\n{audit_text}",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ── Назад ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:back")
async def cb_back(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await call.message.answer("Выбери раздел:", reply_markup=main_menu())
