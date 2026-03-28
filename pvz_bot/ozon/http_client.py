"""
Прямой HTTP клиент для Ozon API.

Использует curl_cffi с имитацией TLS fingerprint Safari для обхода
антифрода Ozon. Без этого Ozon возвращает 403 на все auth-эндпоинты.

Авторизация (автономная, без браузера):
  SSO cookies (1 год) → ozonIdCookie/V3 → PVZ Mobile token (13ч)
  При истечении PVZ токена — автоматическое обновление через SSO куки.
  При устаревании SSO кук — обновление через actionV2TokenUpdate.

Ограничения Mobile токена:
  ✅ claims, stores, analytics dashboard
  ❌ reports (PDF), analytics/claims/general-info (нужен Web токен)
"""
import base64
import json
import os
import time
from curl_cffi.requests import AsyncSession

from config import OZON_SESSION_FILE

TOKEN_FILE = "data/ozon_token.json"

BASE_URL = "https://turbo-pvz.ozon.ru"
REQUEST_TOKEN_URL = f"{BASE_URL}/api2/auth/request-token"
SSO_REFRESH_URL = "https://api.ozon.ru/composer-api.bx/_action/actionV2TokenUpdate"
MOBILE_REFRESH_URL = f"{BASE_URL}/api2/Mobile/refresh-token"
MOBILE_AUTH_URL = f"{BASE_URL}/api2/Mobile/auth/ozonIdCookie/V3"

# TLS fingerprint — ключ к обходу антифрода Ozon
IMPERSONATE = "safari17_0"

HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Safari/605.1.15"
    ),
    "X-O3-App-Name": "turbo-pvz-ui",
    "X-O3-App-Version": "release/51261257",
}

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

SSO_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-o3-app-name": "ozonpvzapp_ios",
    "x-o3-app-version": "3.50.0(609)",
    "x-o3-fp": "0.ba55bb2ca4aca37d",
    "user-agent": "ozonpvzapp_ios_prod",
}

_token_data: dict = {}
_TIMEOUT = 30


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


