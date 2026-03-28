"""
Прямой HTTP клиент для Ozon API.

Как работает авторизация:
  SSO cookies (sso.ozon.ru)  →  GET /api2/auth/request-token  →  pvz-access-token (JWT)

Порядок обновления SSO кук:
  POST api.ozon.ru/composer-api.bx/_action/actionV2TokenUpdate
  — приложение Ozon PVZ iOS вызывает его при старте
  — принимает старые SSO куки, возвращает свежие (expire +1 год)
  — работает пока хоть какие-то SSO куки живые

Порядок обновления PVZ токена:
  1. Читаем из Safari localStorage (если на Mac)
  2. GET request-token с сохранёнными SSO куками
  3. Обновляем SSO через actionV2TokenUpdate и повторяем request-token
  4. Обновляем SSO куки из Safari (если на Mac)
  5. Просим /ozon_token (букмарклет)
"""
import json
import os
import time
import aiohttp
from config import OZON_SESSION_FILE

TOKEN_FILE = "data/ozon_token.json"

BASE_URL = "https://turbo-pvz.ozon.ru"
REQUEST_TOKEN_URL = f"{BASE_URL}/api2/auth/request-token"
SSO_REFRESH_URL = "https://api.ozon.ru/composer-api.bx/_action/actionV2TokenUpdate"

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


async def _refresh_sso_via_api() -> bool:
    """
    Обновляет SSO куки через actionV2TokenUpdate (эндпоинт мобильного приложения).
    Отправляет старые SSO куки → получает новые (expire +1 год).
    Возвращает True если успешно обновило.
    """
    cookies = _get_cookies()
    if not cookies.get("__Secure-access-token") and not cookies.get("__Secure-refresh-token"):
        return False

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-o3-app-name": "ozonpvzapp_ios",
        "x-o3-app-version": "3.50.0(609)",
        "x-o3-fp": "0.ba55bb2ca4aca37d",
        "user-agent": "ozonpvzapp_ios_prod",
        "x-o3-sdk-versions": "push_sdk_ios/9.17.1",
    }
    body = {
        "tokens": [],
        "deviceModel": "Server",
        "tzOffset": 10800,
        "permissions": [],
        "application": {"build": "PROD", "platform": "IOS", "name": "PVZ"},
        "authorization": "ENABLED",
        "hwid": "00000000-0000-0000-0000-000000000001",
    }

    try:
        async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session:
            async with session.post(SSO_REFRESH_URL, json=body, headers=headers) as resp:
                if resp.status != 200:
                    return False
                # Собираем Set-Cookie из ответа
                new_cookies_map = {}
                for header_name, header_val in resp.headers.items():
                    if header_name.lower() != "set-cookie":
                        continue
                    parts = [p.strip() for p in header_val.split(";")]
                    if not parts or "=" not in parts[0]:
                        continue
                    cname, cval = parts[0].split("=", 1)
                    cname = cname.strip()
                    if cname.startswith("__Secure-") or cname in {"abt_data"}:
                        new_cookies_map[cname] = cval.strip()

        if not new_cookies_map:
            return False

        # Обновляем ozon_session.json
        try:
            with open(OZON_SESSION_FILE) as f:
                state = json.load(f)
            existing = {c["name"]: c for c in state.get("cookies", [])}
        except Exception:
            existing = {}
            state = {"cookies": [], "origins": []}

        import time
        expire_ts = int(time.time()) + 365 * 24 * 3600
        for cname, cval in new_cookies_map.items():
            existing[cname] = {
                "name": cname,
                "value": cval,
                "domain": ".ozon.ru",
                "path": "/",
                "httpOnly": cname in {
                    "__Secure-access-token", "__Secure-refresh-token", "__Secure-user-id"
                },
                "secure": True,
                "sameSite": "Lax",
                "expires": expire_ts,
            }

        state["cookies"] = list(existing.values())
        os.makedirs(os.path.dirname(OZON_SESSION_FILE), exist_ok=True)
        with open(OZON_SESSION_FILE, "w") as f:
            json.dump(state, f, indent=2)
        os.chmod(OZON_SESSION_FILE, 0o600)
        return True
    except Exception:
        return False


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

