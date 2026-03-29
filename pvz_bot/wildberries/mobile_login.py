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

    _save_token(token_data)
    return token_data


def _save_token(data: dict):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)
