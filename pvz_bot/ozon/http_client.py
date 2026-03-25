"""
Прямой HTTP клиент для Ozon API.
Не использует Playwright — работает через aiohttp с Bearer токеном.
"""
import json
import os
import time
import aiohttp
from config import OZON_SESSION_FILE

TOKEN_FILE = "data/ozon_token.json"

BASE_URL = "https://turbo-pvz.ozon.ru"
REFRESH_URL = f"{BASE_URL}/api2/auth/v1/refresh"

HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/claims/list",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Safari/605.1.15",
    "X-O3-App-Name": "turbo-pvz-ui",
    "X-O3-App-Version": "release/51261257",
}

_token_data: dict = {}


def _load_token() -> dict:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return {}


def _save_token(data: dict):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _get_cookies() -> dict:
    """Читает куки из ozon_session.json"""
    if not os.path.exists(OZON_SESSION_FILE):
        return {}
    with open(OZON_SESSION_FILE) as f:
        state = json.load(f)
    return {c["name"]: c["value"] for c in state.get("cookies", [])}


async def _refresh_access_token(token_data: dict) -> dict:
    """Обновляет access_token через refresh_token"""
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Нет refresh_token — запусти import_session.py")

    cookies = _get_cookies()
    async with aiohttp.ClientSession(cookies=cookies) as session:
        async with session.post(
            REFRESH_URL,
            json={"refreshToken": refresh_token},
            headers=HEADERS_BASE,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Ошибка обновления токена: {resp.status} {text[:200]}")
            data = await resp.json()

    new_token = {
        "access_token": data.get("access_token") or data.get("accessToken"),
        "refresh_token": data.get("refresh_token") or data.get("refreshToken", refresh_token),
        "expire_time": data.get("expire_time") or data.get("expireTime"),
        "refresh_expire_time": data.get("refresh_expire_time") or data.get("refreshExpireTime"),
    }
    _save_token(new_token)
    return new_token


def _is_token_expired(token_data: dict, margin_sec: int = 300) -> bool:
    """Возвращает True если access_token истёк или истекает через margin_sec секунд."""
    if not token_data or not token_data.get("access_token"):
        return True
    exp = token_data.get("expire_time", 0)
    if exp > 1e12:
        exp /= 1000
    return time.time() >= exp - margin_sec


async def get_access_token() -> str:
    """Возвращает актуальный Bearer токен.

    Порядок обновления:
    1. Если токен ещё живой — возвращаем его.
    2. Пробуем прочитать свежий токен из браузера (Safari).
    3. Если не вышло — обновляем через refresh_token (API).
    4. Если и это не вышло — кидаем ошибку (нужно обновить вручную).
    """
    global _token_data

    if not _token_data:
        _token_data = _load_token()

    if not _is_token_expired(_token_data):
        return _token_data["access_token"]

    # Шаг 1: пробуем браузер
    try:
        from ozon.safari_token import update_token_from_safari
        _token_data = update_token_from_safari()
        print("✅ Токен обновлён из браузера автоматически")
        return _token_data["access_token"]
    except Exception:
        pass

    # Шаг 2: обновляем через refresh_token
    if _token_data and _token_data.get("refresh_token"):
        try:
            _token_data = await _refresh_access_token(_token_data)
            print("✅ Токен обновлён через refresh_token")
            return _token_data["access_token"]
        except Exception as e:
            raise RuntimeError(
                f"Токен истёк, refresh_token тоже не сработал: {e}\n"
                "Скопируй новый токен из браузера через localStorage.getItem('pvz-access-token')"
            )

    raise RuntimeError(
        "Токен истёк и нет refresh_token.\n"
        "Скопируй новый токен из браузера через localStorage.getItem('pvz-access-token')"
    )


_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def _force_refresh_token():
    """Принудительно обновляет токен через refresh_token и возвращает новый access_token."""
    global _token_data
    _token_data = await _refresh_access_token(_token_data)
    return _token_data["access_token"]


async def post(url: str, body: dict) -> dict:
    """POST запрос к Ozon API с авторизацией."""
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}

    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session:
        async with session.post(url, json=body, headers=headers) as resp:
            if resp.status == 401:
                token = await _force_refresh_token()
                headers["Authorization"] = f"Bearer {token}"
                async with session.post(url, json=body, headers=headers) as resp2:
                    return await resp2.json()
            return await resp.json()


async def get(url: str, params: dict = None) -> dict:
    """GET запрос к Ozon API с авторизацией."""
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}

    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 401:
                token = await _force_refresh_token()
                headers["Authorization"] = f"Bearer {token}"
                async with session.get(url, params=params, headers=headers) as resp2:
                    return await resp2.json()
            return await resp.json()


async def get_bytes(url: str) -> bytes:
    """GET запрос возвращающий bytes (для PDF). С 401-retry и таймаутом."""
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=60)  # PDF может быть большим

    async with aiohttp.ClientSession(cookies=cookies, timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 401:
                token = await _force_refresh_token()
                headers["Authorization"] = f"Bearer {token}"
                async with session.get(url, headers=headers) as resp2:
                    if resp2.status != 200:
                        text = await resp2.text()
                        raise RuntimeError(f"Ошибка скачивания: {resp2.status} {text[:200]}")
                    return await resp2.read()
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Ошибка скачивания: {resp.status} {text[:200]}")
            return await resp.read()