MOBILE_REFRESH_URL = f"{BASE_URL}/api2/Mobile/refresh-token"
MOBILE_AUTH_URL = f"{BASE_URL}/api2/Mobile/auth/ozonIdCookie/V3"
MOBILE_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-o3-app-name": "ozonpvzapp_ios",
    "x-o3-app-version": "3.50.0(609)",
    "x-o3-version-code": "609",
    "x-operating-system": "ios",
    "x-o3-version-name": "3.50.0",
    "x-o3-fp": "0.ba55bb2ca4aca37d",
    "user-agent": "ozonpvzapp_ios_prod",
    "accept-language": "ru-UM",
}


async def _fetch_pvz_token_via_mobile_auth() -> dict:
    """
    POST /api2/Mobile/auth/ozonIdCookie/V3 с SSO куками.
    Возвращает PVZ token (~13ч) + refreshToken (~3 дня).
    SSO куки из ozon_session.json живут 1 год.
    """
    cookies = _get_cookies()
    if not cookies.get("__Secure-access-token"):
        raise RuntimeError("SSO куки отсутствуют в ozon_session.json")

    body = {
        "domain": "ozon.ru",
        "auditParameters": {
            "computerName": "iPhone",
            "operationSystem": "ios",
            "userAgent": "iOS 18.5",
        },
        "authorization": [],
    }

    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session:
        async with session.post(MOBILE_AUTH_URL, json=body, headers=MOBILE_HEADERS) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"ozonIdCookie/V3 вернул {resp.status}: {text[:300]}")
            data = await resp.json()

    token = data.get("token")
    refresh = data.get("refreshToken")
    if not token:
        raise RuntimeError(f"token не найден в ответе ozonIdCookie/V3: {list(data.keys())}")

    # Декодируем exp из JWT без верификации
    import base64
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        claims = json.loads(base64.b64decode(payload))
        expire_time = claims.get("exp", 0) * 1000

        refresh_claims = json.loads(base64.b64decode(
            (refresh.split(".")[1] + "==")
        )) if refresh else {}
        refresh_expire_time = refresh_claims.get("exp", 0) * 1000
    except Exception:
        expire_time = int(time.time() + 13 * 3600) * 1000
        refresh_expire_time = int(time.time() + 3 * 86400) * 1000

    token_data = {
        "access_token": token,
        "refresh_token": refresh,
        "expire_time": expire_time,
        "refresh_expire_time": refresh_expire_time,
    }
    _save_token(token_data)
    return token_data


