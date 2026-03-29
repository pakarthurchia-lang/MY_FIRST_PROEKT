"""
Авторизация Ozon через бот.

Флоу /login (Web токен через Chrome):
  /login → бот просит телефон → Chrome открывает id.ozon.ru →
  → Ozon отправляет код на почту/SMS → пользователь вводит код →
  → бот получает Web PVZ токен → reports и fines работают

Работает на Mac/Linux с установленным Chrome.
"""
import html
import json
import os
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import OWNER_CHAT_ID, OZON_PHONE, WB_PHONE

router = Router()


class OzonLoginState(StatesGroup):
    waiting_phone = State()
    waiting_code = State()


class WbLoginState(StatesGroup):
    waiting_phone = State()
    waiting_code = State()


# ── /login ────────────────────────────────────────────────────────────────────

@router.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    await state.clear()

    if OZON_PHONE:
        await _start_web_login(message, state, OZON_PHONE)
    else:
        await state.set_state(OzonLoginState.waiting_phone)
        await message.answer(
            "📱 <b>Авторизация Ozon</b>\n\n"
            "Введи номер телефона или email аккаунта Ozon PVZ\n"
            "(например: +79991234567 или user@mail.ru):",
            parse_mode="HTML",
        )


@router.message(OzonLoginState.waiting_phone)
async def handle_phone(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    await _start_web_login(message, state, message.text.strip())


async def _start_web_login(message: Message, state: FSMContext, phone: str):
    """Запускает Chrome-логин и ждёт код."""
    # Нормализуем телефон (email не трогаем)
    if "@" not in phone:
        phone = "".join(c for c in phone if c.isdigit() or c == "+")
        if not phone.startswith("+"):
            phone = "+7" + phone.lstrip("78") if phone.startswith(("7", "8")) else "+" + phone

    await message.answer(
        f"⏳ Открываю страницу логина Ozon для <b>{phone}</b>...\n"
        f"(Chrome headless)",
        parse_mode="HTML",
    )

    # Запускаем Chrome-логин
    try:
        from ozon.web_login import login_ozon_web
    except ImportError as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Chrome не настроен:</b>\n<code>{e}</code>\n\n"
            f"Установи: pip install undetected-chromedriver",
            parse_mode="HTML",
        )
        return

    await state.set_state(OzonLoginState.waiting_code)

    # callback — вызывается когда Chrome ждёт код
    # Просто отправляет сообщение; реальный код берётся через _get_pending_code_sync
    async def get_code() -> str:
        await message.answer(
            "📨 <b>Ozon отправил код подтверждения</b>\n\n"
            "Введи код из SMS или email:",
            parse_mode="HTML",
        )

    # callback — статусные сообщения
    async def on_status(msg: str):
        try:
            await message.answer(f"🔄 {msg}")
        except Exception:
            pass

    try:
        token_data = await login_ozon_web(
            phone=phone,
            get_code=get_code,
            on_status=on_status,
        )
    except RuntimeError as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Ошибка логина:</b>\n\n<code>{html.escape(str(e))}</code>\n\n"
            f"Попробуй /login снова или /ozon_token для ручного обновления.",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Неожиданная ошибка:</b>\n\n<code>{html.escape(f'{type(e).__name__}: {e}')}</code>",
            parse_mode="HTML",
        )
        return

    await state.clear()

    # Сбрасываем кэш токена
    from ozon import http_client
    http_client._token_data = {}

    # Определяем тип токена
    from ozon.http_client import _jwt_decode
    claims = _jwt_decode(token_data.get("access_token", ""))
    client_type = claims.get("ClientType", "?")

    await message.answer(
        f"✅ <b>Ozon авторизация успешна!</b>\n\n"
        f"Тип токена: <b>{client_type}</b>\n"
        f"{'📊 Reports и прибыль доступны!' if client_type == 'Web' else '⚠️ Mobile токен — reports ограничены'}\n\n"
        f"Токен будет автоматически обновляться.",
        parse_mode="HTML",
    )


