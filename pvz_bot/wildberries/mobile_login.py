"""
Автономный WB Point логин через мобильное API r-point.wb.ru.

Флоу (без Chrome, без mitmproxy):
  1. POST /api/v1/login  {"phone": "79..."}   → session token + SMS
  2. POST /api/v1/validate {"token": "...", "code": "XXXXXX"} → PVZ-scoped JWT

Результат: access token с pid=72827, xpid=50016046, живёт 24ч.
           refresh token живёт 90 дней, бот обновляет автоматически.
"""
import asyncio
import base64
import json
import os
import time
from typing import Callable, Awaitable, Optional

import aiohttp

TOKEN_FILE = "data/wb_token.json"
LOGIN_URL  = "https://r-point.wb.ru/api/v1/login"
VALIDATE_URL = "https://r-point.wb.ru/api/v1/validate"

MOBILE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "x-app-type": "mobile",
    "x-app-version": "v3.61.0",
    "x-device-type": "ios",
    "User-Agent": "WBPoint/14287039 CFNetwork/3826.500.131 Darwin/24.5.0",
}

_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _jwt_decode(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.b64decode(payload))
    except Exception:
        return {}


def _jwt_exp(token: str) -> int:
    return int(_jwt_decode(token).get("exp", 0))


def _normalize_phone(phone: str) -> str:
    """89198929203 → 79198929203"""
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    return digits


async def send_sms(phone: str) -> str:
    """
    Шаг 1: Отправляет SMS на телефон через r-point.wb.ru/api/v1/login.
    Возвращает session_token (поле data из ответа).
    """
    phone_norm = _normalize_phone(phone)
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(LOGIN_URL, headers=MOBILE_HEADERS, json={"phone": phone_norm}) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"WB login вернул {resp.status}: {text[:200]}")
            data = await resp.json()

    token = data.get("data")
    if not token:
        raise RuntimeError(f"WB login: нет поля 'data' в ответе: {data}")
    return token


async def verify_code(session_token: str, sms_code: str) -> dict:
    """
    Шаг 2: Верифицирует SMS код, получает PVZ-scoped токены.
    Возвращает dict с x_token, exp, refresh_token, refresh_exp, pickpoint_id.
    """
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(
            VALIDATE_URL,
            headers=MOBILE_HEADERS,
            json={"token": session_token, "code": sms_code.strip()},
        ) as resp:
            raw = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"WB validate вернул {resp.status}: {raw[:300]}")
            # Логируем полный ответ для диагностики
            print(f"[WB validate full response] {raw}")
            result = json.loads(raw)

    # Ответ может быть {"access": {"token": ...}, "refresh": {"token": ...}}
    # или {"token": ..., "refreshToken": ...}
    access_token = (
        (result.get("access") or {}).get("token")
        or result.get("token")
        or result.get("accessToken")
    )
    refresh_token = (
        (result.get("refresh") or {}).get("token")
        or result.get("refreshToken")
        or result.get("refresh_token")
    )

    if not access_token:
        raise RuntimeError(f"WB validate: токен не найден в ответе: {json.dumps(result)[:300]}")

    claims = _jwt_decode(access_token)
    pickpoint_id = claims.get("xpid") or claims.get("pid") or None

    token_data = {
        "x_token": access_token,
        "exp": _jwt_exp(access_token) or int(time.time()) + 86400,
        "pickpoint_id": pickpoint_id,
        "token_type": "mobile",
    }
    if refresh_token:
        token_data["refresh_token"] = refresh_token
        token_data["refresh_exp"] = _jwt_exp(refresh_token) or int(time.time()) + 90 * 86400

    # Если токен без привязки к ПВЗ (xpid=0) — пробуем апгрейд через refresh
    if not pickpoint_id and refresh_token:
        pvz_token = await _try_select_pvz(access_token, refresh_token)
        if pvz_token:
            _save_token(pvz_token)
            return pvz_token

    _save_token(token_data)
    return token_data


async def _try_select_pvz(general_token: str, refresh_token: str) -> Optional[dict]:
    """
    Когда validate возвращает общий токен (xpid=0), пробуем апгрейд:
    POST /api/v1/refresh с x-pickpoint-external-id → PVZ-scoped токен.
    xpid берём из конфига WB_PVZ_XPID или пробуем найти через API.
    """
    from config import WB_PVZ_XPID
    xpid = WB_PVZ_XPID or await _discover_xpid(general_token)
    if not xpid:
        print("⚠️ WB: xpid не задан — токен без привязки к ПВЗ")
        return None

    headers = {
        **MOBILE_HEADERS,
        "x-token": general_token,
        "x-pickpoint-external-id": str(xpid),
    }
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(
                "https://r-point.wb.ru/api/v1/refresh",
                headers=headers,
                json={"backoffice": False, "token": refresh_token},
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"⚠️ WB PVZ-upgrade failed {resp.status}: {text[:200]}")
                    return None
                result = await resp.json()
    except Exception as e:
        print(f"⚠️ WB PVZ-upgrade error: {e}")
        return None

    new_access = (result.get("access") or {}).get("token")
    new_refresh = (result.get("refresh") or {}).get("token")
    if not new_access:
        print(f"⚠️ WB PVZ-upgrade: токен не найден в ответе: {str(result)[:200]}")
        return None

    pvz_claims = _jwt_decode(new_access)
    pvz_xpid = pvz_claims.get("xpid") or pvz_claims.get("pid") or xpid
    print(f"✅ WB PVZ-upgrade: xpid={pvz_xpid}")
    return {
        "x_token": new_access,
        "exp": _jwt_exp(new_access) or int(time.time()) + 86400,
        "pickpoint_id": pvz_xpid,
        "token_type": "mobile",
        "refresh_token": new_refresh or refresh_token,
        "refresh_exp": (_jwt_exp(new_refresh) if new_refresh else 0) or int(time.time()) + 90 * 86400,
    }


async def _discover_xpid(token: str) -> Optional[int]:
    """Пробует найти xpid ПВЗ через WB API."""
    probe_urls = [
        "https://r-point.wb.ru/api/v1/pvz",
        "https://r-point.wb.ru/api/v1/pvz/list",
        "https://s-point.wb.ru/s3/api/v1/pvz/list",
    ]
    headers = {**MOBILE_HEADERS, "x-token": token}
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        for url in probe_urls:
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    # Ищем xpid / externalId в любом поле
                    for item in (data if isinstance(data, list) else data.get("data", [data])):
                        xpid = (item.get("xpid") or item.get("externalId")
                                or item.get("pickpointId") or item.get("id"))
                        if xpid:
                            print(f"✅ WB xpid обнаружен через {url}: {xpid}")
                            return int(xpid)
            except Exception:
                continue
    return None


def _save_token(data: dict):
    # Не перезаписывать действующий PVZ-токен (xpid != 0) общим токеном (xpid == 0)
    if not data.get("pickpoint_id"):
        try:
            with open(TOKEN_FILE) as f:
                existing = json.load(f)
            # Пропускаем только если старый токен ещё действует
            if existing.get("pickpoint_id") and existing.get("exp", 0) > time.time():
                return
        except Exception:
            pass
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)
