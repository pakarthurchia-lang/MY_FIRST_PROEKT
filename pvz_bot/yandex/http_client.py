"""
HTTP-клиент для Яндекс Маркет Logistics API.

Авторизация: Session_id cookie из браузера (аналогично тому, как Ozon использует pvz-access-token).
Токен сохраняется в data/yandex_token.json.

Как получить Session_id первый раз:
  1. Залогинься на mail.yandex.ru
  2. Открой DevTools → Application → Cookies → .yandex.ru
  3. Найди куку Session_id, скопируй значение
  4. Запусти: python yandex/setup_token.py
  Или вставь вручную в data/yandex_token.json:
  {"session_id": "3:...", "partner_id": 44946604}
"""

import json
import os
import aiohttp

TOKEN_FILE = "data/yandex_token.json"
PARTNER_ID = 44946604

BASE_URL = "https://hubs.market.yandex.ru"

HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru",
    "Referer": f"{BASE_URL}/tpl-partner/{PARTNER_ID}/month-reports",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Safari/605.1.15",
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


def get_token_data() -> dict:
    global _token_data
    if not _token_data:
        _token_data = _load_token()
    return _token_data


def _refresh_session_from_safari() -> str:
    """Пробует получить свежий Session_id из Safari и сохраняет его."""
    global _token_data
    import browser_cookie3
    jar = browser_cookie3.safari(domain_name="yandex.ru")
    session_id = next((c.value for c in jar if c.name == "Session_id"), None)
    if not session_id:
        raise RuntimeError("Session_id не найден в Safari — залогинься на hubs.market.yandex.ru")
    _token_data["session_id"] = session_id
    _save_token(_token_data)
    return session_id


def _get_session_id() -> str:
    data = get_token_data()
    session_id = data.get("session_id")
    if not session_id:
        # Пробуем считать из Safari автоматически
        try:
            session_id = _refresh_session_from_safari()
            print("✅ Session_id Яндекса получен из Safari автоматически")
            return session_id
        except Exception:
            pass
        raise RuntimeError(
            "Нет Session_id для Яндекс Маркет.\n"
            "Запусти: python yandex/setup_token.py"
        )
    return session_id


def _get_cookies() -> dict:
    return {"Session_id": _get_session_id()}


_TIMEOUT = aiohttp.ClientTimeout(total=60)


async def get(url: str, params: dict = None) -> dict:
    cookies = _get_cookies()
    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session:
        async with session.get(url, params=params, headers=HEADERS_BASE) as resp:
            if resp.status == 401:
                # Пробуем обновить из Safari и повторить
                try:
                    new_sid = _refresh_session_from_safari()
                    async with session.get(
                        url, params=params, headers=HEADERS_BASE,
                        cookies={"Session_id": new_sid}
                    ) as resp2:
                        resp2.raise_for_status()
                        return await resp2.json()
                except Exception:
                    raise RuntimeError(
                        "Session_id истёк — запусти python yandex/setup_token.py"
                    )
            resp.raise_for_status()
            return await resp.json()


async def get_bytes(url: str, params: dict = None) -> bytes:
    """Скачивает файл (XLSX) как bytes."""
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Accept": "application/octet-stream, */*"}
    async with aiohttp.ClientSession(cookies=cookies, timeout=_TIMEOUT) as session:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 401:
                try:
                    new_sid = _refresh_session_from_safari()
                    async with session.get(
                        url, params=params, headers=headers,
                        cookies={"Session_id": new_sid}
                    ) as resp2:
                        resp2.raise_for_status()
                        return await resp2.read()
                except Exception:
                    raise RuntimeError(
                        "Session_id истёк — запусти python yandex/setup_token.py"
                    )
            resp.raise_for_status()
            return await resp.read()
