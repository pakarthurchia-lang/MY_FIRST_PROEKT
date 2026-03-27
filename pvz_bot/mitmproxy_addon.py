"""
mitmproxy addon для автоматического захвата SSO кук Ozon PVZ.

Когда приложение Ozon PVZ на iPhone обновляет сессию (actionV2TokenUpdate),
перехватывает свежие SSO куки и сохраняет в data/ozon_session.json.
Бот автоматически начнёт использовать эти куки.

Запуск:
    mitmweb -s mitmproxy_addon.py
    mitmproxy -s mitmproxy_addon.py
"""
import json
import os
import time

from mitmproxy import http

SESSION_FILE = os.path.join(os.path.dirname(__file__), "data", "ozon_session.json")
TARGET_HOST = "api.ozon.ru"
TARGET_PATH = "/composer-api.bx/_action/actionV2TokenUpdate"

PVZ_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "data", "ozon_token.json")
PVZ_AUTH_HOST = "turbo-pvz.ozon.ru"
PVZ_AUTH_PATH = "/api2/Mobile/auth/ozonIdCookie/V3"

WB_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "data", "wb_token.json")
WB_REFRESH_HOST = "r-point.wb.ru"
WB_REFRESH_PATH = "/api/v1/refresh"

# SSO куки которые нас интересуют
SSO_COOKIE_NAMES = {
    "__Secure-access-token",
    "__Secure-refresh-token",
    "__Secure-user-id",
    "__Secure-ab-group",
    "__Secure-ETC",
    "abt_data",
}

# Дополнительные куки которые стоит сохранить для request-token
EXTRA_COOKIE_NAMES = {
    "x-o3-app-name",
    "x-o3-app-version",
    "x-o3-os-version",
}


def response(flow: http.HTTPFlow) -> None:
    """Вызывается mitmproxy на каждый HTTP ответ."""
    # Перехватываем PVZ токен из ozonIdCookie/V3
    if flow.request.host == PVZ_AUTH_HOST and flow.request.path.startswith(PVZ_AUTH_PATH):
        _handle_pvz_auth(flow)
        return

    # Перехватываем WB токены из r-point.wb.ru/api/v1/refresh
    if flow.request.host == WB_REFRESH_HOST and flow.request.path.startswith(WB_REFRESH_PATH):
        _handle_wb_refresh(flow)
        return

    if flow.request.host != TARGET_HOST:
        return
    if not flow.request.path.startswith(TARGET_PATH):
        return
    if flow.response.status_code != 200:
        return

    # Сначала пробуем взять куки из Set-Cookie ответа (если они обновились)
    new_cookies = _parse_set_cookies(flow.response)

    # Если в ответе нет новых кук — берём из запроса (куки уже актуальны)
    request_cookies = _parse_request_cookies(flow.request)
    if not new_cookies and not request_cookies:
        print("[ozon-addon] actionV2TokenUpdate: куки не найдены")
        return

    # Мёржим: Set-Cookie ответа приоритетнее (свежее)
    merged = {**request_cookies, **new_cookies}
    _update_session_file(merged)

    source = "Set-Cookie ответа" if new_cookies else "Cookie запроса"
    print(f"[ozon-addon] ✅ SSO куки сохранены из {source}: {sorted(merged.keys())}")


def _handle_pvz_auth(flow: http.HTTPFlow) -> None:
    """Сохраняет PVZ токены из ответа ozonIdCookie/V3."""
    if flow.response.status_code != 200:
        return
    try:
        import json as _json, base64
        data = _json.loads(flow.response.get_content())
        token = data.get("token")
        refresh = data.get("refreshToken")
        if not token:
            return

        def _decode_exp(jwt):
            try:
                part = jwt.split(".")[1]
                part += "=" * (4 - len(part) % 4)
                claims = _json.loads(base64.b64decode(part))
                return claims.get("exp", 0) * 1000
            except Exception:
                return int(time.time() + 13 * 3600) * 1000

        token_data = {
            "access_token": token,
            "refresh_token": refresh,
            "expire_time": _decode_exp(token),
            "refresh_expire_time": _decode_exp(refresh) if refresh else 0,
        }
        os.makedirs(os.path.dirname(PVZ_TOKEN_FILE), exist_ok=True)
        with open(PVZ_TOKEN_FILE, "w") as f:
            _json.dump(token_data, f, indent=2)
        os.chmod(PVZ_TOKEN_FILE, 0o600)

        # Также сохраняем SSO куки из запроса
        request_cookies = _parse_request_cookies(flow.request)
        if request_cookies:
            _update_session_file(request_cookies)

        print(f"[ozon-addon] ✅ PVZ токен сохранён из ozonIdCookie/V3")
    except Exception as e:
        print(f"[ozon-addon] ❌ Ошибка при сохранении PVZ токена: {e}")


