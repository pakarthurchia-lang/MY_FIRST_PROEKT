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
    wb_pvzs = [p for p in location["pvzs"] if p["platform"] == "wb"]

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

    # ── Wildberries данные ─────────────────────────────────────────────────
    wb_revenue_data: Dict[str, List] = {}  # pvz_name -> list of monthly dicts

    if wb_pvzs:
        try:
            from db.database import get_wb_monthly_history
            for pvz in wb_pvzs:
                name = pvz["pvz_name"]
                history = await get_wb_monthly_history(name, n=6)
                if history:
                    wb_revenue_data[name] = history
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
        wb_revenue_data=wb_revenue_data,
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


async def get_pvz_diagnostics(location: dict) -> str:
    """
    Собирает все данные по претензиям, штрафам и аналитике и просит Claude
    найти систематические нарушения с конкретными рекомендациями.
    """
    ozon_pvzs = [p for p in location["pvzs"] if p["platform"] == "ozon"]
    ym_pvzs   = [p for p in location["pvzs"] if p["platform"] == "ym"]
    wb_pvzs   = [p for p in location["pvzs"] if p["platform"] == "wb"]

    pvz_names = [p["pvz_name"] for p in location["pvzs"]]

    # ── Претензии из БД ────────────────────────────────────────────────────
    from db.database import get_claims_history
    claims = await get_claims_history(pvz_names, limit=100)

    # ── Ozon: аналитика и тренд штрафов по месяцам ────────────────────────
    ozon_analytics: Dict[str, dict] = {}
    ozon_fines_trend: List[dict] = []

    if ozon_pvzs:
        try:
            from ozon.scraper import get_available_reports, get_monthly_stats
            from ozon.analytics import get_store_analytics
            reports = await get_available_reports()
            for r in sorted(reports, key=lambda x: (x["year"], x["month"]), reverse=True)[:12]:
                try:
                    stats = await get_monthly_stats(r["month"], r["year"])
                    total_fines = sum(stats.get("fines_by_pvz", {}).get(p["pvz_name"], 0)
                                      for p in ozon_pvzs)
                    if total_fines > 0:
                        ozon_fines_trend.append({
                            "period": r["label"], "fines": total_fines,
                        })
                except Exception:
                    pass
            for pvz in ozon_pvzs:
                try:
                    store_id = int(pvz["pvz_id"]) if pvz.get("pvz_id") else None
                    if store_id:
                        ozon_analytics[pvz["pvz_name"]] = await get_store_analytics(
                            store_id, pvz["pvz_name"]
                        )
                except Exception:
                    pass
        except Exception:
            pass

    # ── WB: история штрафов/удержаний ─────────────────────────────────────
    wb_fines_history: Dict[str, List] = {}
    if wb_pvzs:
        try:
            from db.database import get_wb_monthly_history
            for pvz in wb_pvzs:
                history = await get_wb_monthly_history(pvz["pvz_name"], n=12)
                if history:
                    wb_fines_history[pvz["pvz_name"]] = [
                        h for h in history if h.get("fines", 0) > 0
                    ]
        except Exception:
            pass

    # ── ЯМ: штрафы ────────────────────────────────────────────────────────
    ym_fines_history: Dict[str, List] = {}
    if ym_pvzs:
        try:
            from yandex.reports import download_report_xlsx, available_months_for_menu
            from yandex.xlsx_parser import parse_ym_fines
            for m in available_months_for_menu(6):
                try:
                    xlsx = await download_report_xlsx(m["month"], m["year"])
                    fines = parse_ym_fines(xlsx, m["month"], m["year"])
                    for pvz in ym_pvzs:
                        name = pvz["pvz_name"]
                        if name in fines and fines[name]["total"] > 0:
                            ym_fines_history.setdefault(name, []).append({
                                "period": m["label"],
                                **fines[name],
                            })
                except Exception:
                    pass
        except Exception:
            pass

    prompt = _build_diagnostics_prompt(
        location, claims, ozon_analytics, ozon_fines_trend,
        wb_fines_history, ym_fines_history,
    )

    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


