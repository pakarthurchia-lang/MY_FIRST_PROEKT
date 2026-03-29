"""
HTTP-клиент для WB ПВЗ API (point-balance.wb.ru).

Безопасность:
- X-Token НИКОГДА не логируется (ни в print, ни в logging)
- Токен читается из data/wb_token.json (права 600, в .gitignore)
- Все запросы только READ-ONLY (GET)
- HTTPS — трафик зашифрован

Авторизация: X-Token header (JWT, живёт 24 часа).
Автообновление: POST r-point.wb.ru/api/v1/refresh с refresh_token (живёт 90 дней).

Формат data/wb_token.json:
  {
    "x_token": "...",        # access token, 24ч
    "exp": 1234567890,       # unix timestamp истечения access token
    "refresh_token": "...",  # refresh token, 90 дней
    "refresh_exp": ...,      # unix timestamp истечения refresh token
    "pickpoint_id": 12345
  }
"""
import json
import os
import time
import base64
import aiohttp

TOKEN_FILE = "data/wb_token.json"
BASE_URL = "https://point-balance.wb.ru"
WB_REFRESH_URL = "https://r-point.wb.ru/api/v1/refresh"

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

MOBILE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "x-app-type": "mobile",
    "x-app-version": "v3.61.0",
    "x-device-type": "ios",
    "User-Agent": "WBPoint/14287039 CFNetwork/3826.500.131 Darwin/24.5.0",
}

_token_cache: dict = {}
_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _load_token() -> dict:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return {}


def _save_token(data: dict):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)


def _jwt_exp(token: str) -> int:
    """Декодирует exp из JWT без верификации подписи."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        claims = json.loads(base64.b64decode(payload))
        return int(claims.get("exp", 0))
    except Exception:
        return 0


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


async def _refresh_wb_token() -> str:
    """
    Обновляет WB access token через refresh token.
    POST r-point.wb.ru/api/v1/refresh
    Возвращает новый x_token и сохраняет в файл.
    """
    global _token_cache
    data = _token_cache or _load_token()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("WB refresh_token отсутствует")

    refresh_exp = data.get("refresh_exp", 0)
    if refresh_exp and time.time() >= refresh_exp:
        raise RuntimeError("WB refresh_token истёк (90 дней)")

    access_token = data.get("x_token", "")
    pickpoint_id = data.get("pickpoint_id")
    headers = {**MOBILE_HEADERS, "x-token": access_token}
    if pickpoint_id:
        headers["x-pickpoint-external-id"] = str(pickpoint_id)
    body = {"backoffice": False, "token": refresh_token}

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(WB_REFRESH_URL, json=body, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"WB refresh вернул {resp.status}: {text[:200]}")
            result = await resp.json()

    new_access = result["access"]["token"]
    new_refresh = result["refresh"]["token"]

    updated = {
        **data,
        "x_token": new_access,
        "exp": _jwt_exp(new_access),
        "refresh_token": new_refresh,
        "refresh_exp": _jwt_exp(new_refresh),
    }
    _save_token(updated)
    _token_cache = updated
    print("✅ WB токен обновлён через refresh_token")
    return new_access


async def _get_token() -> str:
    """
    Возвращает актуальный X-Token.
    1. Из кэша если не истёк
    2. Обновляем через refresh_token (90 дней)
    3. Из Safari localStorage
    4. Ошибка с инструкцией
    """
    global _token_cache

    if not _token_cache:
        _token_cache = _load_token()

    if _token_cache.get("x_token") and not _is_expired(_token_cache):
        return _token_cache["x_token"]

    # Пробуем обновить через refresh_token
    try:
        return await _refresh_wb_token()
    except Exception as e:
        print(f"⚠️ WB refresh не сработал: {e}")

    # Пробуем обновить из Safari (только Mac)
    try:
        from wildberries.safari_token import update_token_from_safari
        update_token_from_safari()
        _token_cache = _load_token()
        if _token_cache.get("x_token") and not _is_expired(_token_cache):
            return _token_cache["x_token"]
    except Exception:
        pass

    raise RuntimeError(
        "WB токен истёк и не удалось обновить автоматически.\n"
        "Используй /wb_login для входа через браузер."
    )


async def get(url: str, params: dict = None) -> dict:
    """GET запрос к WB API. x-token передаётся в заголовке, не логируется."""
    token = await _get_token()
    pickpoint_id = get_pickpoint_id()

    data = _token_cache or _load_token()
    if data.get("token_type") == "web":
        headers = {k: v for k, v in HEADERS_BASE.items() if k != "Content-Type"}
    else:
        headers = {k: v for k, v in MOBILE_HEADERS.items() if k != "Content-Type"}
    headers["x-token"] = token
    if pickpoint_id:
        headers["x-pickpoint-external-id"] = str(pickpoint_id)

    # wb.ru куки общие для всех поддоменов — нужны для point-balance.wb.ru
    cookies = data.get("wb_cookies") or {}

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.get(url, params=params, headers=headers, cookies=cookies) as resp:
            if resp.status == 401:
                _token_cache.clear()
                try:
                    token = await _get_token()
                    headers["x-token"] = token
                    async with session.get(url, params=params, headers=headers, cookies=cookies) as resp2:
                        resp2.raise_for_status()
                        return await resp2.json()
                except RuntimeError:
                    raise
            resp.raise_for_status()
            return await resp.json()
