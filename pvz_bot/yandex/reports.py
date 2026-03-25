"""
Скачивание месячных отчётов Яндекс Маркет.

Эндпоинт:
  GET https://hubs.market.yandex.ru/api/partner-gateway/{partner_id}/reports/download-lp-billing
  Параметры:
    date={year}-{month}-{last_day}  — последний день нужного месяца
    reportType=DETAIL               — детализация акта услуг (XLSX с разбивкой по ПВЗ)
"""

import calendar
from yandex.http_client import get_bytes, PARTNER_ID

BASE_URL = "https://hubs.market.yandex.ru"
DOWNLOAD_URL = f"{BASE_URL}/api/partner-gateway/{PARTNER_ID}/reports/download-lp-billing"

MONTHS_RU = ["", "Янв", "Фев", "Мар", "Апр", "Май", "Июн",
             "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]


def _last_day_of_month(year: int, month: int) -> str:
    """Возвращает последний день месяца в формате YYYY-MM-DD."""
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-{last_day:02d}"


async def download_report_xlsx(month: int, year: int) -> bytes:
    """Скачивает XLSX детализации за указанный месяц/год."""
    date_str = _last_day_of_month(year, month)
    return await get_bytes(DOWNLOAD_URL, params={"date": date_str, "reportType": "DETAIL"})


def available_months_for_menu(count: int = 6) -> list:
    """
    Возвращает последние N месяцев для меню выбора.
    Формат: [{"month": 1, "year": 2026, "label": "Янв 2026"}]
    """
    from datetime import date
    today = date.today()
    result = []
    m, y = today.month - 1, today.year
    if m == 0:
        m, y = 12, y - 1
    for _ in range(count):
        result.append({"month": m, "year": y, "label": f"{MONTHS_RU[m]} {y}"})
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return result