def _handle_wb_refresh(flow: http.HTTPFlow) -> None:
    """Сохраняет WB токены из ответа r-point.wb.ru/api/v1/refresh."""
    if flow.response.status_code != 200:
        return
    try:
        import json as _json, base64
        data = _json.loads(flow.response.get_content())
        new_access = data.get("access", {}).get("token")
        new_refresh = data.get("refresh", {}).get("token")
        if not new_access:
            return

        def _jwt_exp(jwt):
            try:
                part = jwt.split(".")[1]
                part += "=" * (4 - len(part) % 4)
                return int(_json.loads(base64.b64decode(part)).get("exp", 0))
            except Exception:
                return 0

        # Читаем pickpoint_id из существующего файла
        try:
            with open(WB_TOKEN_FILE) as f:
                existing = _json.load(f)
        except Exception:
            existing = {}

        token_data = {
            **existing,
            "x_token": new_access,
            "exp": _jwt_exp(new_access),
            "refresh_token": new_refresh,
            "refresh_exp": _jwt_exp(new_refresh),
        }
        os.makedirs(os.path.dirname(WB_TOKEN_FILE), exist_ok=True)
        with open(WB_TOKEN_FILE, "w") as f:
            _json.dump(token_data, f, indent=2)
        os.chmod(WB_TOKEN_FILE, 0o600)
        print("[ozon-addon] ✅ WB токены сохранены из r-point/refresh")
    except Exception as e:
        print(f"[ozon-addon] ❌ Ошибка при сохранении WB токена: {e}")


def _parse_set_cookies(response: http.Response) -> dict:
    """Парсит Set-Cookie заголовки ответа."""
    cookies = {}
    for value in response.headers.get_all("set-cookie"):
        parts = [p.strip() for p in value.split(";")]
        if not parts or "=" not in parts[0]:
            continue
        cookie_name, cookie_value = parts[0].split("=", 1)
        cookie_name = cookie_name.strip()
        if cookie_name not in SSO_COOKIE_NAMES:
            continue

        expires = int(time.time()) + 365 * 24 * 3600  # по умолчанию +1 год
        for part in parts[1:]:
            if part.lower().startswith("expires="):
                # Пропускаем парсинг даты — берём 1 год
                break

        cookies[cookie_name] = {
            "name": cookie_name,
            "value": cookie_value.strip(),
            "domain": ".ozon.ru",
            "path": "/",
            "httpOnly": cookie_name in {
                "__Secure-access-token", "__Secure-refresh-token", "__Secure-user-id"
            },
            "secure": True,
            "sameSite": "Lax",
            "expires": expires,
        }
    return cookies


def _parse_request_cookies(request: http.Request) -> dict:
    """Читает SSO куки из Cookie заголовка запроса."""
    cookies = {}
    expire_ts = int(time.time()) + 365 * 24 * 3600
    for cookie_str in request.headers.get_all("cookie"):
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            cname, cval = pair.split("=", 1)
            cname = cname.strip()
            if cname not in SSO_COOKIE_NAMES and cname not in EXTRA_COOKIE_NAMES:
                continue
            cookies[cname] = {
                "name": cname,
                "value": cval.strip(),
                "domain": ".ozon.ru",
                "path": "/",
                "httpOnly": cname in {
                    "__Secure-access-token", "__Secure-refresh-token", "__Secure-user-id"
                },
                "secure": True,
                "sameSite": "Lax",
                "expires": expire_ts,
            }
    return cookies


def _update_session_file(new_cookies: dict) -> None:
    """Обновляет ozon_session.json новыми SSO куками."""
    try:
        with open(SESSION_FILE) as f:
            state = json.load(f)
        existing = {c["name"]: c for c in state.get("cookies", [])}
    except Exception:
        existing = {}
        state = {"cookies": [], "origins": []}

    existing.update(new_cookies)
    state["cookies"] = list(existing.values())

    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    with open(SESSION_FILE, "w") as f:
        json.dump(state, f, indent=2)
    os.chmod(SESSION_FILE, 0o600)
