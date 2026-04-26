import os
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile,
)
from config import OWNER_CHAT_ID
from ozon.scraper import scrape_claims, get_monthly_stats, get_available_reports, scrape_archive_claims
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
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Ozon",            callback_data="claims:ozon_menu")],
        [InlineKeyboardButton(text="🟣 Wildberries",     callback_data="claims:wb")],
        [InlineKeyboardButton(text="🟡 Яндекс Маркет",  callback_data="claims:ym")],
        [InlineKeyboardButton(text="◀️ Назад",           callback_data="menu:back")],
    ])
    await call.message.answer(
        "📋 <b>Претензии и штрафы</b>\n\nВыбери платформу:",
        parse_mode="HTML", reply_markup=markup,
    )


@router.callback_query(F.data == "claims:ozon_menu")
async def cb_claims_ozon_menu(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Активные претензии",   callback_data="claims:ozon")],
        [InlineKeyboardButton(text="💸 Архив (списания)",     callback_data="claims:ozon_archive")],
        [InlineKeyboardButton(text="◀️ Назад",                callback_data="menu:claims")],
    ])
    await call.message.answer(
        "🔵 <b>Претензии Ozon</b>\n\nВыбери раздел:",
        parse_mode="HTML", reply_markup=markup,
    )


@router.callback_query(F.data == "claims:ozon")
async def cb_claims_ozon(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await call.message.answer("🔄 Получаю претензии Ozon...")
    try:
        claims = await scrape_claims()
    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}", reply_markup=main_menu())
        return

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К претензиям", callback_data="menu:claims")],
    ])
    if not claims:
        await call.message.answer("✅ Активных претензий Ozon нет!", reply_markup=back_kb)
        return

    claims.sort(key=lambda c: c.get("deadline") or "9999")
    total = sum(c.get("amount", 0) for c in claims)
    text = f"📋 <b>Претензии Ozon</b> — {len(claims)} шт.\n"
    text += f"💸 Итого: <b>{total:,.2f} руб.</b>\n\n"
    for claim in claims:
        text += format_claim(claim) + "\n\n"

    # Кнопки для каждой претензии
    buttons = []
    for claim in claims:
        cb = f"claim_detail:{claim['id']}:{claim.get('store_id','')}:{claim.get('request_type','Claim')}"
        buttons.append([InlineKeyboardButton(
            text=f"🔍 №{claim['id']} — {claim['claim_type']} {claim['amount']:,.0f}₽",
            callback_data=cb,
        )])
    buttons.append([InlineKeyboardButton(text="◀️ К претензиям", callback_data="menu:claims")])
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    await call.message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data == "claims:ozon_archive")
async def cb_claims_ozon_archive(call: CallbackQuery):
    """Показывает выбор месяца для архива претензий Ozon."""
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    from datetime import date
    today = date.today()

    buttons = []
    row = []
    for i in range(12):
        # идём назад от прошлого месяца
        month = (today.month - 1 - i - 1) % 12 + 1
        year  = today.year + ((today.month - 1 - i - 1) // 12)
        label = f"{MONTHS_RU[month][:3]} {year}"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"ozon_arch:{month}:{year}",
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="claims:ozon_menu")])

    await call.message.answer(
        "💸 <b>Архив претензий Ozon (списания)</b>\n\nВыбери месяц:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.regexp(r"^ozon_arch:\d+:\d+$"))
async def cb_ozon_archive_month(call: CallbackQuery):
    """Загружает и показывает архивные претензии за выбранный месяц."""
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    _, month_str, year_str = call.data.split(":")
    month, year = int(month_str), int(year_str)

    await call.message.answer(
        f"⏳ Загружаю архив претензий Ozon за {MONTHS_RU[month]} {year}..."
    )

    try:
        claims = await scrape_archive_claims(month, year)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}")
        return

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К выбору месяца", callback_data="claims:ozon_archive")],
        [InlineKeyboardButton(text="🏠 Меню",            callback_data="menu:back")],
    ])

    _CLAIM_EMOJI = {
        "Штраф":          "⚠️",
        "Возврат агента":  "🔄",
        "Утеря":           "📦",
        "Повреждение":     "🔨",
        "Недостача":       "📉",
        "Претензия":       "📋",
    }
    _MONTHS_SHORT = ["", "янв", "фев", "мар", "апр", "май", "июн",
                     "июл", "авг", "сен", "окт", "ноя", "дек"]

    def _short_date(d: str) -> str:
        parts = d.split("-")
        if len(parts) == 3:
            return f"{int(parts[2])} {_MONTHS_SHORT[int(parts[1])]}"
        return d

    if not claims:
        await call.message.answer(
            f"📭 Архивных претензий за {MONTHS_RU[month]} {year} нет\n"
            f"(статусы: Оплачена / Удержано из АВ)",
            reply_markup=back_kb,
        )
        return

    from collections import defaultdict
    by_pvz: dict = defaultdict(list)
    for c in claims:
        by_pvz[c["pvz"]].append(c)

    total_all = sum(c["amount"] for c in claims)
    text = (
        f"💸 <b>Архив Ozon — {MONTHS_RU[month]} {year}</b>\n"
        f"📊 {len(claims)} претензий · <b>{total_all:,.0f} ₽</b>\n"
    )

    for pvz_name, pvz_claims in sorted(by_pvz.items()):
        pvz_total = sum(c["amount"] for c in pvz_claims)
        text += f"\n\n🏪 <b>{pvz_name}</b> · {pvz_total:,.0f} ₽\n"

        # Группируем по типу, сортируем по сумме убыванию
        by_type: dict = defaultdict(list)
        for c in pvz_claims:
            by_type[c["claim_type"]].append(c)

        for claim_type, type_claims in sorted(
            by_type.items(), key=lambda x: -sum(c["amount"] for c in x[1])
        ):
            type_total = sum(c["amount"] for c in type_claims)
            emoji = _CLAIM_EMOJI.get(claim_type, "•")
            text += (
                f"{emoji} {claim_type} ({len(type_claims)}): "
                f"<b>{type_total:,.0f} ₽</b>\n"
            )
            for c in sorted(type_claims, key=lambda x: -x["amount"]):
                text += f"   {_short_date(c['date_issued'])} · {c['amount']:,.0f} ₽\n"

    await call.message.answer(text, parse_mode="HTML", reply_markup=back_kb)


