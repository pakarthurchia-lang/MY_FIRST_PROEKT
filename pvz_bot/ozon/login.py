"""
Авторизация в Ozon PVZ (turbo-pvz.ozon.ru) через прямые API-запросы.

Не использует браузер и Playwright — работает на любом сервере/мини-ПК.

Ozon PVZ использует собственный auth API (/api2/auth/v1/).
Флоу: отправить логин → получить SMS/email → подтвердить код → токены.
"""
import aiohttp
from ozon.http_client import BASE_URL, HEADERS_BASE, _save_token

_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Возможные пути для отправки кода (попробуем по порядку)
_SEND_CODE_URLS = [
    f"{BASE_URL}/api2/auth/v1/send-code",
    f"{BASE_URL}/api2/auth/v1/login",
    f"{BASE_URL}/api2/auth/v1/sign-in",
]

# Возможные пути для верификации кода
_VERIFY_URLS = [
    f"{BASE_URL}/api2/auth/v1/verify",
    f"{BASE_URL}/api2/auth/v1/confirm",
    f"{BASE_URL}/api2/auth/v1/login/confirm",
]


async def send_login_code(login: str) -> dict:
    """
    Отправляет OTP-код на телефон или email.

    login: номер телефона (+79...) или email
    Возвращает ответ сервера (нужен для последующего verify).
    Бросает RuntimeError с полным ответом сервера если все эндпоинты не сработали.
    """
    login_type = "phone" if login.startswith("+") or login.lstrip("+").isdigit() else "email"

    # Нормализуем телефон — убираем пробелы, скобки, тире
    if login_type == "phone":
        login = "".join(c for c in login if c.isdigit() or c == "+")
        if not login.startswith("+"):
            login = "+7" + login.lstrip("78") if login.startswith(("7", "8")) else "+" + login

    last_error = None
    for url in _SEND_CODE_URLS:
        try:
            result = await _try_send_code(url, login, login_type)
            return {"url": url, "login": login, "type": login_type, "response": result}
        except RuntimeError as e:
            last_error = str(e)
            if "404" not in str(e):
                # Если не 404 — сервер ответил, это уже что-то значит
                raise
            continue

    raise RuntimeError(
        f"Не удалось найти эндпоинт авторизации.\n"
        f"Последняя ошибка: {last_error}\n\n"
        f"Нужно перехватить запрос в DevTools при ручном входе на turbo-pvz.ozon.ru\n"
        f"и обновить _SEND_CODE_URLS в ozon/login.py"
    )


async def _try_send_code(url: str, login: str, login_type: str) -> dict:
    """Пробует один конкретный эндпоинт для отправки кода."""
    bodies = [
        {"login": login, "type": login_type},
        {login_type: login},
        {"phone": login} if login_type == "phone" else {"email": login},
    ]
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        for body in bodies:
            async with session.post(url, json=body, headers=HEADERS_BASE) as resp:
                text = await resp.text()
                if resp.status == 404:
                    raise RuntimeError(f"404 {url}")
                if resp.status in (200, 201):
                    try:
                        return resp.json() and await resp.json() or {"raw": text}
                    except Exception:
                        return {"raw": text}
                if resp.status == 400:
                    # 400 может значить неправильный формат тела — пробуем следующий
                    continue
                # Любой другой статус — пробрасываем с деталями
                raise RuntimeError(f"Ошибка {resp.status} от {url}: {text[:500]}")
    raise RuntimeError(f"Все форматы тела запроса отклонены для {url}")


async def verify_login_code(ctx: dict, code: str) -> dict:
    """
    Подтверждает OTP-код и сохраняет токены.

    ctx: словарь из send_login_code (содержит url, login, type, response)
    code: код из SMS или email
    Возвращает token_data и сохраняет в data/ozon_token.json.
    """
    login = ctx["login"]
    login_type = ctx["type"]
    code = code.strip()

    last_error = None
    for url in _VERIFY_URLS:
        try:
            data = await _try_verify(url, login, login_type, code, ctx.get("response", {}))
            token_data = _extract_tokens(data, url)
            _save_token(token_data)
            return token_data
        except RuntimeError as e:
            last_error = str(e)
            if "404" not in str(e):
                raise
            continue

    raise RuntimeError(
        f"Не удалось найти эндпоинт подтверждения.\n"
        f"Последняя ошибка: {last_error}"
    )


async def _try_verify(url: str, login: str, login_type: str, code: str, send_resp: dict) -> dict:
    """Пробует один конкретный эндпоинт верификации."""
    # Попробуем разные форматы тела
    bodies = [
        {"login": login, "type": login_type, "code": code},
        {login_type: login, "code": code},
        {"phone": login, "code": code} if login_type == "phone" else {"email": login, "code": code},
    ]
    # Если в ответе на send_code был session_id или подобное — добавляем
    for key in ("session_id", "sessionId", "token", "requestId", "request_id"):
        if key in send_resp:
            for b in bodies:
                b[key] = send_resp[key]

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        for body in bodies:
            async with session.post(url, json=body, headers=HEADERS_BASE) as resp:
                text = await resp.text()
                if resp.status == 404:
                    raise RuntimeError(f"404 {url}")
                if resp.status in (200, 201):
                    try:
                        return await resp.json()
                    except Exception:
                        return {"raw": text}
                if resp.status == 400:
                    continue
                raise RuntimeError(f"Ошибка {resp.status} от {url}: {text[:500]}")
    raise RuntimeError(f"Верификация не удалась ни с одним форматом тела для {url}")


def _extract_tokens(data: dict, source_url: str) -> dict:
    """Извлекает токены из ответа сервера (разные форматы полей)."""
    access = (
        data.get("access_token") or data.get("accessToken") or
        data.get("token") or data.get("pvzAccessToken")
    )
    refresh = (
        data.get("refresh_token") or data.get("refreshToken") or
        data.get("refreshToken")
    )
    expire = data.get("expire_time") or data.get("expireTime") or data.get("expiresIn")
    refresh_expire = data.get("refresh_expire_time") or data.get("refreshExpireTime")

    if not access:
        raise RuntimeError(
            f"Токен не найден в ответе сервера.\n"
            f"Ответ от {source_url}: {data}"
        )

    return {
        "access_token": access,
        "refresh_token": refresh,
        "expire_time": expire,
        "refresh_expire_time": refresh_expire,
    }
