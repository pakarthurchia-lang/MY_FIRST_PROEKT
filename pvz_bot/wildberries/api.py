"""
WB ПВЗ API — получение выплат из point-balance.wb.ru.

Эндпоинт: GET /s3/api/v2/partner-payments
Параметры: limit, offset, pickpoint_id

Структура ответа (одна неделя):
  date_from, date_to    — период выплаты (понедельно)
  base_accrued          — реализация услуг (основное вознаграждение)
  other_accrued         — прочие начисления (отрицательное = удержания/штрафы)
  total                 — итого к выплате = base_accrued + other_accrued
  total_turnover.base   — товарооборот за период

Данные агрегируются по календарному месяцу (по date_to).
"""
from datetime import date, datetime
from typing import Optional
from wildberries.http_client import get, get_pickpoint_id

PAYMENTS_URL = "https://point-balance.wb.ru/s3/api/v2/partner-payments"

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


async def _fetch_payments_page(pickpoint_id: int, limit: int = 50, offset: int = 0) -> list:
    """Загружает одну страницу выплат."""
    data = await get(PAYMENTS_URL, params={
        "limit": limit,
        "offset": offset,
        "pickpoint_id": pickpoint_id,
    })
    return data.get("payments", [])


async def fetch_all_payments(pickpoint_id: int) -> list:
    """
    Загружает ВСЕ недельные выплаты (постранично, без ограничений).
    """
    all_payments = []
    offset = 0
    page_size = 50

    while True:
        batch = await _fetch_payments_page(pickpoint_id, limit=page_size, offset=offset)
        if not batch:
            break
        all_payments.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    return all_payments


def _parse_week(payment: dict) -> dict:
    """
    Разбирает одну недельную запись.
    Возвращает {date_from, date_to, revenue, fines, net, turnover, orders}.
    """
    base = payment.get("base_accrued", 0) or 0
    expensive = payment.get("expensive_accrued", 0) or 0
    courier = payment.get("courier_accrued", 0) or 0
    other = payment.get("other_accrued", 0) or 0
    fines = abs(other) if other < 0 else 0
    bonuses = other if other > 0 else 0
    revenue = base + expensive + courier + bonuses  # все положительные начисления

    # Количество выдач — из category 1, operation id 1
    orders = 0
    for cat in payment.get("total_transactions", []):
        if cat.get("id") == 1:
            for op in cat.get("operations", []):
                if op.get("id") == 1:
                    orders = op.get("count", 0)
            break

    turnover = (payment.get("total_turnover") or {}).get("base", 0) or 0

    return {
        "date_from": payment["date_from"],
        "date_to": payment["date_to"],
        "revenue": round(revenue, 2),
        "fines": round(fines, 2),
        "net": round(payment.get("total", 0) or 0, 2),
        "turnover": round(turnover, 2),
        "orders": orders,
    }


def aggregate_by_month(payments: list) -> dict:
    """
    Группирует недельные выплаты по календарному месяцу (по date_to).
    Возвращает {(month, year): {revenue, fines, net, turnover, orders, weeks}}.
    """
    result = {}
    for p in payments:
        week = _parse_week(p)
        try:
            dt = datetime.strptime(week["date_to"], "%Y-%m-%d")
        except (ValueError, KeyError):
            continue
        key = (dt.month, dt.year)
        if key not in result:
            result[key] = {
                "revenue": 0.0, "fines": 0.0, "net": 0.0,
                "turnover": 0.0, "orders": 0, "weeks": 0,
            }
        result[key]["revenue"] += week["revenue"]
        result[key]["fines"] += week["fines"]
        result[key]["net"] += week["net"]
        result[key]["turnover"] += week["turnover"]
        result[key]["orders"] += week["orders"]
        result[key]["weeks"] += 1

    # Округляем
    for v in result.values():
        v["revenue"] = round(v["revenue"], 2)
        v["fines"] = round(v["fines"], 2)
        v["net"] = round(v["net"], 2)
        v["turnover"] = round(v["turnover"], 2)

    return result


async def get_monthly_data(month: int, year: int) -> Optional[dict]:
    """
    Загружает и возвращает данные за конкретный месяц.
    Возвращает {revenue, fines, net, turnover, orders, address} или None.
    """
    pickpoint_id = get_pickpoint_id()
    if not pickpoint_id:
        raise RuntimeError("pickpoint_id не найден — обнови WB токен")

    payments = await fetch_all_payments(pickpoint_id)
    by_month = aggregate_by_month(payments)
    data = by_month.get((month, year))
    if not data:
        return None

    # Адрес из первой записи
    address = None
    for p in payments:
        pp = (p.get("pickpoint_payments") or [])
        if pp:
            address = pp[0].get("address")
            break

    return {**data, "address": address, "pickpoint_id": pickpoint_id}


async def get_available_months(n: int = 6) -> list:
    """
    Возвращает список месяцев за которые есть данные WB (свежие первые).
    [{month, year, label, revenue, net}]
    """
    pickpoint_id = get_pickpoint_id()
    if not pickpoint_id:
        return []

    payments = await fetch_all_payments(pickpoint_id)
    by_month = aggregate_by_month(payments)

    result = []
    for (m, y), data in sorted(by_month.items(), key=lambda x: (x[0][1], x[0][0]), reverse=True):
        result.append({
            "month": m,
            "year": y,
            "label": f"{MONTHS_RU[m][:3]} {y}",
            "revenue": data["revenue"],
            "net": data["net"],
        })
    return result[:n]