@router.message(OzonLoginState.waiting_code)
async def handle_code(message: Message, state: FSMContext):
    """Получает код от пользователя и передаёт в Chrome."""
    if message.from_user.id != OWNER_CHAT_ID:
        return

    if not message.text:
        # Фото/файл/стикер — игнорируем, ждём текстовый код
        return

    code = message.text.strip()
    data = await state.get_data()

    # Находим Future и устанавливаем результат
    # Future передаётся через замыкание в _start_web_login
    # Здесь мы не можем напрямую обратиться к Future,
    # поэтому используем глобальный механизм
    _set_pending_code(code)

    await message.answer("⏳ Проверяю код...")


# Механизм передачи кода между async handler и Chrome thread (для Ozon)
import concurrent.futures as _cf
import base64 as _b64
_code_future: "_cf.Future | None" = None


def _set_pending_code(code: str):
    global _code_future
    if _code_future is not None and not _code_future.done():
        _code_future.set_result(code)


def _get_pending_code_sync(timeout: float = 300) -> str:
    global _code_future
    _code_future = _cf.Future()
    try:
        return _code_future.result(timeout=timeout)
    except Exception:
        return ""
    finally:
        _code_future = None


def _jwt_decode_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(_b64.b64decode(payload))
    except Exception:
        return {}


# ── /wb_token — ручной ввод PVZ x-token из mitmproxy ─────────────────────────

@router.message(Command("wb_token"))
async def cmd_wb_token(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "🔑 <b>Ручная установка WB PVZ токена</b>\n\n"
            "Как получить PVZ-токен:\n"
            "1. Установи <b>mitmproxy</b> на Mac\n"
            "2. Настрой прокси на iPhone → открой WB Point → перейди на экран выплат\n"
            "3. mitmproxy перехватит запрос к <code>r-point.wb.ru/api/v1/refresh</code>\n"
            "4. Скопируй значения <code>access.token</code> и <code>refresh.token</code>\n\n"
            "Или вставь JSON напрямую:\n"
            "<code>/wb_token {\"x_token\": \"eyJ...\", \"refresh_token\": \"eyJ...\", \"pickpoint_id\": 50016046}</code>",
            parse_mode="HTML",
        )
        return

    raw = parts[1].strip()
    try:
        data = json.loads(raw)
    except Exception:
        # Может быть просто токен без JSON
        if raw.startswith("eyJ"):
            data = {"x_token": raw}
        else:
            await message.answer("❌ Не удалось разобрать. Передай JSON или просто токен eyJ...")
            return

    import base64 as _b64, time as _time
    def _decode(tok):
        try:
            p = tok.split(".")[1]; p += "=" * (4 - len(p) % 4)
            return json.loads(_b64.b64decode(p))
        except Exception:
            return {}

    x_token = data.get("x_token") or data.get("access_token") or data.get("token")
    if not x_token:
        await message.answer("❌ Поле x_token не найдено.")
        return

    claims = _decode(x_token)
    pid = claims.get("pid", 0)
    xpid = claims.get("xpid", 0)
    exp = claims.get("exp", int(_time.time()) + 86400)

    refresh_token = data.get("refresh_token")
    pickpoint_id = data.get("pickpoint_id") or xpid or None

    token_data = {
        "x_token": x_token,
        "exp": exp,
        "pickpoint_id": pickpoint_id,
        "token_type": "mobile",
    }
    if refresh_token:
        rc = _decode(refresh_token)
        token_data["refresh_token"] = refresh_token
        token_data["refresh_exp"] = rc.get("exp", int(_time.time()) + 90 * 86400)

    import os as _os
    _os.makedirs("data", exist_ok=True)
    with open("data/wb_token.json", "w") as f:
        json.dump(token_data, f, indent=2)
    _os.chmod("data/wb_token.json", 0o600)

    from wildberries import http_client as wb_http
    wb_http._token_cache.clear()

    try:
        await message.delete()
    except Exception:
        pass

    remaining_h = max(0, int((exp - _time.time()) / 3600))
    await message.answer(
        f"✅ <b>WB PVZ токен сохранён!</b>\n\n"
        f"pid={pid}, xpid={xpid}\n"
        f"ПВЗ ID: <b>{pickpoint_id}</b>\n"
        f"Действует: <b>~{remaining_h}ч</b>\n"
        f"{'♻️ Refresh token (90 дней) — бот обновит автоматически' if refresh_token else '⚠️ Без refresh token'}",
        parse_mode="HTML",
    )


