from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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



@router.callback_query(F.data.startswith("claim_detail:"))
async def cb_claim_detail(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    parts = call.data.split(":")
    claim_id = parts[1]
    store_id = parts[2]
    request_type = parts[3] if len(parts) > 3 else "Claim"

    await call.message.answer(f"🔄 Загружаю детали претензии №{claim_id}...")

    try:
        from ozon.claim_detail import get_claim_detail
        detail = await get_claim_detail(claim_id, store_id, request_type)
    except Exception as e:
        await call.message.answer(f"❌ Не удалось загрузить детали: {e}")
        return

    msg = detail.get("message", "")
    sidebar = ""
    if detail.get("date_issued"):   sidebar += f"📅 Дата: {detail['date_issued']}\n"
    if detail.get("reason"):        sidebar += f"⚡ Причина: {detail['reason']}\n"
    if detail.get("amount"):        sidebar += f"💸 Сумма: {detail['amount']} руб.\n"
    if detail.get("time_to_respond"): sidebar += f"⏰ Срок ответа: {detail['time_to_respond']}\n"
    if detail.get("direction"):     sidebar += f"📦 Направление: {detail['direction']}\n"
    if detail.get("shipping_number"): sidebar += f"🚚 Перевозка №{detail['shipping_number']}\n"
    if detail.get("shipping_date"): sidebar += f"📆 Дата перевозки: {detail['shipping_date']}\n"

    # Кратко суммируем сообщение от Ozon через Claude
    summary = msg
    if msg and len(msg) > 100:
        try:
            import os
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            res = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{"role": "user", "content": (
                    f"Кратко опиши суть этого сообщения от Ozon в 1-2 предложениях. "
                    f"Только суть — что произошло и что требуется. Не пересказывай полностью.\n\n{msg[:2000]}"
                )}],
            )
            summary = res.content[0].text.strip()
        except Exception:
            summary = msg[:300] + ("..." if len(msg) > 300 else "")

    text = (
        f"📋 <b>Претензия №{claim_id}</b>\n\n"
        f"{sidebar}\n"
        f"💬 <b>Суть:</b>\n{summary}"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🤖 Как обработать претензию",
            callback_data=f"claim_advice:{claim_id}:{store_id}:{request_type}",
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="claims:ozon")],
    ])
    await call.message.answer(text[:4000], parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data.startswith("claim_advice:"))
async def cb_claim_advice(call: CallbackQuery):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()

    parts = call.data.split(":")
    claim_id = parts[1]
    store_id = parts[2]
    request_type = parts[3] if len(parts) > 3 else "Claim"

    await call.message.answer(f"🤖 Анализирую претензию №{claim_id}...")

    try:
        from ozon.claim_detail import get_claim_detail
        detail = await get_claim_detail(claim_id, store_id, request_type)
    except Exception as e:
        await call.message.answer(f"❌ Не удалось загрузить детали: {e}")
        return

    # Строим промпт для Claude
    prompt = f"""Ты эксперт по работе с претензиями Ozon ПВЗ. Помоги обработать претензию.

Претензия №{claim_id}
Причина: {detail.get('reason', '—')}
Сумма: {detail.get('amount', '—')} руб.
Направление: {detail.get('direction', '—')}
Срок ответа: {detail.get('time_to_respond', '—')}
Номер перевозки: {detail.get('shipping_number', '—')}
Дата перевозки: {detail.get('shipping_date', '—')}

Текст от Ozon:
{detail.get('message', '—')}

Дай:
1. Краткий анализ — в чём суть претензии и что от нас требуют
2. Конкретные шаги что нужно сделать (проверить камеры, найти видео, написать ответ и т.д.)
3. Готовый текст ответа Ozon (можно вставить в форму на сайте)
4. Шансы на оспаривание (высокие/средние/низкие) и почему

Отвечай на русском, конкретно и по делу. Не более 400 слов."""

    try:
        import os
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        advice = message.content[0].text
    except Exception as e:
        await call.message.answer(f"❌ Ошибка Claude: {e}")
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К претензии", callback_data=f"claim_detail:{claim_id}:{store_id}:{request_type}")],
        [InlineKeyboardButton(text="📋 Все претензии", callback_data="claims:ozon")],
    ])
    await call.message.answer(
        f"🤖 <b>Рекомендация по претензии №{claim_id}</b>\n\n{advice}",
        parse_mode="HTML",
        reply_markup=markup,
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
