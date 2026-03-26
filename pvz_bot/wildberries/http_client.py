"""
HTTP-клиент для WB ПВЗ API (point-balance.wb.ru).

Безопасность:
- X-Token НИКОГДА не логируется (ни в print, ни в logging)
- Токен читается из data/wb_token.json (права 600, в .gitignore)
- Все запросы только READ-ONLY (GET)
- HTTPS — трафик зашифрован

Авторизация: X-Token header (JWT, живёт 24 часа).
Автообновление: пробуем читать из Safari localStorage.
"""
import json
import os
import time
import aiohttp

TOKEN_FILE = "data/wb_token.json"
BASE_URL = "https://point-balance.wb.ru"

HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru",
    "Origin": "https://pvz-lk.wb.ru",
    "Referer": "https://pvz-lk.wb.ru/payments",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/26.2 Safari/605.1.15"
    ),
    "X-App-Type": "prod",
    "X-App-Version": "v9.7.362",
}

_token_cache: dict = {}
_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _load_token() -> dict:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return {}


def _is_expired(token_data: dict, margin_sec: int = 300) -> bool:
    exp = token_data.get("exp", 0)
    return time.time() >= exp - margin_sec


def get_pickpoint_id():
    """Возвращает pickpoint_id из сохранённого токена."""
    global _token_cache
    if not _token_cache:
        _token_cache = _load_token()
    return _token_cache.get("pickpoint_id")


def get_token_status() -> dict:
    """Возвращает статус токена (для отображения в боте)."""
    data = _load_token()
    if not data or not data.get("x_token"):
        return {"valid": False, "reason": "no_token"}
    if _is_expired(data):
        return {"valid": False, "reason": "expired"}
    remaining = int(data["exp"] - time.time())
    return {
        "valid": True,
        "remaining_hours": remaining // 3600,
        "remaining_min": (remaining % 3600) // 60,
        "pickpoint_id": data.get("pickpoint_id"),
    }


async def _get_token() -> str:
    """
    Возвращает актуальный X-Token.
    1. Из кэша если не истёк
    2. Из Safari localStorage автоматически
    3. Ошибка с инструкцией
    """
    global _token_cache

    if not _token_cache:
        _token_cache = _load_token()

    if _token_cache.get("x_token") and not _is_expired(_token_cache):
        return _token_cache["x_token"]

    # Пробуем обновить из Safari
    try:
        from wildberries.safari_token import update_token_from_safari
        token = update_token_from_safari()
        _token_cache = _load_token()
        return token
    except Exception:
        pass

    if _token_cache.get("x_token") and not _is_expired(_token_cache, margin_sec=0):
        # Токен ещё формально живой (просто истекает скоро)
        return _token_cache["x_token"]

    raise RuntimeError(
        "WB токен истёк. Обнови его:\n"
        "1. Открой pvz-lk.wb.ru в Safari\n"
        "2. Запусти: python wildberries/setup_token.py\n"
        "Или в боте: /wb_refresh"
    )


async def get(url: str, params: dict = None) -> dict:
    """GET запрос к WB API. X-Token передаётся в заголовке, не логируется."""
    token = await _get_token()
    headers = {**HEADERS_BASE, "X-Token": token}

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 401:
                # Пробуем обновить из Safari и повторить
                _token_cache.clear()
                try:
                    token = await _get_token()
                    headers["X-Token"] = token
                    async with session.get(url, params=params, headers=headers) as resp2:
                        resp2.raise_for_status()
                        return await resp2.json()
                except RuntimeError:
                    raise
            resp.raise_for_status()
            return await resp.json()