async def get_pvz_valuation(location: dict, expenses: dict, month: int, year: int) -> str:
    """
    Собирает данные за максимально доступный период и рассчитывает рыночную
    стоимость ПВЗ: чистая прибыль × мультипликатор 6–12.
    """
    from ozon.scraper import get_available_reports, get_monthly_stats
    from ozon.analytics import get_store_analytics

    ozon_pvzs = [p for p in location["pvzs"] if p["platform"] == "ozon"]
    ym_pvzs   = [p for p in location["pvzs"] if p["platform"] == "ym"]
    wb_pvzs   = [p for p in location["pvzs"] if p["platform"] == "wb"]

    # ── Ozon: до 12 месяцев ────────────────────────────────────────────────
    ozon_monthly: List[dict] = []
    ozon_analytics: Dict[str, dict] = {}

    if ozon_pvzs:
        try:
            reports = await get_available_reports()
            def _dist(r):
                return abs((r["year"] - year) * 12 + (r["month"] - month))
            for r in sorted(reports, key=_dist)[:12]:
                try:
                    stats = await get_monthly_stats(r["month"], r["year"])
                    total_rev  = sum(stats.get("pvz_revenue", {}).get(p["pvz_name"], 0) for p in ozon_pvzs)
                    total_fine = sum(stats.get("fines_by_pvz", {}).get(p["pvz_name"], 0) for p in ozon_pvzs)
                    if total_rev > 0:
                        tax = round(total_rev * stats["tax_rate"], 2)
                        ozon_monthly.append({
                            "period": r["label"],
                            "month": r["month"], "year": r["year"],
                            "revenue": total_rev,
                            "tax": tax,
                            "fines": total_fine,
                            "profit": round(total_rev - tax - total_fine, 2),
                        })
                except Exception:
                    pass
            for pvz in ozon_pvzs:
                try:
                    store_id = int(pvz["pvz_id"]) if pvz.get("pvz_id") else None
                    if store_id:
                        ozon_analytics[pvz["pvz_name"]] = await get_store_analytics(
                            store_id, pvz["pvz_name"]
                        )
                except Exception:
                    pass
        except Exception:
            pass

    # ── ЯМ: до 12 месяцев ─────────────────────────────────────────────────
    ym_monthly: Dict[str, List] = {}
    if ym_pvzs:
        try:
            from yandex.reports import download_report_xlsx, available_months_for_menu
            from yandex.xlsx_parser import parse_ym_xlsx
            for m in available_months_for_menu(12):
                try:
                    xlsx = await download_report_xlsx(m["month"], m["year"])
                    data = parse_ym_xlsx(xlsx)
                    for pvz in ym_pvzs:
                        name = pvz["pvz_name"]
                        rev = data.get(name)
                        if rev is not None:
                            ym_monthly.setdefault(name, []).append({"period": m["label"], "revenue": rev})
                except Exception:
                    pass
        except Exception:
            pass

    # ── WB: до 12 месяцев ─────────────────────────────────────────────────
    wb_monthly: Dict[str, List] = {}
    if wb_pvzs:
        try:
            from db.database import get_wb_monthly_history
            for pvz in wb_pvzs:
                history = await get_wb_monthly_history(pvz["pvz_name"], n=12)
                if history:
                    wb_monthly[pvz["pvz_name"]] = history
        except Exception:
            pass

    prompt = _build_valuation_prompt(
        location, expenses, month, year,
        ozon_monthly, ozon_analytics, ym_monthly, wb_monthly,
    )

    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _build_diagnostics_prompt(
    location: dict,
    claims: list,
    ozon_analytics: dict,
    ozon_fines_trend: list,
    wb_fines_history: dict,
    ym_fines_history: dict,
) -> str:
    lines = [
        "Ты эксперт по операционному управлению ПВЗ маркетплейсов.",
        "Проанализируй данные о штрафах и претензиях и найди КОНКРЕТНЫЕ систематические нарушения.",
        "НЕ используй общие фразы. Называй конкретный вид нарушения, его частоту, денежный ущерб и точные шаги для исправления.",
        f"\nЛокация: {location['name']}",
    ]

    # Претензии из БД
    if claims:
        from collections import Counter
        type_counts = Counter(c.get("claim_type", "?") for c in claims)
        type_amounts = {}
        for c in claims:
            t = c.get("claim_type", "?")
            type_amounts[t] = type_amounts.get(t, 0) + (c.get("amount") or 0)

        lines.append(f"\nПРЕТЕНЗИИ ИЗ БАЗЫ ({len(claims)} шт.):")
        for t, cnt in type_counts.most_common():
            lines.append(f"  {t}: {cnt} шт. на сумму {type_amounts.get(t, 0):,.0f} руб.")

        # Последние 10 претензий с деталями
        lines.append("\nПоследние претензии:")
        for c in claims[:10]:
            lines.append(
                f"  {c.get('date_issued', '?')[:10]} | {c.get('pvz', '?')} | "
                f"{c.get('claim_type', '?')} | {c.get('amount', 0):,.0f} руб. | {c.get('status', '?')}"
            )
    else:
        lines.append("\nПретензии в базе: не найдены (возможно, нет активных)")

    # Тренд штрафов Ozon
    if ozon_fines_trend:
        lines.append("\nOzon — штрафы по месяцам:")
        for item in ozon_fines_trend[:6]:
            lines.append(f"  {item['period']}: {item['fines']:,.0f} руб.")

    # Аналитика Ozon
    if ozon_analytics:
        lines.append("\nOzon — рейтинг и частота:")
        for pvz_name, a in ozon_analytics.items():
            lines.append(
                f"  {pvz_name}: рейтинг {a.get('rating', '—')}, "
                f"частота {a.get('frequency', '—')} (регион: {a.get('frequency_region', '—')}), "
                f"выдач за неделю: {a.get('received_total', '—')}"
            )

    # WB штрафы
    if wb_fines_history:
        lines.append("\nWildberries — удержания по месяцам:")
        for pvz_name, history in wb_fines_history.items():
            lines.append(f"  {pvz_name}:")
            for h in history[:6]:
                lines.append(f"    {h['period']}: -{h['fines']:,.0f} руб. ({h.get('orders', 0)} выдач)")

    # ЯМ штрафы
    if ym_fines_history:
        lines.append("\nЯндекс Маркет — штрафы:")
        for pvz_name, history in ym_fines_history.items():
            lines.append(f"  {pvz_name}:")
            for h in history[:6]:
                lines.append(f"    {h['period']}: -{h['total']:,.0f} руб.")
                for item in h.get("items", [])[:3]:
                    lines.append(f"      • {item.get('date', '')} {item.get('reason', '')}: -{item.get('amount', 0):,.0f} руб.")

    lines += [
        "",
        "ЗАДАЧА: найди КОНКРЕТНЫЕ систематические нарушения и причины потерь прибыли.",
        "",
        "ФОРМАТ ОТВЕТА (до 300 слов, без воды):",
        "🔍 Главные проблемы: [топ-3 нарушения с суммой ущерба]",
        "📌 По каждой проблеме:",
        "   — Вид нарушения и как часто повторяется",
        "   — Вероятная причина (например: конкретный сотрудник в конкретную смену, процессная ошибка при приёмке)",
        "   — Конкретное решение (что именно сделать, например: ввести видеофиксацию вскрытия, изменить инструкцию приёмки возвратов)",
        "   — Ожидаемое снижение штрафов в руб./мес.",
        "⚡ Быстрые победы: что можно исправить за неделю",
    ]

    return "\n".join(lines)


