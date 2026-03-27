"""
Прямой HTTP клиент для Ozon API.

Как работает авторизация:
  SSO cookies (sso.ozon.ru)  →  GET /api2/auth/request-token  →  pvz-access-token (JWT)

Порядок обновления токена:
  1. GET request-token с сохранёнными SSO куками (работает быстро и надёжно)
  2. Если на Mac — обновить куки из Safari и повторить
  3. Если нет кук — просить /login
"""
import json
import os
import time
import aiohttp
from config import OZON_SESSION_FILE

TOKEN_FILE = "data/ozon_token.json"

BASE_URL = "https://turbo-pvz.ozon.ru"
REQUEST_TOKEN_URL = f"{BASE_URL}/api2/auth/request-token"

HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Safari/605.1.15",
    "X-O3-App-Name": "turbo-pvz-ui",
    "X-O3-App-Version": "release/51261257",
}

_token_data: dict = {}
_TIMEOUT = aiohttp.ClientTimeout(total=30)


# ── Файл с PVZ токеном ────────────────────────────────────────────────────────

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


def _is_token_expired(token_data: dict, margin_sec: int = 300) -> bool:
    if not token_data or not token_data.get("access_token"):
        return True
    exp = token_data.get("expire_time", 0)
    if exp > 1e12:
        exp /= 1000
    return time.time() >= exp - margin_sec


# ── SSO куки ──────────────────────────────────────────────────────────────────

def _get_cookies() -> dict:
    """Читает SSO куки из ozon_session.json."""
    if not os.path.exists(OZON_SESSION_FILE):
        return {}
    try:
        with open(OZON_SESSION_FILE) as f:
            state = json.load(f)
        return {c["name"]: c["value"] for c in state.get("cookies", [])}
    except Exception:
        return {}


def _refresh_cookies_from_safari() -> bool:
    """
    Обновляет ozon_session.json из Safari (только на Mac).
    Возвращает True если успешно.
    """
    try:
        import browser_cookie3
        jar = browser_cookie3.safari(domain_name="ozon.ru")
        cookies_list = list(jar)
        if not cookies_list:
            return False

        playwright_cookies = []
        for c in cookies_list:
            cookie = {
                "name": c.name,
                "value": c.value,
                "domain": c.domain if c.domain.startswith(".") else f".{c.domain}",
                "path": c.path or "/",
                "httpOnly": bool(getattr(c, "_rest", {}).get("HttpOnly", False)),
                "secure": bool(c.secure),
                "sameSite": "Lax",
                "expires": int(c.expires) if c.expires and c.expires > 0 else -1,
            }
            playwright_cookies.append(cookie)

        os.makedirs(os.path.dirname(OZON_SESSION_FILE), exist_ok=True)
        with open(OZON_SESSION_FILE, "w") as f:
            json.dump({"cookies": playwright_cookies, "origins": []}, f, indent=2)
        os.chmod(OZON_SESSION_FILE, 0o600)
        return True
    except Exception:
        return False


# ── Главный механизм обновления токена ───────────────────────────────────────

async def _fetch_pvz_token_via_cookies(cookies: dict) -> dict:
    """
    GET /api2/auth/request-token с SSO куками → возвращает pvz-access-token.
    Это официальный механизм который использует сам браузер.
    """
    headers = {k: v for k, v in HEADERS_BASE.items() if k != "Content-Type"}
    params = {"returnUrl": BASE_URL}

    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session:
        async with session.get(REQUEST_TOKEN_URL, params=params, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"request-token вернул {resp.status}: {text[:300]}")
            data = await resp.json()

    token_data = {
        "access_token": data.get("access_token") or data.get("accessToken"),
        "refresh_token": data.get("refresh_token") or data.get("refreshToken"),
        "expire_time": data.get("expire_time") or data.get("expireTime"),
        "refresh_expire_time": data.get("refresh_expire_time") or data.get("refreshExpireTime"),
    }
    if not token_data["access_token"]:
        raise RuntimeError(f"access_token не найден в ответе request-token: {data}")

    _save_token(token_data)
    return token_data


async def _renew_token() -> dict:
    """
    Обновляет PVZ токен.

    Порядок:
    1. Читаем свежий токен из Safari localStorage (если бот на Mac и Safari открыт)
    2. GET request-token с сохранёнными SSO куками
    3. Если на Mac — обновляем куки из Safari и повторяем request-token
    4. Просим /login
    """
    # Попытка 1: читаем токен прямо из Safari localStorage — самый быстрый путь на Mac
    try:
        from ozon.safari_token import update_token_from_safari
        result = update_token_from_safari()
        if result.get("access_token"):
            print("✅ PVZ токен обновлён из Safari localStorage")
            return result
    except Exception:
        pass

    # Попытка 2: GET request-token с сохранёнными SSO куками
    cookies = _get_cookies()
    if cookies:
        try:
            result = await _fetch_pvz_token_via_cookies(cookies)
            print("✅ PVZ токен обновлён через SSO cookies (request-token)")
            return result
        except Exception as e:
            print(f"⚠️ request-token с текущими куками не сработал: {e}")

    # Попытка 3: обновить куки из Safari и повторить request-token
    if _refresh_cookies_from_safari():
        cookies = _get_cookies()
        if cookies:
            try:
                result = await _fetch_pvz_token_via_cookies(cookies)
                print("✅ PVZ токен обновлён через свежие SSO cookies из Safari")
                return result
            except Exception as e:
                print(f"⚠️ request-token после обновления кук не сработал: {e}")

    raise RuntimeError(
        "Не удалось обновить токен автоматически.\n"
        "Выполни /login для повторной авторизации."
    )


# ── Публичный интерфейс ───────────────────────────────────────────────────────

async def get_access_token() -> str:
    """Возвращает актуальный Bearer токен, обновляет при необходимости."""
    global _token_data

    if not _token_data:
        _token_data = _load_token()

    if not _is_token_expired(_token_data):
        return _token_data["access_token"]

    _token_data = await _renew_token()
    return _token_data["access_token"]


async def _force_renew() -> str:
    """Принудительное обновление токена (вызывается при 401)."""
    global _token_data
    _token_data = await _renew_token()
    return _token_data["access_token"]


async def post(url: str, body: dict) -> dict:
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}

    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session:
        async with session.post(url, json=body, headers=headers) as resp:
            if resp.status == 401:
                token = await _force_renew()
                headers["Authorization"] = f"Bearer {token}"
                async with session.post(url, json=body, headers=headers) as resp2:
                    return await resp2.json()
            return await resp.json()


async def get(url: str, params: dict = None) -> dict:
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}

    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 401:
                token = await _force_renew()
                headers["Authorization"] = f"Bearer {token}"
                async with session.get(url, params=params, headers=headers) as resp2:
                    return await resp2.json()
            return await resp.json()


async def get_bytes(url: str) -> bytes:
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(cookies=cookies, timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 401:
                token = await _force_renew()
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
