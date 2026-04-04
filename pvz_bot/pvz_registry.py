"""
Реестр ПВЗ по всем платформам.
Хранится в data/pvz_registry.json и обновляется автоматически
при каждом успешном получении данных с платформы.

Формат:
{
  "ozon": [{"pvz_id": "123", "pvz_name": "Название"}],
  "wb":   [{"pvz_id": "50016046", "pvz_name": "Адрес ПВЗ"}],
  "ym":   [{"pvz_id": null, "pvz_name": "Название из XLSX"}]
}
"""
import json
import os

REGISTRY_FILE = "data/pvz_registry.json"


def _load() -> dict:
    try:
        with open(REGISTRY_FILE) as f:
            return json.load(f)
    except Exception:
        return {"ozon": [], "wb": [], "ym": []}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
    with open(REGISTRY_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_all() -> dict:
    return _load()


def update_platform(platform: str, pvzs: list) -> None:
    """
    Обновляет список ПВЗ для платформы.
    pvzs = [{"pvz_id": str|None, "pvz_name": str}]
    """
    if not pvzs:
        return
    data = _load()
    # Мёрджим: не удаляем старые, добавляем новые
    existing = {p["pvz_name"]: p for p in data.get(platform, [])}
    for p in pvzs:
        existing[p["pvz_name"]] = p
    data[platform] = list(existing.values())
    _save(data)


async def refresh_ozon() -> None:
    """Обновляет реестр Ozon ПВЗ из API."""
    try:
        from ozon.scraper import _get_all_stores
        stores = await _get_all_stores()
        pvzs = [{"pvz_id": str(s["id"]), "pvz_name": s.get("name") or str(s["id"])}
                for s in stores if s.get("id")]
        if pvzs:
            update_platform("ozon", pvzs)
            print(f"✅ Ozon ПВЗ в реестре: {[p['pvz_name'] for p in pvzs]}")
    except Exception as e:
        print(f"⚠️ Ozon реестр не обновлён: {e}")


async def refresh_ym() -> None:
    """Обновляет реестр ЯМ ПВЗ из последнего XLSX отчёта."""
    try:
        from yandex.reports import download_report_xlsx, available_months_for_menu
        from yandex.xlsx_parser import parse_ym_xlsx
        months = available_months_for_menu(1)
        if not months:
            return
        xlsx = await download_report_xlsx(months[0]["month"], months[0]["year"])
        names = list(parse_ym_xlsx(xlsx).keys())
        pvzs = [{"pvz_id": None, "pvz_name": name} for name in names if name]
        if pvzs:
            update_platform("ym", pvzs)
            print(f"✅ ЯМ ПВЗ в реестре: {[p['pvz_name'] for p in pvzs]}")
    except Exception as e:
        print(f"⚠️ ЯМ реестр не обновлён: {e}")


def refresh_wb() -> None:
    """Обновляет реестр WB ПВЗ из токена."""
    try:
        from wildberries.http_client import _load_token
        wb = _load_token()
        pid = wb.get("pickpoint_id")
        if not pid:
            return
        pvz_name = wb.get("pvz_address") or f"WB ПВЗ #{pid}"
        update_platform("wb", [{"pvz_id": str(pid), "pvz_name": pvz_name}])
        print(f"✅ WB ПВЗ в реестре: {pvz_name}")
    except Exception as e:
        print(f"⚠️ WB реестр не обновлён: {e}")


async def refresh_all() -> None:
    """Обновляет реестр по всем платформам."""
    refresh_wb()
    await refresh_ozon()
    await refresh_ym()
