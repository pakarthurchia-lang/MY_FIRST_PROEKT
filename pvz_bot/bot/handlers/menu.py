import os
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile,
)
from config import OWNER_CHAT_ID
from ozon.scraper import scrape_claims, get_monthly_stats, get_available_reports
from yandex.reports import available_months_for_menu as ym_available_months
from ozon.analytics import get_all_pvz_analytics
from bot.handlers.claims import format_claim
from wildberries.http_client import get_token_status

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
            InlineKeyboardButton(text="🤖 Аналитика", callback_data="menu:analytics"),
        ],
    ])


WELCOME_IMAGE = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "welcome.jpg")

WELCOME_TEXT = (
    "👋 <b>Привет, Артур!</b>\n\n"
    "Я твой AI-ассистент по ПВЗ.\n"
    "Слежу за прибылью, претензиями и аналитикой по трём платформам:\n\n"
    "🔵 Ozon · 🟣 Wildberries · 🟡 Яндекс Маркет\n\n"
    "Выбери раздел:"
)


@router.message(CommandStart())
async def cmd_start(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    img = os.path.normpath(WELCOME_IMAGE)
    if os.path.exists(img):
        await message.answer_photo(
            photo=FSInputFile(img),
            caption=WELCOME_TEXT,
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            WELCOME_TEXT,
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


# ── Прибыль — выбор платформы ──────────────────────────────────────────────

def _platform_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Ozon",           callback_data="profit_platform:ozon:0")],
        [InlineKeyboardButton(text="🟣 Wildberries",   callback_data="profit_platform:wb:0")],
        [InlineKeyboardButton(text="🟡 Яндекс Маркет", callback_data="profit_platform:ym:0")],
        [InlineKeyboardButton(text="◀️ Назад",         callback_data="menu:back")],
    ])


@router.callback_query(F.data == "menu:profit")
async def cb_profit_menu(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await call.message.answer(
        "💰 <b>Прибыль</b>\n\nВыбери платформу:",
        reply_markup=_platform_keyboard(),
        parse_mode="HTML",
    )


# ── Прибыль — выбор месяца по платформе ────────────────────────────────────

PAGE_SIZE = 6  # месяцев на странице (2 колонки × 3 строки)


def _months_keyboard(platform: str, months: list, page: int) -> InlineKeyboardMarkup:
    """Строит клавиатуру с месяцами: 2 колонки, PAGE_SIZE месяцев + пагинация."""
    start = page * PAGE_SIZE
    chunk = months[start: start + PAGE_SIZE]

    icons = {"ozon": "🔵", "wb": "🟣", "ym": "🟡"}
    prefixes = {"ozon": "profit", "wb": "wb_profit", "ym": "ym_profit"}
    icon = icons[platform]
    prefix = prefixes[platform]

    buttons = []
    # Пары кнопок по 2 в ряд
    row = []
    for item in chunk:
        m, y = item["month"], item["year"]
        label = item.get("label") or f"{MONTHS_RU[m][:3]} {y}"
        row.append(InlineKeyboardButton(
            text=f"{icon} {label}",
            callback_data=f"{prefix}:{m}:{y}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Пагинация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"profit_platform:{platform}:{page - 1}"))
    if start + PAGE_SIZE < len(months):
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"profit_platform:{platform}:{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="↩️ К платформам", callback_data="menu:profit")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("profit_platform:"))
async def cb_profit_platform(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    _, platform, page_str = call.data.split(":")
    page = int(page_str)

    names = {"ozon": "🔵 Ozon", "wb": "🟣 Wildberries", "ym": "🟡 Яндекс Маркет"}

    try:
        if platform == "ozon":
            reports = await get_available_reports()
            months = sorted(
                [{"month": r["month"], "year": r["year"], "label": r["label"]} for r in reports],
                key=lambda x: (x["year"], x["month"]), reverse=True,
            )
        elif platform == "wb":
            if not get_token_status()["valid"]:
                await call.message.answer(
                    "⚠️ WB токен не найден или истёк.\n"
                    "Обнови: /wb_token eyJ...",
                    reply_markup=_platform_keyboard(),
                )
                return
            from wildberries.api import get_available_months as wb_get_months
            months = await wb_get_months(24)
        elif platform == "ym":
            months = ym_available_months(24)
        else:
            return
    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}", reply_markup=_platform_keyboard())
        return

    if not months:
        await call.message.answer(
            f"⚠️ Нет данных для {names[platform]}",
            reply_markup=_platform_keyboard(),
        )
        return

    total = len(months)
    shown_from = page * PAGE_SIZE + 1
    shown_to = min((page + 1) * PAGE_SIZE, total)
    page_info = f"  <i>{shown_from}–{shown_to} из {total}</i>" if total > PAGE_SIZE else ""

    await call.message.answer(
        f"💰 {names[platform]} — выбери месяц:{page_info}",
        reply_markup=_months_keyboard(platform, months, page),
        parse_mode="HTML",
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

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ К месяцам Ozon", callback_data="profit_platform:ozon:0")],
        [InlineKeyboardButton(text="🏠 Главное меню",   callback_data="menu:back")],
    ])
    await call.message.answer(text, parse_mode="HTML", reply_markup=back_kb)


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
    today = date.today()
    week_start = (today - timedelta(days=6)).strftime("%-d %b").lower()
    week_end = today.strftime("%-d %b").lower()

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

        text += (
            f"\n\n🏪 <b>{name}</b>\n"
            f"📦 Принято за неделю: {received_str}\n"
            f"👥 Уникальных клиентов: {clients_str}\n"
            f"🔁 Частота заказов: {freq_str}\n"
            f"⭐ Рейтинг: {rating_str}"
        )

    await call.message.answer(text, parse_mode="HTML", reply_markup=main_menu())


# ── Назад ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:back")
async def cb_back(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    img = os.path.normpath(WELCOME_IMAGE)
    if os.path.exists(img):
        await call.message.answer_photo(
            photo=FSInputFile(img),
            caption=WELCOME_TEXT,
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
    else:
        await call.message.answer(WELCOME_TEXT, reply_markup=main_menu(), parse_mode="HTML")
