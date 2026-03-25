"""
ИИ-аудит ПВЗ через Claude API.
"""
import os
import anthropic
from typing import Dict, List


async def get_pvz_audit(location: dict, expenses: dict, month: int, year: int) -> str:
    """
    Собирает данные по локации и генерирует аудит через Claude.

    location = get_location_with_pvzs(...) результат:
        {id, name, pvzs: [{platform, pvz_id, pvz_name}]}

    expenses = {"rent", "salary", "utilities", "turnover", "ym_turnover"}
    month, year — выбранный пользователем период
    """
    from ozon.scraper import get_available_reports, get_monthly_stats
    from ozon.analytics import get_store_analytics

    ozon_pvzs = [p for p in location["pvzs"] if p["platform"] == "ozon"]
    ym_pvzs = [p for p in location["pvzs"] if p["platform"] == "ym"]

    # ── Ozon данные ────────────────────────────────────────────────────────
    ozon_profit_data: Dict[str, List] = {}  # pvz_name -> list of monthly dicts
    ozon_analytics: Dict[str, dict] = {}   # pvz_name -> analytics dict
    reports_total = 0
    ozon_data_missing = False

    if ozon_pvzs:
        try:
            reports = await get_available_reports()
            reports_total = len(reports)
            # Берём до 6 месяцев, начиная с выбранного пользователем
            # Сортируем: сначала самый близкий к выбранному месяцу
            def _dist(r):
                return abs((r["year"] - year) * 12 + (r["month"] - month))
            sorted_reports = sorted(reports, key=_dist)[:6]
            for r in sorted_reports:
                try:
                    stats = await get_monthly_stats(month=r["month"], year=r["year"])
                    for pvz in ozon_pvzs:
                        name = pvz["pvz_name"]
                        pvz_rev = stats.get("pvz_revenue", {}).get(name)
                        if pvz_rev:
                            tax = round(pvz_rev * stats["tax_rate"], 2)
                            fines = stats.get("fines_by_pvz", {}).get(name, 0)
                            profit = round(pvz_rev - tax - fines, 2)
                            ozon_profit_data.setdefault(name, []).append({
                                "period": r["label"],
                                "revenue": pvz_rev,
                                "tax": tax,
                                "fines": fines,
                                "profit": profit,
                            })
                except Exception:
                    pass
            if not ozon_profit_data:
                ozon_data_missing = True
        except Exception:
            ozon_data_missing = True

        for pvz in ozon_pvzs:
            try:
                store_id = int(pvz["pvz_id"]) if pvz.get("pvz_id") else None
                if store_id:
                    analytics = await get_store_analytics(store_id, pvz["pvz_name"])
                    ozon_analytics[pvz["pvz_name"]] = analytics
            except Exception:
                pass

    # ── Яндекс Маркет данные ───────────────────────────────────────────────
    ym_revenue_data: Dict[str, List] = {}   # pvz_name -> list of monthly dicts
    ym_turnover_data: Dict[str, List] = {}  # pvz_name -> list of monthly dicts
    ym_fines_data: Dict[str, dict] = {}     # pvz_name -> {total, items}

    if ym_pvzs:
        try:
            from yandex.reports import download_report_xlsx, available_months_for_menu
            from yandex.xlsx_parser import parse_ym_xlsx, parse_ym_turnover, parse_ym_fines

            months = available_months_for_menu(3)
            for m in months:
                try:
                    xlsx_bytes = await download_report_xlsx(m["month"], m["year"])
                    ym_data = parse_ym_xlsx(xlsx_bytes)
                    ym_turn = parse_ym_turnover(xlsx_bytes)
                    ym_fines = parse_ym_fines(xlsx_bytes, month=m["month"], year=m["year"])
                    for pvz in ym_pvzs:
                        name = pvz["pvz_name"]
                        rev = ym_data.get(name)
                        if rev is not None:
                            ym_revenue_data.setdefault(name, []).append({
                                "period": m["label"],
                                "revenue": rev,
                                "fines": ym_fines.get(name, {}).get("total", 0.0),
                            })
                        turn = ym_turn.get(name)
                        if turn is not None:
                            ym_turnover_data.setdefault(name, []).append({
                                "period": m["label"],
                                "turnover": turn,
                            })
                        # Штрафы за самый свежий (выбранный) месяц
                        if m["month"] == month and m["year"] == year and name in ym_fines:
                            ym_fines_data[name] = ym_fines[name]
                except Exception:
                    pass
        except Exception:
            pass

    # ── Строим промпт ──────────────────────────────────────────────────────
    prompt = _build_prompt(
        location=location,
        expenses=expenses,
        month=month,
        year=year,
        ozon_profit_data=ozon_profit_data,
        ozon_analytics=ozon_analytics,
        ym_revenue_data=ym_revenue_data,
        ym_turnover_data=ym_turnover_data,
        ym_fines_data=ym_fines_data,
        reports_total=reports_total,
        ozon_data_missing=ozon_data_missing and bool(ozon_pvzs),
    )

    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _build_prompt(
    location: dict,
    expenses: dict,
    month: int,
    year: int,
    ozon_profit_data: dict,
    ozon_analytics: dict,
    ym_revenue_data: dict,
    ym_turnover_data: dict,
    ym_fines_data: dict,
    reports_total: int,
    ozon_data_missing: bool = False,
) -> str:
    MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                 "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

    lines = [
        "Ты эксперт по оценке бизнеса. Дай краткий аудит локации ПВЗ для принятия решения о покупке или продаже.",
        f"Локация: {location['name']}",
        f"Анализируемый период: {MONTHS_RU[month]} {year}",
        f"Возраст в системе: ~{reports_total} мес." if reports_total else "Возраст: неизвестен",
    ]

    if ozon_data_missing:
        lines += [
            "",
            "⚠️ ВАЖНО: данные Ozon НЕДОСТУПНЫ (токен истёк). Не делай выводов об убыточности или прибыльности",
            "на основе отсутствующих данных Ozon. Укажи это как критическое ограничение анализа.",
        ]

    lines += ["", "ДАННЫЕ ПО ПВЗ:"]

    # Ozon финансы
    if ozon_profit_data:
        lines.append("\nOzon — финансы по месяцам:")
        for pvz_name, months_list in ozon_profit_data.items():
            lines.append(f"  ПВЗ: {pvz_name}")
            for p in months_list:
                lines.append(
                    f"    {p['period']}: выручка {p['revenue']:,.0f} → чистая {p['profit']:,.0f} руб."
                    f" (налог: {p['tax']:,.0f}, штрафы: {p['fines']:,.0f})"
                )
            if len(months_list) >= 2:
                diff = months_list[0]["profit"] - months_list[-1]["profit"]
                trend = f"рост +{diff:,.0f} руб." if diff > 0 else f"падение {diff:,.0f} руб."
                lines.append(f"    Тренд: {trend}")

    # Ozon трафик
    if ozon_analytics:
        lines.append("\nOzon — трафик (за неделю):")
        for pvz_name, a in ozon_analytics.items():
            lines.append(
                f"  {pvz_name}: выдач {a.get('received_total', '—')}, "
                f"клиентов {a.get('unique_clients_last', '—')} (пред.: {a.get('unique_clients_prev', '—')}), "
                f"частота {a.get('frequency', '—')} (регион: {a.get('frequency_region', '—')}), "
                f"рейтинг {a.get('rating', '—')}"
            )

    # ЯМ финансы
    if ym_revenue_data:
        lines.append("\nЯндекс Маркет — вознаграждение по месяцам:")
        for pvz_name, months_list in ym_revenue_data.items():
            lines.append(f"  ПВЗ: {pvz_name}")
            for p in months_list:
                fines_note = f" (штрафы: -{p['fines']:,.0f})" if p.get("fines") else ""
                lines.append(f"    {p['period']}: {p['revenue']:,.0f} руб.{fines_note}")
            if len(months_list) >= 2:
                diff = months_list[0]["revenue"] - months_list[-1]["revenue"]
                trend = f"рост +{diff:,.0f} руб." if diff > 0 else f"падение {diff:,.0f} руб."
                lines.append(f"    Тренд: {trend}")

    if ym_fines_data:
        lines.append(f"\nЯндекс Маркет — штрафы за {MONTHS_RU[month]} {year}:")
        for pvz_name, fdata in ym_fines_data.items():
            lines.append(f"  {pvz_name}: -{fdata['total']:,.0f} руб.")
            for it in fdata["items"]:
                lines.append(f"    • {it['date']} {it['reason']}: -{it['amount']:,.0f} руб.")

    if ym_turnover_data:
        lines.append("\nЯндекс Маркет — оборот по месяцам:")
        for pvz_name, months_list in ym_turnover_data.items():
            lines.append(f"  ПВЗ: {pvz_name}")
            for p in months_list:
                lines.append(f"    {p['period']}: {p['turnover']:,.0f} руб.")

    # Расходы
    rent = expenses.get("rent", 0)
    salary = expenses.get("salary", 0)
    utilities = expenses.get("utilities", 0)
    turnover = expenses.get("turnover", 0)
    total_expenses = rent + salary + utilities

    lines += [
        "",
        "РАСХОДЫ:",
        f"  Аренда: {rent:,.0f} руб./мес.",
        f"  ФОТ: {salary:,.0f} руб./мес.",
        f"  Коммуналка: {utilities:,.0f} руб./мес.",
        f"  Товарооборот: {turnover:,.0f} руб./мес.",
        f"  Итого расходов: {total_expenses:,.0f} руб./мес.",
    ]

    # Расчёт чистой прибыли
    total_ozon_revenue = sum(
        months[0]["revenue"] for months in ozon_profit_data.values() if months
    )
    total_ozon_tax = sum(
        months[0]["tax"] for months in ozon_profit_data.values() if months
    )
    total_ozon_fines = sum(
        months[0]["fines"] for months in ozon_profit_data.values() if months
    )
    total_ym_revenue = sum(
        months[0]["revenue"] for months in ym_revenue_data.values() if months
    )
    total_gross = total_ozon_revenue + total_ym_revenue
    total_deductions = total_ozon_tax + total_ozon_fines
    net_profit = total_gross - total_deductions - total_expenses

    lines += [
        "",
        "РАСЧЁТ (последний месяц):",
        f"  Суммарная выручка (Ozon + ЯМ): {total_gross:,.0f} руб.",
        f"  Налоги: -{total_ozon_tax:,.0f} руб.",
        f"  Штрафы/претензии: -{total_ozon_fines:,.0f} руб.",
        f"  Расходы: -{total_expenses:,.0f} руб.",
        f"  Чистая прибыль: {net_profit:,.0f} руб.",
        "",
        "ФОРМАТ ОТВЕТА (строго, без воды, до 250 слов):",
        "🎯 Вердикт: [держать / развивать / продавать и почему]",
        "💰 Финансы: [доходность, рентабельность с учётом расходов, тренд]",
        "📦 Трафик: [поток, сравнение с регионом]",
        "⚠️ Риски: [конкретный список]",
        "💵 Оценка бизнеса: [N × месячная чистая прибыль, где N обоснуй]",
    ]

    return "\n".join(lines)
