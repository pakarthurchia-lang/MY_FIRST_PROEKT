"""
Аналитика ПВЗ из Ozon API.
"""
from datetime import date, timedelta
from ozon.http_client import get
from ozon.scraper import _get_all_stores

ANALYTICS_BASE = "https://turbo-pvz.ozon.ru/api2/analytics/analytics"


async def get_store_analytics(store_id: int, store_name: str) -> dict:
    """Собирает аналитику по одной ПВЗ за текущую неделю/месяц."""
    today = date.today()
    week_start = (today - timedelta(days=6)).isoformat()
    week_end = today.isoformat()

    result = {"name": store_name, "store_id": store_id}

    # Принятые посылки за неделю
    try:
        data = await get(
            f"{ANALYTICS_BASE}/received-postings",
            params={"selectedStoreId": store_id, "startDate": week_start, "endDate": week_end}
        )
        for series in data.get("series", []):
            if series.get("label") == "Всего":
                result["received_total"] = sum(series["yAxisValues"])
                result["received_daily"] = series["yAxisValues"]
                result["received_days"] = data.get("xAxisValues", [])
                break
    except Exception:
        result["received_total"] = None

    # Уникальные клиенты (последние недели)
    try:
        data = await get(
            f"{ANALYTICS_BASE}/store-unique-clients",
            params={"selectedStoreId": store_id}
        )
        for series in data.get("series", []):
            if series.get("type") == "UniqueClientsSelectedPvz":
                values = series["yAxisValues"]
                result["unique_clients_last"] = values[-1] if values else None
                result["unique_clients_prev"] = values[-2] if len(values) > 1 else None
                break
    except Exception:
        result["unique_clients_last"] = None

    # Частота заказов
    try:
        data = await get(
            f"{ANALYTICS_BASE}/store-frequency-of-orders",
            params={"selectedStoreId": store_id}
        )
        pvz_vals, region_vals = [], []
        for series in data.get("series", []):
            if series.get("type") == "FrequencyOfOrdersSelectedPvz":
                pvz_vals = series["yAxisValues"]
            elif series.get("type") == "FrequencyOfOrdersAverageByRegion":
                region_vals = series["yAxisValues"]
        result["frequency"] = pvz_vals[-1] if pvz_vals else None
        result["frequency_region"] = region_vals[-1] if region_vals else None
    except Exception:
        result["frequency"] = None

    # Рейтинг
    try:
        data = await get(
            f"{ANALYTICS_BASE}/store-rating",
            params={"selectedStoreId": store_id}
        )
        result["rating"] = data.get("rating")
        result["rating_delta"] = data.get("delta")
    except Exception:
        result["rating"] = None

    return result


async def get_all_pvz_analytics() -> list:
    """Возвращает аналитику по всем ПВЗ."""
    stores = await _get_all_stores()

    results = []
    for store in stores:
        store_id = store.get("id")
        store_name = store.get("name", str(store_id))
        analytics = await get_store_analytics(store_id, store_name)
        results.append(analytics)
    return results