def _jwt_decode(token: str) -> dict:
    """Декодирует payload JWT без верификации подписи."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.b64decode(payload))
    except Exception:
        return {}


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


def _save_sso_cookies(new_cookies_map: dict):
    """Сохраняет обновлённые SSO куки в ozon_session.json."""
    try:
        with open(OZON_SESSION_FILE) as f:
            state = json.load(f)
        existing = {c["name"]: c for c in state.get("cookies", [])}
    except Exception:
        existing = {}
        state = {"cookies": [], "origins": []}

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


async def _refresh_sso_via_api() -> bool:
    """
    Обновляет SSO куки через actionV2TokenUpdate + curl_cffi.
    Возвращает True если успешно обновило.
    """
    cookies = _get_cookies()
    if not cookies.get("__Secure-access-token") and not cookies.get("__Secure-refresh-token"):
        return False

    body = {
        "tokens": [],
        "deviceModel": "iPhone",
        "tzOffset": 10800,
        "permissions": [],
        "application": {"build": "PROD", "platform": "IOS", "name": "PVZ"},
        "authorization": "ENABLED",
        "hwid": "00000000-0000-0000-0000-000000000001",
    }

    try:
        async with AsyncSession(impersonate=IMPERSONATE, timeout=_TIMEOUT) as s:
            r = await s.post(SSO_REFRESH_URL, json=body, headers=SSO_HEADERS, cookies=cookies)
            if r.status_code != 200:
                return False

            # Собираем Set-Cookie из ответа
            new_cookies_map = {}
            for header_val in r.headers.get_list("set-cookie"):
                parts = [p.strip() for p in header_val.split(";")]
                if not parts or "=" not in parts[0]:
                    continue
                cname, cval = parts[0].split("=", 1)
                cname = cname.strip()
                if cname.startswith("__Secure-") or cname in {"abt_data"}:
                    new_cookies_map[cname] = cval.strip()

        if not new_cookies_map:
            return False

        _save_sso_cookies(new_cookies_map)
        return True
    except Exception:
        return False


# ── Механизмы получения PVZ токена ───────────────────────────────────────────

async def _fetch_pvz_token_via_mobile_auth() -> dict:
    """
    POST ozonIdCookie/V3 с SSO куками через curl_cffi.
    Возвращает PVZ Mobile token (~13ч) + refreshToken (~3 дня).
    SSO куки живут ~1 год → это основной автономный способ.
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

    async with AsyncSession(impersonate=IMPERSONATE, timeout=_TIMEOUT) as s:
        r = await s.post(MOBILE_AUTH_URL, json=body, headers=MOBILE_HEADERS, cookies=cookies)
        if r.status_code != 200:
            raise RuntimeError(f"ozonIdCookie/V3 вернул {r.status_code}: {r.text[:300]}")
        data = r.json()

    token = data.get("token")
    refresh = data.get("refreshToken")
    if not token:
        raise RuntimeError(f"token не найден в ответе ozonIdCookie/V3: {list(data.keys())}")

    claims = _jwt_decode(token)
    expire_time = claims.get("exp", 0) * 1000

    refresh_claims = _jwt_decode(refresh) if refresh else {}
    refresh_expire_time = refresh_claims.get("exp", 0) * 1000

    if not expire_time:
        expire_time = int(time.time() + 13 * 3600) * 1000
    if not refresh_expire_time:
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
    Обновляет PVZ токен через refresh_token + curl_cffi.
    refresh_token живёт ~3 дня.
    """
    current = _load_token()
    refresh_token = current.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("refresh_token отсутствует")

    refresh_exp = current.get("refresh_expire_time", 0)
    if refresh_exp > 1e12:
        refresh_exp /= 1000
    if refresh_exp and time.time() >= refresh_exp:
        raise RuntimeError("refresh_token истёк")

    async with AsyncSession(impersonate=IMPERSONATE, timeout=_TIMEOUT) as s:
        r = await s.post(
            MOBILE_REFRESH_URL,
            json={"refreshToken": refresh_token},
            headers=MOBILE_HEADERS,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Mobile refresh вернул {r.status_code}: {r.text[:300]}")
        data = r.json()

    access = data.get("token") or data.get("access_token") or data.get("accessToken")
    if data.get("returnCode") == "Unauthorized" or not access:
        raise RuntimeError(f"Mobile refresh отклонён: {data.get('message', r.text[:200])}")

    refresh_new = data.get("refreshToken") or data.get("refresh_token") or refresh_token
    claims = _jwt_decode(access)
    refresh_claims = _jwt_decode(refresh_new) if refresh_new != refresh_token else {}

    token_data = {
        "access_token": access,
        "refresh_token": refresh_new,
        "expire_time": claims.get("exp", 0) * 1000 or data.get("expire_time"),
        "refresh_expire_time": (
            refresh_claims.get("exp", 0) * 1000
            or data.get("refresh_expire_time")
            or current.get("refresh_expire_time")
        ),
    }
    _save_token(token_data)
    return token_data


async def _renew_token() -> dict:
    """
    Обновляет PVZ токен. Полностью автономно через curl_cffi.

    Порядок:
    0. Если текущий токен Web и ещё валиден — возвращаем его (не перезаписываем Mobile)
    1. Mobile refresh_token (3 дня)
    2. ozonIdCookie/V3 + SSO куки (1 год)
    3. Обновляем SSO куки (actionV2TokenUpdate) + ozonIdCookie/V3
    4. Safari localStorage (Mac, fallback)
    5. Ошибка с инструкцией
    """
    # Запоминаем был ли у предыдущего токена StoreId (нужен для отчётов)
    saved = _load_token()
    _prev_had_store_id = bool(
        saved.get("access_token") and _jwt_decode(saved["access_token"]).get("StoreId")
    )

    # Попытка 0: если сохранённый токен — Web и ещё не истёк, не трогаем его
    if saved.get("access_token") and not _is_token_expired(saved):
        claims = _jwt_decode(saved["access_token"])
        if claims.get("ClientType") == "Web":
            return saved

    def _check_store_id(result: dict) -> dict:
        """Если предыдущий токен имел StoreId, а новый — нет, требуем /login."""
        if _prev_had_store_id:
            new_claims = _jwt_decode(result.get("access_token", ""))
            if not new_claims.get("StoreId"):
                raise RuntimeError(
                    "Web токен с доступом к отчётам истёк и не может быть обновлён автоматически.\n"
                    "Запусти /login чтобы войти снова."
                )
        return result

    # Попытка 1: refresh_token — быстро, без SSO кук
    try:
        result = await _refresh_via_mobile_token()
        print("✅ Ozon PVZ токен обновлён через refresh_token")
        return _check_store_id(result)
    except RuntimeError:
        raise
    except Exception as e:
        print(f"⚠️ Mobile refresh: {e}")

    # Попытка 2: ozonIdCookie/V3 — основной способ (SSO куки живут 1 год)
    try:
        result = await _fetch_pvz_token_via_mobile_auth()
        print("✅ Ozon PVZ токен обновлён через ozonIdCookie/V3")
        return _check_store_id(result)
    except RuntimeError:
        raise
    except Exception as e:
        print(f"⚠️ ozonIdCookie/V3: {e}")

    # Попытка 3: обновить SSO куки и повторить ozonIdCookie/V3
    if await _refresh_sso_via_api():
        print("✅ SSO куки обновлены через actionV2TokenUpdate")
        try:
            result = await _fetch_pvz_token_via_mobile_auth()
            print("✅ Ozon PVZ токен обновлён через ozonIdCookie/V3 (после SSO refresh)")
            return _check_store_id(result)
        except RuntimeError:
            raise
        except Exception as e:
            print(f"⚠️ ozonIdCookie/V3 после SSO refresh: {e}")

    # Попытка 4: Safari localStorage (только Mac, fallback)
    try:
        from ozon.safari_token import update_token_from_safari
        result = update_token_from_safari()
        if result.get("access_token") and not _is_token_expired(result):
            print("✅ Ozon PVZ токен из Safari localStorage")
            return _check_store_id(result)
        elif result.get("access_token"):
            print("⚠️ Safari localStorage: токен уже истёк")
    except RuntimeError:
        raise
    except Exception:
        pass

    raise RuntimeError(
        "Не удалось обновить Ozon токен автоматически.\n"
        "Используй /ozon_token для ручного обновления."
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
    """Принудительное обновление токена (вызывается при 401/403)."""
    global _token_data
    _token_data = await _renew_token()
    return _token_data["access_token"]


async def post(url: str, body: dict) -> dict:
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}

    async with AsyncSession(impersonate=IMPERSONATE, timeout=_TIMEOUT) as s:
        r = await s.post(url, json=body, headers=headers, cookies=cookies)
        if r.status_code != 401:
            r.raise_for_status()
            return r.json()

    # 401 — обновляем токен и повторяем
    token = await _force_renew()
    cookies = _get_cookies()
    headers["Authorization"] = f"Bearer {token}"
    async with AsyncSession(impersonate=IMPERSONATE, timeout=_TIMEOUT) as s:
        r = await s.post(url, json=body, headers=headers, cookies=cookies)
        r.raise_for_status()
        return r.json()


async def get(url: str, params: dict = None) -> dict:
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}

    async with AsyncSession(impersonate=IMPERSONATE, timeout=_TIMEOUT) as s:
        r = await s.get(url, params=params, headers=headers, cookies=cookies)
        if r.status_code != 401:
            r.raise_for_status()
            return r.json()

    # 401 — обновляем токен и повторяем
    token = await _force_renew()
    cookies = _get_cookies()
    headers["Authorization"] = f"Bearer {token}"
    async with AsyncSession(impersonate=IMPERSONATE, timeout=_TIMEOUT) as s:
        r = await s.get(url, params=params, headers=headers, cookies=cookies)
        r.raise_for_status()
        return r.json()


async def get_bytes(url: str) -> bytes:
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}

    async with AsyncSession(impersonate=IMPERSONATE, timeout=60) as s:
        r = await s.get(url, headers=headers, cookies=cookies)
        if r.status_code == 401:
            pass  # обновляем ниже
        elif r.status_code != 200:
            raise RuntimeError(f"Ошибка скачивания: {r.status_code} {r.text[:200]}")
        else:
            return r.content

    # 401 — обновляем токен и повторяем
    token = await _force_renew()
    cookies = _get_cookies()
    headers["Authorization"] = f"Bearer {token}"
    async with AsyncSession(impersonate=IMPERSONATE, timeout=60) as s:
        r = await s.get(url, headers=headers, cookies=cookies)
        if r.status_code != 200:
            raise RuntimeError(f"Ошибка скачивания: {r.status_code} {r.text[:200]}")
        return r.content