# ── /wb_login ─────────────────────────────────────────────────────────────────

@router.message(Command("wb_login"))
async def cmd_wb_login(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    await state.clear()

    if WB_PHONE:
        await _start_wb_web_login(message, state, WB_PHONE)
    else:
        await state.set_state(WbLoginState.waiting_phone)
        await message.answer(
            "📱 <b>Авторизация Wildberries ПВЗ</b>\n\n"
            "Введи номер телефона аккаунта WB ПВЗ\n"
            "(например: +79991234567):",
            parse_mode="HTML",
        )


@router.message(WbLoginState.waiting_phone)
async def handle_wb_phone(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    await _start_wb_web_login(message, state, message.text.strip())


async def _start_wb_web_login(message: Message, state: FSMContext, phone: str):
    """Шаг 1: отправляем SMS через WB Point mobile API."""
    from wildberries.mobile_login import send_sms, _normalize_phone
    phone_norm = _normalize_phone(phone)

    await message.answer(
        f"⏳ Отправляю SMS на <b>+{phone_norm}</b>...",
        parse_mode="HTML",
    )

    try:
        session_token = await send_sms(phone_norm)
    except RuntimeError as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Ошибка отправки SMS:</b>\n\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Неожиданная ошибка:</b>\n\n<code>{html.escape(f'{type(e).__name__}: {e}')}</code>",
            parse_mode="HTML",
        )
        return

    # Сохраняем session_token в FSM state
    await state.update_data(wb_session_token=session_token)
    await state.set_state(WbLoginState.waiting_code)

    await message.answer(
        "📨 <b>SMS отправлена!</b>\n\nВведи код из SMS:",
        parse_mode="HTML",
    )


@router.message(WbLoginState.waiting_code)
async def handle_wb_code(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    if not message.text:
        return

    code = message.text.strip()
    data = await state.get_data()
    session_token = data.get("wb_session_token")

    if not session_token:
        await state.clear()
        await message.answer("❌ Сессия устарела. Попробуй /wb_login снова.")
        return

    await message.answer("⏳ Проверяю код...")

    from wildberries.mobile_login import verify_code
    try:
        token_data = await verify_code(session_token, code)
    except RuntimeError as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Ошибка верификации:</b>\n\n<code>{html.escape(str(e))}</code>\n\n"
            f"Попробуй /wb_login снова.",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Неожиданная ошибка:</b>\n\n<code>{html.escape(f'{type(e).__name__}: {e}')}</code>",
            parse_mode="HTML",
        )
        return

    await state.clear()

    from wildberries import http_client as wb_http
    wb_http._token_cache.clear()

    import time as _time
    exp = token_data.get("exp", 0)
    remaining_h = max(0, int((exp - _time.time()) / 3600))
    has_refresh = bool(token_data.get("refresh_token"))
    pid = token_data.get("pickpoint_id")
    claims_pid = _jwt_decode_claims(token_data.get("x_token", ""))

    await message.answer(
        f"✅ <b>WB авторизация успешна!</b>\n\n"
        f"ПВЗ ID: <b>{pid}</b>\n"
        f"pid={claims_pid.get('pid')}, xpid={claims_pid.get('xpid')}\n"
        f"Токен действует: <b>~{remaining_h}ч</b>\n"
        f"{'♻️ Refresh token получен (90 дней)!' if has_refresh else '⚠️ Refresh token не получен'}",
        parse_mode="HTML",
    )


# ── /wb_debug — диагностика validate endpoint ────────────────────────────────

@router.message(Command("wb_debug"))
async def cmd_wb_debug(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    import aiohttp
    import json as _json
    import time as _time

    try:
        with open("data/wb_token.json") as f:
            token_data = _json.load(f)
    except Exception as e:
        await message.answer(f"❌ Не могу прочитать wb_token.json: {e}")
        return

    x_token = token_data.get("x_token", "")
    pickpoint_id = token_data.get("pickpoint_id")
    wb_cookies = token_data.get("wb_cookies", {})

    await message.answer(
        f"🔍 WB Debug\n"
        f"x-token: {x_token[:40]}...\n"
        f"pid/xpid в JWT: проверяю...\n"
        f"Куки: {len(wb_cookies)} штук\n"
        f"pickpoint_id: {pickpoint_id}"
    )

    # 1. Попробуем r-point.wb.ru/api/v1/validate с куками (как делает браузер)
    timeout = aiohttp.ClientTimeout(total=10)
    headers_cookie = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://pvz-lk.wb.ru",
        "Referer": "https://pvz-lk.wb.ru/payments",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    }
    if pickpoint_id:
        headers_cookie["x-pickpoint-external-id"] = str(pickpoint_id)

    # Мобильные заголовки WB Point
    headers_mobile = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "x-app-type": "mobile",
        "x-app-version": "v3.61.0",
        "x-device-type": "ios",
        "User-Agent": "WBPoint/14287039 CFNetwork/3826.500.131 Darwin/24.5.0",
    }

    from config import WB_PHONE
    phone_digits = "".join(c for c in (WB_PHONE or "") if c.isdigit())

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Перебираем возможные endpoint'ы для отправки SMS (шаг 1 WB Point логина)
        # Хардкодим известный pickpoint (pid=72827, xpid=50016046)
        KNOWN_XPID = 50016046
        KNOWN_PID  = 72827
        refresh_token = token_data.get("refresh_token", "")

        headers_tok = {**headers_mobile, "x-token": x_token}
        headers_tok_xpid = {**headers_mobile, "x-token": x_token, "x-pickpoint-external-id": str(KNOWN_XPID)}

        await message.answer(f"🔍 Ищу PVZ-select endpoint (xpid={KNOWN_XPID})...")

        # Декодируем allowed поле из токена
        import base64 as _b64_dbg
        claims_now = _jwt_decode_claims(x_token)
        allowed_raw = claims_now.get("allowed", "")
        await message.answer(
            f"JWT: pid={claims_now.get('pid')}, xpid={claims_now.get('xpid')}\n"
            f"allowed[:80]: <code>{html.escape(allowed_raw[:80])}</code>",
            parse_mode="HTML"
        )
        if allowed_raw:
            try:
                decoded_hex = _b64_dbg.b64decode(allowed_raw + "==").hex()
                await message.answer(f"allowed hex: <code>{decoded_hex[:300]}</code>", parse_mode="HTML")
            except Exception as e:
                await message.answer(f"allowed decode: {e}")

        headers_tok_xpid = {**headers_mobile, "x-token": x_token, "x-pickpoint-external-id": str(KNOWN_XPID)}

        # Используем refresh_token прямо сейчас пока он свежий
        refresh_token = token_data.get("refresh_token", "")

        pvz_endpoints = [
            # Ключевой тест: refresh с xpid в заголовке
            ("POST", "https://r-point.wb.ru/api/v1/refresh",
             {"backoffice": False, "token": refresh_token}, headers_tok_xpid),
            # refresh с backoffice=true (другой режим)
            ("POST", "https://r-point.wb.ru/api/v1/refresh",
             {"backoffice": True, "token": refresh_token}, headers_tok_xpid),
            # Список доступных сервисов на api-discovery
            ("GET",  "https://api-discovery.wb.ru/api/v1/",                   None, headers_tok),
            ("GET",  "https://api-discovery.wb.ru/",                          None, headers_tok),
            # s-point — попробуем разные пути
            ("GET",  "https://s-point.wb.ru/s3/api/v2/user/info",             None, headers_tok),
            ("GET",  "https://s-point.wb.ru/s9/api/v2/user/info",             None, headers_tok),
            ("GET",  "https://s-point.wb.ru/s3/api/v1/pvz/list",              None, headers_tok),
            ("GET",  "https://s-point.wb.ru/s9/api/v1/pvz/list",              None, headers_tok),
            # r-point с другими путями
            ("GET",  "https://r-point.wb.ru/api/v1/pvz",                      None, headers_tok),
            ("GET",  "https://r-point.wb.ru/api/v1/pvz/list",                 None, headers_tok),
        ]

        for method, url, body, hdrs in pvz_endpoints:
            try:
                if method == "GET":
                    async with session.get(url, headers=hdrs) as resp:
                        resp_body = await resp.text()
                else:
                    async with session.post(url, headers=hdrs, json=body) as resp:
                        resp_body = await resp.text()
                short = url.replace("https://r-point.wb.ru", "")
                # Декодируем токен если 200
                note = ""
                if resp.status == 200:
                    try:
                        d = _json.loads(resp_body)
                        at = (d.get("access") or {}).get("token") or d.get("token")
                        if at:
                            c = _jwt_decode_claims(at)
                            note = f"\n🔑 pid={c.get('pid')}, xpid={c.get('xpid')}"
                    except Exception:
                        pass
                await message.answer(
                    f"<code>{method} {short}</code> → {resp.status}:{note}\n"
                    f"<code>{html.escape(resp_body[:600])}</code>",
                    parse_mode="HTML"
                )
            except Exception as e:
                short = url.replace("https://r-point.wb.ru", "")
                await message.answer(f"<code>{method} {short}</code> → ошибка: {e}", parse_mode="HTML")

    await message.answer("✅ Debug завершён")


