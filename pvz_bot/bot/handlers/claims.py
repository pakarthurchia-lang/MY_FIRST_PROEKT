from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message
from ozon.scraper import scrape_claims, get_monthly_stats
from config import OWNER_CHAT_ID

router = Router()


def format_deadline(deadline_str) -> str:
    if not deadline_str:
        return "не указан"
    try:
        deadline = datetime.fromisoformat(deadline_str)
        now = datetime.now()
        diff = deadline - now

        if diff.total_seconds() < 0:
            return "❌ Истёк"
        hours = int(diff.total_seconds() / 3600)
        if hours < 2:
            return f"🚨 {hours}ч {int((diff.total_seconds() % 3600) / 60)}мин"
        elif hours < 24:
            return f"⚠️ {hours} ч"
        else:
            days = diff.days
            return f"🕐 {days} дн"
    except Exception:
        return deadline_str


def format_claim(claim: dict) -> str:
    deadline = format_deadline(claim.get("deadline"))
    return (
        f"{'🔴' if 'Штраф' in (claim.get('reason') or '') else '🟡'} "
        f"№{claim['id']} | {claim['pvz']}\n"
        f"   Причина: {claim.get('reason', '—')}\n"
        f"   Сумма: {claim.get('amount', 0):,.2f} руб.\n"
        f"   Дата: {claim.get('date_issued', '—')[:10] if claim.get('date_issued') else '—'}\n"
        f"   Срок: {deadline}"
    )



@router.message(F.text == "/claims")
async def cmd_claims(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    await message.answer("🔄 Получаю актуальные претензии...")

    try:
        claims = await scrape_claims()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {e}\nПопробуй /login для повторной авторизации.")
        return

    if not claims:
        await message.answer("✅ Активных претензий нет!")
        return

    # Сортируем: сначала срочные
    claims.sort(key=lambda c: c.get("deadline") or "9999")

    total = sum(c.get("amount", 0) for c in claims)
    text = f"📋 <b>Претензии Ozon</b> — {len(claims)} шт.\n"
    text += f"💸 Итого под угрозой: <b>{total:,.2f} руб.</b>\n\n"

    for claim in claims:
        text += format_claim(claim) + "\n\n"

    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "/stats")
async def cmd_stats(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    await message.answer("📊 Загружаю финансовую статистику...")

    try:
        stats = await get_monthly_stats()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return

    if "error" in stats:
        await message.answer(f"⚠️ {stats['error']}")
        return

    tax_pct = int(stats["tax_rate"] * 100)

    pvz_revenue = stats.get("pvz_revenue", {})
    if "_error" in pvz_revenue:
        await message.answer(f"⚠️ Не удалось загрузить разбивку по ПВЗ: {pvz_revenue['_error']}")
    pvz_lines = ""
    if pvz_revenue and "_error" not in pvz_revenue:
        for pvz_name, pvz_amt in pvz_revenue.items():
            pvz_tax = round(pvz_amt * stats["tax_rate"], 2)
            pvz_profit = round(pvz_amt - pvz_tax, 2)
            pvz_lines += (
                f"\n🏪 <b>{pvz_name}</b>\n"
                f"   Выручка: {pvz_amt:,.2f} руб.\n"
                f"   Налог {tax_pct}%: -{pvz_tax:,.2f} руб.\n"
                f"   Прибыль: {pvz_profit:,.2f} руб."
            )

    text = (
        f"📊 <b>Финансы Ozon ПВЗ</b>\n"
        f"📅 Период: {stats['begin_date']} — {stats['end_date']}\n\n"
        f"💰 <b>Вознаграждение итого:</b> {stats['revenue']:,.2f} руб."
        f"{pvz_lines}\n\n"
        f"🏛 Налог УСН {tax_pct}% итого: -{stats['tax']:,.2f} руб.\n"
        f"⚠️ К вычету из вознаграждения: -{stats['fines_total']:,.2f} руб.\n"
        f"{'─' * 30}\n"
        f"✅ <b>Чистая прибыль: {stats['profit']:,.2f} руб.</b>"
    )

    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "/help")
async def cmd_help(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    text = (
        "📦 <b>ПВЗ Аналитика — команды:</b>\n\n"
        "/claims — актуальные претензии Ozon\n"
        "/stats — статистика по точкам\n"
        "/login — повторная авторизация (если сессия истекла)\n"
        "/help — эта справка\n\n"
        "🔔 Бот автоматически уведомляет о претензиях за 24ч и 2ч до истечения срока."
    )
    await message.answer(text, parse_mode="HTML")