def _build_valuation_prompt(
    location: dict,
    expenses: dict,
    month: int,
    year: int,
    ozon_monthly: list,
    ozon_analytics: dict,
    ym_monthly: dict,
    wb_monthly: dict,
) -> str:
    MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                 "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

    rent      = expenses.get("rent", 0)
    salary    = expenses.get("salary", 0)
    utilities = expenses.get("utilities", 0)
    turnover  = expenses.get("turnover", 0)
    total_exp = rent + salary + utilities

    lines = [
        "Ты эксперт по оценке бизнеса ПВЗ маркетплейсов.",
        "Рассчитай рыночную стоимость локации ПВЗ на основе финансовых данных.",
        f"\nЛокация: {location['name']}",
        f"Базовый месяц: {MONTHS_RU[month]} {year}",
        "",
        "ФИНАНСОВЫЕ ДАННЫЕ:",
    ]

    # Ozon по месяцам
    if ozon_monthly:
        lines.append("\nOzon — вознаграждение (до 12 мес.):")
        for p in ozon_monthly:
            lines.append(
                f"  {p['period']}: выручка {p['revenue']:,.0f} → чистая {p['profit']:,.0f} руб."
                f" (налог: {p['tax']:,.0f}, штрафы: {p['fines']:,.0f})"
            )
        avg_ozon = sum(p["profit"] for p in ozon_monthly) / len(ozon_monthly)
        lines.append(f"  Среднемесячная чистая Ozon: {avg_ozon:,.0f} руб.")

    # ЯМ по месяцам
    if ym_monthly:
        lines.append("\nЯндекс Маркет — вознаграждение:")
        for pvz_name, months_list in ym_monthly.items():
            lines.append(f"  {pvz_name}:")
            for p in months_list[:12]:
                lines.append(f"    {p['period']}: {p['revenue']:,.0f} руб.")
            if months_list:
                avg = sum(p["revenue"] for p in months_list) / len(months_list)
                lines.append(f"    Среднемесячная: {avg:,.0f} руб.")

    # WB по месяцам
    if wb_monthly:
        lines.append("\nWildberries — вознаграждение:")
        for pvz_name, months_list in wb_monthly.items():
            lines.append(f"  {pvz_name}:")
            for p in months_list[:12]:
                fines_note = f" (удержания: -{p['fines']:,.0f})" if p.get("fines") else ""
                orders_note = f" [{p['orders']} выдач]" if p.get("orders") else ""
                lines.append(f"    {p['period']}: {p['revenue']:,.0f} руб.{fines_note}{orders_note}")
            if months_list:
                avg = sum(p["revenue"] for p in months_list) / len(months_list)
                lines.append(f"    Среднемесячная: {avg:,.0f} руб.")

    # Аналитика
    if ozon_analytics:
        lines.append("\nOzon аналитика (трафик и рейтинг):")
        for pvz_name, a in ozon_analytics.items():
            lines.append(
                f"  {pvz_name}: рейтинг {a.get('rating', '—')}, "
                f"частота {a.get('frequency', '—')} vs регион {a.get('frequency_region', '—')}, "
                f"выдач за неделю: {a.get('received_total', '—')}"
            )

    # Расходы
    lines += [
        "",
        "РАСХОДЫ (ежемесячные):",
        f"  Аренда: {rent:,.0f} руб.",
        f"  ФОТ: {salary:,.0f} руб.",
        f"  Коммуналка: {utilities:,.0f} руб.",
        f"  Товарооборот: {turnover:,.0f} руб.",
        f"  Итого расходов: {total_exp:,.0f} руб.",
    ]

    lines += [
        "",
        "ЗАДАЧА: рассчитай рыночную стоимость ПВЗ.",
        "",
        "ФОРМАТ ОТВЕТА (до 300 слов):",
        "📊 Финансовый расчёт:",
        "   — Средняя выручка со всех платформ (Ozon + ЯМ + WB): X руб./мес.",
        "   — Минус расходы (аренда + ФОТ + коммуналка): X руб./мес.",
        "   — Чистая прибыль: X руб./мес.",
        "   — Годовая чистая прибыль: X руб.",
        "💰 Оценка бизнеса:",
        "   — Диапазон стоимости: от X (6 мес.) до X (12 мес.)",
        "   — Обоснованная цена: X руб. (N мес.) — объясни почему именно N",
        "⚖️ Факторы влияния на мультипликатор (что увеличивает/уменьшает цену):",
        "   — Плюсы: [конкретные сильные стороны из данных]",
        "   — Риски: [конкретные риски из данных]",
        "🎯 Совет владельцу: [продавать / держать / как увеличить стоимость перед продажей]",
    ]

    return "\n".join(lines)


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
    wb_revenue_data: dict,
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

    # WB финансы
    if wb_revenue_data:
        lines.append("\nWildberries — вознаграждение по месяцам:")
        for pvz_name, months_list in wb_revenue_data.items():
            lines.append(f"  ПВЗ: {pvz_name}")
            for p in months_list:
                fines_note = f" (удержания: -{p['fines']:,.0f})" if p.get("fines") else ""
                orders_note = f" [{p['orders']} выдач]" if p.get("orders") else ""
                lines.append(f"    {p['period']}: {p['revenue']:,.0f} руб.{fines_note}{orders_note}")
            if len(months_list) >= 2:
                diff = months_list[0]["revenue"] - months_list[-1]["revenue"]
                trend = f"рост +{diff:,.0f} руб." if diff > 0 else f"падение {diff:,.0f} руб."
                lines.append(f"    Тренд: {trend}")

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
    total_wb_revenue = sum(
        months[0]["revenue"] for months in wb_revenue_data.values() if months
    )
    total_gross = total_ozon_revenue + total_ym_revenue + total_wb_revenue
    total_deductions = total_ozon_tax + total_ozon_fines
    net_profit = total_gross - total_deductions - total_expenses

    lines += [
        "",
        "РАСЧЁТ (последний месяц):",
        f"  Суммарная выручка (Ozon + ЯМ + WB): {total_gross:,.0f} руб.",
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
