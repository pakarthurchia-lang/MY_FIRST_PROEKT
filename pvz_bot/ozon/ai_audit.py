"""
ИИ-аудит ПВЗ через Claude API.
"""
import os
import anthropic
from datetime import date

from ozon.analytics import get_store_analytics
from ozon.scraper import get_available_reports, get_monthly_stats
from db.database import get_turnover


async def get_pvz_audit(store_id: int, store_name: str) -> str:
    """Собирает данные по ПВЗ и генерирует аудит через Claude."""

    # 1. Аналитика за текущую неделю
    analytics = await get_store_analytics(store_id, store_name)

    # 2. Финансы за последние доступные месяцы (до 6)
    profit_data = []
    reports_total = 0
    try:
        reports = await get_available_reports()
        reports_total = len(reports)  # для оценки возраста ПВЗ
        for r in reports[:6]:
            try:
                stats = await get_monthly_stats(month=r["month"], year=r["year"])
                pvz_rev = stats.get("pvz_revenue", {}).get(store_name)
                if pvz_rev:
                    tax = round(pvz_rev * stats["tax_rate"], 2)
                    fines = stats.get("fines_by_pvz", {}).get(store_name, 0)
                    profit = round(pvz_rev - tax - fines, 2)
                    profit_data.append({
                        "period": r["label"],
                        "revenue": pvz_rev,
                        "tax": tax,
                        "fines": fines,
                        "profit": profit,
                    })
            except Exception:
                pass
    except Exception:
        pass

    # 3. Оборот из БД (текущий месяц)
    today = date.today()
    turnover = await get_turnover(store_name, today.month, today.year)

    # 4. Формируем промпт и вызываем Claude
    prompt = _build_prompt(store_name, analytics, profit_data, turnover, reports_total)

    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _build_prompt(store_name: str, analytics: dict, profit_data: list, turnover, reports_total: int) -> str:
    # Финансовый тренд
    trend = ""
    if len(profit_data) >= 2:
        diff = profit_data[0]["profit"] - profit_data[-1]["profit"]
        trend = f"рост +{diff:,.0f} руб." if diff > 0 else f"падение {diff:,.0f} руб."

    # Возраст ПВЗ (примерно по числу месяцев отчётов)
    age_str = f"~{reports_total} мес." if reports_total else "неизвестен"

    lines = [
        "Ты эксперт по оценке бизнеса. Дай краткий аудит ПВЗ Ozon для принятия решения о покупке или продаже.",
        f"ПВЗ: {store_name} | Возраст в системе: {age_str}",
        "",
        "ДАННЫЕ:",
    ]

    # Финансы
    if profit_data:
        lines.append("Вознаграждение Ozon (после налогов и штрафов) по месяцам:")
        for p in profit_data:
            lines.append(f"  {p['period']}: {p['revenue']:,.0f} → чистая {p['profit']:,.0f} руб. (штрафы: {p['fines']:,.0f})")
        if trend:
            lines.append(f"Тренд: {trend}")

    if turnover:
        lines.append(f"Товарооборот тек. месяц: {turnover:,.0f} руб.")

    # Трафик
    lines += [
        f"Выдач за неделю: {analytics.get('received_total', '—')} шт.",
        f"Уникальных клиентов: {analytics.get('unique_clients_last', '—')} (пред.: {analytics.get('unique_clients_prev', '—')})",
        f"Частота заказов: {analytics.get('frequency', '—')} (регион: {analytics.get('frequency_region', '—')})",
        f"Рейтинг: {analytics.get('rating', '—')} (δ {analytics.get('rating_delta', '—')})",
        "",
        "ВАЖНО: данные о расходах (аренда, ФОТ, коммуналка), конкурентах рядом и локации НЕ предоставлены.",
        "",
        "ФОРМАТ ОТВЕТА (строго, без воды, до 200 слов):",
        "🎯 Вердикт: [одно предложение — покупать / держать / продавать и почему]",
        "💰 Финансы: [оценка доходности, тренд, риск штрафов]",
        "📦 Трафик: [оценка потока, сравнение с регионом]",
        "⚠️ Что проверить до сделки: [конкретный список — конкуренты, аренда, ФОТ, возраст и т.д.]",
        "💵 Справедливая цена: [оценочный диапазон = N × месячная прибыль, где N обоснуй]",
    ]

    return "\n".join(lines)
