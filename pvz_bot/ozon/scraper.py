from datetime import datetime, timedelta
from typing import Optional
from ozon.http_client import post, get
from db.database import upsert_claim

CLAIMS_API = "https://turbo-pvz.ozon.ru/api2/claims/v1/Claims"
STORES_API = "https://turbo-pvz.ozon.ru/api2/pick-up-point-profile/pick-up-points/details"
CLAIMS_ANALYTICS_URL = "https://turbo-pvz.ozon.ru/api2/analytics/claims/general-info"

CLAIM_TYPE_RU = {
    "Loss": "Утеря",
    "AgentReturns": "Возврат агента",
    "Penalty": "Штраф",
    "Claim": "Претензия",
    "Damage": "Повреждение",
    "Shortage": "Недостача",
}

STATUS_RU = {
    "ResponseRequired": "Требует ответа",
    "PaymentRequired": "Требует оплаты",
    "UnderReview": "На рассмотрении",
    "Closed": "Закрыта",
}

_pvz_cache: dict = {}
_stores_cache: list = []

# Известные магазины — fallback если API недоступен
_KNOWN_STORES = [
    {"id": 1020002440339000, "name": "ВНУКОВСКОЕ_28"},
    {"id": 1020000822262000, "name": "РОСТОВ-НА-ДОНУ_622"},
]


async def _get_all_stores() -> list:
    """Возвращает список магазинов с id и name."""
    global _stores_cache, _pvz_cache
    if _stores_cache:
        return _stores_cache
    try:
        data = await get(STORES_API)
        stores = data.get("stores", [])
        if stores and "reason" not in data:
            for s in stores:
                pid = s.get("id")
                name = s.get("name") or str(pid)
                if pid:
                    _pvz_cache[str(pid)] = name
            _stores_cache = stores
            return _stores_cache
    except Exception:
        pass
    # Fallback: используем известные магазины
    for s in _KNOWN_STORES:
        _pvz_cache[str(s["id"])] = s["name"]
    _stores_cache = _KNOWN_STORES
    return _stores_cache


async def _get_pvz_map() -> dict:
    """Возвращает словарь {pickPointId: pvzName}"""
    await _get_all_stores()
    return _pvz_cache


def _deadline_from_minutes(minutes: int) -> Optional[str]:
    if minutes is None:
        return None
    return (datetime.now() + timedelta(minutes=minutes)).isoformat()


async def scrape_claims() -> list:
    """Получает активные претензии через Ozon API и сохраняет в БД."""
    pvz_map = await _get_pvz_map()

    body = {
        "claimStatuses": ["ResponseRequired", "PaymentRequired"],
        "limit": 50,
        "from": None,
        "to": None,
        "requestTypes": [],
        "pickPointIds": [],
    }

    data = await post(CLAIMS_API, body)
    raw_claims = data.get("claims", [])

    claims = []
    for c in raw_claims:
        pick_id = str(c.get("pickPointId", ""))
        pvz_name = pvz_map.get(pick_id, pick_id)
        claim_type_en = c.get("claimType", "")

        claim = {
            "id": str(c["claimId"]),
            "pvz": pvz_name,
            "claim_type": CLAIM_TYPE_RU.get(claim_type_en, claim_type_en),
            "reason": CLAIM_TYPE_RU.get(claim_type_en, claim_type_en),
            "amount": c.get("amount", {}).get("decimalValue", 0),
            "date_issued": c.get("createdAt"),
            "deadline": _deadline_from_minutes(c.get("deadLine")),
            "status": STATUS_RU.get(c.get("status", ""), c.get("status", "")),
        }
        claims.append(claim)
        await upsert_claim(claim)

    return claims


async def get_claims_deductible(period_from: str, period_to: str) -> tuple:
    """
    Возвращает (fines_total, {pvz_name: enrollFromAb}) — суммы к вычету из вознаграждения
    по каждой ПВЗ за период, используя аналитический API.
    """
    stores = await _get_all_stores()
    fines_by_pvz = {}
    fines_total = 0.0

    for store in stores:
        store_id = store.get("id")
        pvz_name = store.get("name", str(store_id))
        try:
            data = await post(CLAIMS_ANALYTICS_URL, {
                "storeIds": [store_id],
                "periodFrom": period_from,
                "periodTo": period_to,
            })
            enroll = data.get("financialSummary", {}).get("enrollFromAb", 0) or 0
            fines_by_pvz[pvz_name] = enroll
            fines_total += enroll
        except Exception:
            fines_by_pvz[pvz_name] = 0

    return fines_total, fines_by_pvz