# ── /wb_spy — открывает Chrome с токеном и смотрит что pvz-lk.wb.ru запрашивает ──

@router.message(Command("wb_spy"))
async def cmd_wb_spy(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    await message.answer("🕵️ Открываю Chrome с сохранённым токеном, иду на /payments...")

    import asyncio
    loop = asyncio.get_event_loop()

    def _spy():
        import undetected_chromedriver as uc
        import json as _j
        import time as _t

        try:
            with open("data/wb_token.json") as f:
                tok = _j.load(f)
        except Exception as e:
            return [f"Не могу прочитать токен: {e}"]

        x_token = tok.get("x_token", "")
        wb_cookies = tok.get("wb_cookies", {})

        opts = uc.ChromeOptions()
        opts.add_argument("--window-size=1280,800")
        opts.page_load_strategy = "eager"
        opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        driver = uc.Chrome(options=opts, version_main=146)
        driver.set_page_load_timeout(60)

        try:
            driver.execute_cdp_cmd("Network.enable", {})

            # Инжектируем токен в localStorage перед навигацией
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": f"""
                window._wbSpy = {{calls: []}};
                var origFetch = window.fetch;
                window.fetch = function(input, init) {{
                    var url = typeof input === 'string' ? input : (input && input.url ? input.url : '');
                    var entry = {{url: url, method: (init&&init.method)||'GET', reqBody: null, status: null, respBody: null}};
                    if (init && init.body) {{
                        try {{ entry.reqBody = typeof init.body === 'string' ? init.body.slice(0,200) : JSON.stringify(init.body).slice(0,200); }} catch(e){{}}
                    }}
                    window._wbSpy.calls.push(entry);
                    return origFetch.call(window, input, init).then(function(r) {{
                        entry.status = r.status;
                        r.clone().text().then(function(t) {{ entry.respBody = t.slice(0,300); }}).catch(function(){{}});
                        return r;
                    }});
                }};
            """})

            # Вставляем куки через CDP ДО любой навигации
            for name, value in wb_cookies.items():
                try:
                    driver.execute_cdp_cmd("Network.setCookie", {
                        "name": name, "value": value,
                        "domain": ".wb.ru", "path": "/",
                        "secure": True, "httpOnly": False,
                    })
                except Exception:
                    pass

            # Также вставляем x-token как куку
            if x_token:
                for cookie_name in ["x-token", "wb_token", "WBToken"]:
                    try:
                        driver.execute_cdp_cmd("Network.setCookie", {
                            "name": cookie_name, "value": x_token,
                            "domain": ".wb.ru", "path": "/",
                            "secure": True,
                        })
                    except Exception:
                        pass

            # Идём на /payments напрямую
            try:
                driver.get("https://pvz-lk.wb.ru/payments")
            except Exception:
                pass
            _t.sleep(3)

            # Вставляем токен в localStorage (приложение может читать оттуда)
            driver.execute_script(f"""
                try {{ localStorage.setItem('wb-token', arguments[0]); }} catch(e) {{}}
                try {{ localStorage.setItem('x-token', arguments[0]); }} catch(e) {{}}
                try {{ localStorage.setItem('pvz-x-token', arguments[0]); }} catch(e) {{}}
            """, x_token)

            _t.sleep(10)

            # Кликаем на раздел если нужно
            driver.execute_script("""
                var kws = ['выплат', 'история', 'вознаграждени', 'начислени'];
                var els = Array.from(document.querySelectorAll('a,button,[role="tab"]'));
                for (var kw of kws) {
                    for (var el of els) {
                        var r = el.getBoundingClientRect();
                        if (r.width > 0 && el.textContent.toLowerCase().includes(kw)) { el.click(); break; }
                    }
                }
            """)
            _t.sleep(8)

            # Собираем JS перехваченные вызовы
            spy = driver.execute_script("return window._wbSpy || null;")
            results = []
            if spy:
                for c in spy.get("calls", []):
                    url = c.get("url", "")
                    if any(x in url for x in [".js", ".css", ".wasm", ".png", "version.json"]):
                        continue
                    results.append(
                        f"[{c.get('method')}] {url[-90:]}\n"
                        f"  req: {(c.get('reqBody') or '')[:100]}\n"
                        f"  {c.get('status')}: {(c.get('respBody') or '')[:200]}"
                    )

            # CDP логи
            import json as _jj
            try:
                logs = driver.get_log("performance")
                for entry in logs:
                    msg = _jj.loads(entry["message"])["message"]
                    if msg.get("method") == "Network.requestWillBeSent":
                        params = msg.get("params", {})
                        req = params.get("request", {})
                        url = req.get("url", "")
                        h = req.get("headers", {})
                        h_low = {k.lower(): v for k, v in h.items()}
                        if any(d in url for d in ["point-balance", "pvz-lk.wb.ru/api", "r-point", "s-point"]):
                            tok_preview = h_low.get("x-token", "")[:20]
                            xpid = h_low.get("x-pickpoint-external-id", "")
                            results.append(f"CDP [{req.get('method','GET')}] {url[-90:]} | tok={tok_preview} xpid={xpid}")
            except Exception:
                pass

            return results or ["Нет API вызовов (кроме статики)"]
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    try:
        results = await loop.run_in_executor(None, _spy)
        for chunk in results[:15]:
            try:
                await message.answer(f"<code>{html.escape(chunk)}</code>", parse_mode="HTML")
            except Exception:
                pass
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

    await message.answer("✅ spy завершён")


# ── /setup — установка закладок ───────────────────────────────────────────────

@router.message(Command("setup"))
async def cmd_setup(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "setup", "bookmarklets.html")
    html_path = os.path.normpath(html_path)

    await message.answer(
        "📎 <b>Установка кнопок для браузера</b>\n\n"
        "Открой файл ниже на компьютере в браузере и перетащи кнопки в панель закладок.\n\n"
        "После этого обновлять токен будет просто:\n"
        "нажать кнопку → скопировать → вставить в бота.",
        parse_mode="HTML",
    )
    await message.answer_document(
        FSInputFile(html_path, filename="Настройка ПВЗ бота.html"),
    )


# ── /ozon_token — ручное обновление (запасной вариант) ────────────────────────

@router.message(Command("ozon_token"))
async def cmd_ozon_token(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "🔑 <b>Ручное обновление Ozon токена</b>\n\n"
            "Основной способ — через бота: /login\n\n"
            "Запасной (если /login не работает):\n"
            "1. Открой <b>turbo-pvz.ozon.ru</b> в браузере\n"
            "2. DevTools → Консоль (F12 или ⌘+Option+C)\n"
            "3. Введи: <code>localStorage.getItem('pvz-access-token')</code>\n"
            "4. Скопируй результат и отправь:\n"
            "<code>/ozon_token {вставь сюда}</code>",
            parse_mode="HTML",
        )
        return

    raw = parts[1].strip()

    if raw.startswith('"') and raw.endswith('"'):
        try:
            raw = json.loads(raw)
        except Exception:
            pass

    try:
        data = json.loads(raw)
    except Exception:
        await message.answer(
            "❌ Не удалось разобрать JSON.\n"
            "Убедись что скопировал полное значение включая фигурные скобки."
        )
        return

    token_data = {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expire_time": data.get("expire_time"),
        "refresh_expire_time": data.get("refresh_expire_time"),
    }
    if not token_data["access_token"]:
        await message.answer("❌ Поле access_token не найдено в данных.")
        return

    from ozon.http_client import _save_token
    _save_token(token_data)

    from ozon import http_client
    http_client._token_data = {}

    try:
        await message.delete()
    except Exception:
        pass

    await message.answer("✅ Ozon токен обновлён и сохранён.")