@router.callback_query(F.data == "claims:wb")
async def cb_claims_wb(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await call.message.answer("🔄 Получаю удержания Wildberries...")

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К претензиям", callback_data="menu:claims")],
    ])
    try:
        from wildberries.http_client import get_pickpoint_id
        from wildberries.api import fetch_all_payments, aggregate_by_month
        from datetime import date

        pid = get_pickpoint_id()
        if not pid:
            await call.message.answer("❌ WB токен не найден. Войди через /wb_login.", reply_markup=back_kb)
            return

        payments = await fetch_all_payments(pid)
        by_month = aggregate_by_month(payments)

        # Последние 3 месяца с удержаниями
        today = date.today()
        rows = []
        total_fines = 0.0
        for (m, y), data in sorted(by_month.items(), key=lambda x: (x[0][1], x[0][0]), reverse=True)[:6]:
            if data["fines"] > 0:
                rows.append(
                    f"📅 <b>{MONTHS_RU[m]} {y}</b>\n"
                    f"   Удержания: <b>-{data['fines']:,.2f} руб.</b>\n"
                    f"   Выдач: {data['orders']} | Вознаграждение: {data['revenue']:,.2f} руб."
                )
                total_fines += data["fines"]

        if not rows:
            await call.message.answer("✅ Удержаний WB за последние 6 месяцев нет!", reply_markup=back_kb)
            return

        text = (
            f"🟣 <b>Удержания Wildberries</b>\n"
            f"💸 Итого за период: <b>{total_fines:,.2f} руб.</b>\n\n"
            + "\n\n".join(rows)
        )
        await call.message.answer(text, parse_mode="HTML", reply_markup=back_kb)

    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}", reply_markup=back_kb)


@router.callback_query(F.data == "claims:ym")
async def cb_claims_ym(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await call.message.answer("🔄 Получаю штрафы Яндекс Маркет...")

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К претензиям", callback_data="menu:claims")],
    ])
    try:
        from yandex.reports import download_report_xlsx, available_months_for_menu
        from yandex.xlsx_parser import parse_ym_fines

        months = available_months_for_menu(3)
        if not months:
            await call.message.answer("❌ Отчёты ЯМ не найдены.", reply_markup=back_kb)
            return

        all_rows = []
        total_fines = 0.0
        for m in months:
            try:
                xlsx = await download_report_xlsx(m["month"], m["year"])
                fines = parse_ym_fines(xlsx, m["month"], m["year"])
                if not fines:
                    continue
                period_total = sum(v["total"] for v in fines.values())
                if period_total <= 0:
                    continue
                total_fines += period_total
                block = f"📅 <b>{m['label']}</b> — {period_total:,.2f} руб.\n"
                for pvz_name, data in fines.items():
                    if data["total"] <= 0:
                        continue
                    block += f"   🏪 {pvz_name}: <b>{data['total']:,.2f} руб.</b>\n"
                    for item in data.get("items", [])[:5]:
                        block += f"      • {item.get('date', '')[:10]} {item.get('reason', '')}: -{item.get('amount', 0):,.2f} руб.\n"
                all_rows.append(block)
            except Exception:
                continue

        if not all_rows:
            await call.message.answer("✅ Штрафов ЯМ за последние 3 месяца нет!", reply_markup=back_kb)
            return

        text = (
            f"🟡 <b>Штрафы Яндекс Маркет</b>\n"
            f"💸 Итого за период: <b>{total_fines:,.2f} руб.</b>\n\n"
            + "\n".join(all_rows)
        )
        await call.message.answer(text, parse_mode="HTML", reply_markup=back_kb)

    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}", reply_markup=back_kb)


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

@router.callback_query(F.data.regexp(r"^profit:\d+:\d+$"))
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
        if pvz_revenue.get("_error"):
            err = pvz_revenue["_error"]
            if "403" in err:
                pvz_lines = "\n⚠️ <i>Разбивка по ПВЗ недоступна — нужен Web-токен. Запусти /login.</i>"
            else:
                pvz_lines = f"\n⚠️ <i>PDF не распарсился: {err[:120]}</i>"

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