async def get_monthly_stats(month: int = None, year: int = None) -> dict:
    """
    Возвращает финансовую статистику за месяц:
    - выручка (вознаграждение из PDF-отчёта)
    - налог УСН 12%
    - штрафы к вычету из вознаграждения (из аналитики претензий)
    - чистая прибыль = выручка − налог − штрафы
    """
    from config import get_tax_rate
    from datetime import date

    today = date.today()
    if not month:
        month = today.month - 1 if today.month > 1 else 12
        year = today.year if today.month > 1 else today.year - 1
    if not year:
        year = today.year

    # Получаем список отчётов
    date_from = f"{year - 1}-01-01"
    date_to = today.isoformat()
    reports_data = await get(
        f"https://turbo-pvz.ozon.ru/api2/reports/agent/reports-by-contract-ids"
        f"?reportType=DocumentsAgentReport&dateFrom={date_from}&dateTo={date_to}"
    )
    reports = reports_data.get("reports", [])

    # Ищем отчёт за нужный месяц
    target_report = None
    for r in reports:
        begin = r.get("beginDate", "")
        if begin:
            r_year, r_month = int(begin[:4]), int(begin[5:7])
            if r_year == year and r_month == month and r.get("state") == "Accepted":
                target_report = r
                break

    if not target_report:
        for r in reports:
            if r.get("state") == "Accepted":
                target_report = r
                break

    if not target_report:
        return {"error": "Нет утверждённых отчётов"}

    report_id = target_report.get("id") or target_report.get("reportId")
    revenue = target_report.get("amount", 0)
    begin_date = target_report.get("beginDate", "")[:10]
    end_date = target_report.get("endDate", "")[:10]
    period_name = target_report.get("name", "")
    report_year = int(begin_date[:4]) if begin_date else year
    TAX_RATE = get_tax_rate(report_year)

    # Штрафы к вычету из вознаграждения по каждой ПВЗ
    fines_total, fines_by_pvz = await get_claims_deductible(begin_date, end_date)

    # Скачиваем PDF для разбивки выручки по ПВЗ
    pvz_revenue = {}
    if report_id:
        try:
            from ozon.pdf_parser import download_and_parse_pdf
            pvz_revenue = await download_and_parse_pdf(str(report_id), total_revenue=revenue)
        except Exception as e:
            pvz_revenue = {"_error": str(e)}

    tax = round(revenue * TAX_RATE, 2)
    profit = round(revenue - tax - fines_total, 2)

    return {
        "period": period_name,
        "begin_date": begin_date,
        "end_date": end_date,
        "revenue": revenue,
        "tax": tax,
        "tax_rate": TAX_RATE,
        "fines_total": fines_total,
        "fines_by_pvz": fines_by_pvz,
        "profit": profit,
        "pvz_revenue": pvz_revenue,
    }


async def get_available_reports() -> list:
    """Возвращает список утверждённых отчётов [{month, year, label}]"""
    from datetime import date
    today = date.today()
    date_from = f"{today.year - 1}-01-01"
    date_to = today.isoformat()
    try:
        data = await get(
            f"https://turbo-pvz.ozon.ru/api2/reports/agent/reports-by-contract-ids"
            f"?reportType=DocumentsAgentReport&dateFrom={date_from}&dateTo={date_to}"
        )
    except Exception as e:
        if "403" in str(e):
            raise RuntimeError(
                "Отчёты Ozon недоступны с текущим токеном.\n"
                "Запусти /login чтобы войти снова."
            ) from None
        raise
    reports = data.get("reports", [])
    result = []
    for r in reports:
        if r.get("state") != "Accepted":
            continue
        begin = r.get("beginDate", "")
        if not begin:
            continue
        year, month = int(begin[:4]), int(begin[5:7])
        MONTHS_RU = ["", "Янв", "Фев", "Мар", "Апр", "Май", "Июн",
                     "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
        label = f"{MONTHS_RU[month]} {year}"
        result.append({"month": month, "year": year, "label": label})
    return result


async def get_pvz_stats() -> dict:
    return {}