async def _refresh_via_mobile_token() -> dict:
    """
    Обновляет PVZ токен через мобильный API с помощью refresh_token.
    POST /api2/Mobile/refresh-token — живёт ~3 дня.
    Возвращает новый token_data или бросает RuntimeError.
    """
    current = _load_token()
    refresh_token = current.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("refresh_token отсутствует")

    # Проверяем что refresh_token ещё не истёк
    refresh_exp = current.get("refresh_expire_time", 0)
    if refresh_exp > 1e12:
        refresh_exp /= 1000
    if refresh_exp and time.time() >= refresh_exp:
        raise RuntimeError("refresh_token истёк")

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(
            MOBILE_REFRESH_URL,
            json={"refreshToken": refresh_token},
            headers=MOBILE_HEADERS,
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Mobile refresh вернул {resp.status}: {text[:300]}")
            data = await resp.json()

    if data.get("returnCode") == "Unauthorized" or not data.get("access_token"):
        raise RuntimeError(f"Mobile refresh отклонён: {data.get('message', text[:200])}")

    token_data = {
        "access_token": data.get("access_token") or data.get("accessToken"),
        "refresh_token": data.get("refresh_token") or data.get("refreshToken") or refresh_token,
        "expire_time": data.get("expire_time") or data.get("expireTime"),
        "refresh_expire_time": data.get("refresh_expire_time") or data.get("refreshExpireTime") or current.get("refresh_expire_time"),
    }
    _save_token(token_data)
    return token_data


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
    1. Mobile refresh_token → новый PVZ token (работает 3 дня)
    2. ozonIdCookie/V3 с SSO куками → PVZ token (SSO живут 1 год)
    3. Safari localStorage (Mac)
    4. request-token с SSO куками
    5. actionV2TokenUpdate + request-token
    6. Safari куки + request-token
    7. Просим /ozon_token (букмарклет)
    """
    # Попытка 1: обновляем через refresh_token (Mobile API) — быстро, без SSO кук
    try:
        result = await _refresh_via_mobile_token()
        print("✅ PVZ токен обновлён через Mobile refresh_token")
        return result
    except Exception as e:
        print(f"⚠️ Mobile refresh не сработал: {e}")

    # Попытка 2: ozonIdCookie/V3 с SSO куками — основной долгосрочный способ
    try:
        result = await _fetch_pvz_token_via_mobile_auth()
        print("✅ PVZ токен обновлён через ozonIdCookie/V3 (SSO куки)")
        return result
    except Exception as e:
        print(f"⚠️ ozonIdCookie/V3 не сработал: {e}")

    # Попытка 2: читаем токен прямо из Safari localStorage — мгновенно на Mac
    try:
        from ozon.safari_token import update_token_from_safari
        result = update_token_from_safari()
        if result.get("access_token"):
            print("✅ PVZ токен обновлён из Safari localStorage")
            return result
    except Exception:
        pass

    # Попытка 3: GET request-token с сохранёнными SSO куками
    cookies = _get_cookies()
    if cookies:
        try:
            result = await _fetch_pvz_token_via_cookies(cookies)
            print("✅ PVZ токен обновлён через SSO cookies (request-token)")
            return result
        except Exception as e:
            print(f"⚠️ request-token с текущими куками не сработал: {e}")

    # Попытка 3: обновить SSO куки через actionV2TokenUpdate и повторить
    if await _refresh_sso_via_api():
        cookies = _get_cookies()
        if cookies:
            try:
                result = await _fetch_pvz_token_via_cookies(cookies)
                print("✅ PVZ токен обновлён через SSO (actionV2TokenUpdate + request-token)")
                return result
            except Exception as e:
                print(f"⚠️ request-token после SSO refresh не сработал: {e}")

    # Попытка 4: обновить куки из Safari и повторить request-token (только Mac)
    if _refresh_cookies_from_safari():
        cookies = _get_cookies()
        if cookies:
            try:
                result = await _fetch_pvz_token_via_cookies(cookies)
                print("✅ PVZ токен обновлён через свежие SSO cookies из Safari")
                return result
            except Exception as e:
                print(f"⚠️ request-token после обновления кук из Safari не сработал: {e}")

    raise RuntimeError(
        "Не удалось обновить токен автоматически.\n"
        "Открой turbo-pvz.ozon.ru и нажми закладку 'Обновить токен ПВЗ', "
        "затем вставь результат командой /ozon_token."
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
            if resp.status != 401:
                return await resp.json()

    # 401 — обновляем токен и куки, создаём новую сессию
    token = await _force_renew()
    cookies = _get_cookies()
    headers["Authorization"] = f"Bearer {token}"
    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session2:
        async with session2.post(url, json=body, headers=headers) as resp2:
            resp2.raise_for_status()
            return await resp2.json()


async def get(url: str, params: dict = None) -> dict:
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}

    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status != 401:
                return await resp.json()

    # 401 — обновляем токен и куки, создаём новую сессию
    token = await _force_renew()
    cookies = _get_cookies()
    headers["Authorization"] = f"Bearer {token}"
    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session2:
        async with session2.get(url, params=params, headers=headers) as resp2:
            resp2.raise_for_status()
            return await resp2.json()


async def get_bytes(url: str) -> bytes:
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(cookies=cookies, timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 401:
                pass  # выходим из сессии, обновляем ниже
            elif resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Ошибка скачивания: {resp.status} {text[:200]}")
            else:
                return await resp.read()

    # 401 — обновляем токен и куки, создаём новую сессию
    token = await _force_renew()
    cookies = _get_cookies()
    headers["Authorization"] = f"Bearer {token}"
    async with aiohttp.ClientSession(cookies=cookies, timeout=timeout) as session2:
        async with session2.get(url, headers=headers) as resp2:
            if resp2.status != 200:
                text = await resp2.text()
                raise RuntimeError(f"Ошибка скачивания: {resp2.status} {text[:200]}")
            return await resp2.read()
