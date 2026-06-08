"""
Автоматический логин в WB ПВЗ кабинет через undetected Chrome.

Флоу:
  1. Открывает Chrome → pvz-lk.wb.ru
  2. Вводит телефон → WB отправляет SMS
  3. Ждёт код от пользователя (через callback)
  4. Вводит код → перехватывает x-token из исходящих запросов браузера
  5. Сохраняет токен в data/wb_token.json

Используется из bot/handlers/auth.py при команде /wb_login.
"""
import asyncio
import base64
import json
import os
import time
from typing import Callable, Awaitable, Optional

TOKEN_FILE = "data/wb_token.json"
WB_PVZ_URL = "https://pvz-lk.wb.ru"

# Переопределяется из auth.py чтобы использовать WB future (не Ozon)
_get_pending_code_sync_override: Optional[Callable] = None


def _jwt_decode(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.b64decode(payload))
    except Exception:
        return {}


def _jwt_exp(token: str) -> int:
    return int(_jwt_decode(token).get("exp", 0))


async def login_wb_web(
    phone: str,
    get_code: Callable[[], Awaitable[str]],
    on_status: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    """
    Выполняет логин в WB ПВЗ кабинет через Chrome и возвращает x-token.

    Args:
        phone: номер телефона (+7...)
        get_code: async callback, вызывается когда нужен SMS код
        on_status: async callback для статусных сообщений

    Returns:
        dict с x_token, exp, refresh_token, refresh_exp, pickpoint_id

    Raises:
        RuntimeError если логин не удался
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _chrome_login_sync(phone, loop, get_code, on_status),
    )
    return result


def _cdp_get_network_logs(driver) -> list:
    """Разбирает CDP performance logs и возвращает список запросов с requestId."""
    try:
        logs = driver.get_log("performance")
    except Exception:
        return []
    requests = []
    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
            method = msg.get("method", "")
            if method == "Network.requestWillBeSent":
                params = msg.get("params", {})
                req = params.get("request", {})
                requests.append({
                    "url": req.get("url", ""),
                    "headers": req.get("headers", {}),
                    "method": req.get("method", "GET"),
                    "requestId": params.get("requestId"),
                    "postData": req.get("postData", ""),
                })
            elif method == "Network.responseReceived":
                params = msg.get("params", {})
                resp = params.get("response", {})
                requests.append({
                    "url": resp.get("url", ""),
                    "status": resp.get("status"),
                    "resp_headers": resp.get("headers", {}),
                    "requestId": params.get("requestId"),
                    "_type": "response",
                })
        except Exception:
            pass
    return requests


def _cdp_get_response_body(driver, request_id: str) -> str:
    """Получает тело ответа через CDP Network.getResponseBody."""
    try:
        result = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
        body = result.get("body", "")
        if result.get("base64Encoded"):
            import base64 as _b64
            body = _b64.b64decode(body).decode("utf-8", errors="replace")
        return body
    except Exception:
        return ""


async def _validate_mobile(session_token: str, sms_code: str, status_fn=None) -> Optional[dict]:
    """
    Вызывает r-point.wb.ru/api/v1/validate из Python с мобильными заголовками.
    session_token + sms_code берём из CDP postData браузера.
    """
    from wildberries.mobile_login import (
        VALIDATE_URL, MOBILE_HEADERS, _jwt_decode as _mjwt, _jwt_exp,
    )
    import aiohttp as _aio
    try:
        async with _aio.ClientSession(timeout=_aio.ClientTimeout(total=20)) as sess:
            async with sess.post(
                VALIDATE_URL,
                headers=MOBILE_HEADERS,
                json={"token": session_token, "code": sms_code.strip()},
            ) as resp:
                raw = await resp.text()
                if status_fn:
                    status_fn(f"mobile validate HTTP {resp.status}: {raw[:200]}")
                if resp.status != 200:
                    return None
                result = json.loads(raw)
        access_token = (
            (result.get("access") or {}).get("token")
            or result.get("token") or result.get("accessToken")
        )
        refresh_token = (
            (result.get("refresh") or {}).get("token")
            or result.get("refreshToken") or result.get("refresh_token")
        )
        if not access_token:
            return None
        claims = _mjwt(access_token)
        return {
            "x_token": access_token,
            "exp": _jwt_exp(access_token) or int(time.time()) + 86400,
            "pickpoint_id": claims.get("xpid") or claims.get("pid"),
            "token_type": "mobile",
            "refresh_token": refresh_token or "",
            "refresh_exp": (_jwt_exp(refresh_token) if refresh_token else 0) or int(time.time()) + 90 * 86400,
        }
    except Exception as _e:
        if status_fn:
            status_fn(f"_validate_mobile exception: {_e}")
        return None


async def _select_pvz_mobile(general_token: str, refresh_tok: str, status_fn=None) -> Optional[dict]:
    """
    Апгрейд общего токена (xpid=0) до PVZ-scoped через POST /api/v1/refresh
    с заголовком x-pickpoint-external-id. Аналог mobile_login._try_select_pvz.
    """
    from wildberries.mobile_login import MOBILE_HEADERS, _jwt_decode as _mjwt, _jwt_exp
    from config import WB_PVZ_XPID
    import aiohttp as _aio
    xpid = WB_PVZ_XPID
    if not xpid:
        if status_fn:
            status_fn("WB_PVZ_XPID не задан — пропускаю PVZ-upgrade")
        return None
    headers = {
        **MOBILE_HEADERS,
        "x-token": general_token,
        "x-pickpoint-external-id": str(xpid),
    }
    try:
        async with _aio.ClientSession(timeout=_aio.ClientTimeout(total=20)) as sess:
            async with sess.post(
                "https://r-point.wb.ru/api/v1/refresh",
                headers=headers,
                json={"backoffice": False, "token": refresh_tok},
            ) as resp:
                raw = await resp.text()
                if status_fn:
                    status_fn(f"PVZ-upgrade HTTP {resp.status}: {raw[:200]}")
                if resp.status != 200:
                    return None
                result = json.loads(raw)
        new_access = (result.get("access") or {}).get("token")
        new_refresh = (result.get("refresh") or {}).get("token")
        if not new_access:
            return None
        claims = _mjwt(new_access)
        return {
            "x_token": new_access,
            "exp": _jwt_exp(new_access) or int(time.time()) + 86400,
            "pickpoint_id": claims.get("xpid") or claims.get("pid") or xpid,
            "token_type": "mobile",
            "refresh_token": new_refresh or refresh_tok,
            "refresh_exp": (_jwt_exp(new_refresh) if new_refresh else 0) or int(time.time()) + 90 * 86400,
        }
    except Exception as _e:
        if status_fn:
            status_fn(f"_select_pvz_mobile exception: {_e}")
        return None


def _chrome_login_sync(
    phone: str,
    loop: asyncio.AbstractEventLoop,
    get_code: Callable,
    on_status: Optional[Callable],
) -> dict:
    """Синхронная часть — работает в thread executor."""
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    def status(msg: str):
        if on_status:
            asyncio.run_coroutine_threadsafe(on_status(msg), loop)

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1280,720")
    options.page_load_strategy = "eager"
    # Включаем CDP performance logs для перехвата ВСЕХ сетевых запросов
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = None
    try:
        driver = uc.Chrome(options=options, version_main=148)
        driver.set_page_load_timeout(60)
        driver.implicitly_wait(5)

        # Включаем CDP Network monitoring
        try:
            driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass

        # Инжектируем перехватчик ПЕРЕД любой навигацией.
        # Перехватываем fetch/XHR и ловим x-token из исходящих запросов.
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': """
            window._wbCapture = {xToken: null, xTokenUrl: null, refreshToken: null, calls: [], validateReqs: []};

            // Перехватываем fetch — ловим x-token из headers
            var origFetch = window.fetch;
            window.fetch = function(input, init) {
                var url = typeof input === 'string' ? input : (input && input.url ? input.url : String(input));
                if (init && init.headers) {
                    var h = init.headers;
                    var tok = null;
                    if (typeof h.get === 'function') {
                        tok = h.get('x-token') || h.get('X-Token');
                    } else if (typeof h === 'object') {
                        tok = h['x-token'] || h['X-Token'];
                    }
                    if (tok && tok.startsWith('eyJ')) {
                        window._wbCapture.xToken = tok;
                        window._wbCapture.xTokenUrl = url;
                    }
                }
                // Захватываем тело запроса к validate/refresh
                var reqBody = null;
                if (url && url.includes('r-point.wb.ru') && init && init.body) {
                    try { reqBody = typeof init.body === 'string' ? init.body : JSON.stringify(init.body); } catch(e) {}
                    window._wbCapture.validateReqs.push({url: url, reqBody: reqBody, method: (init.method||'GET')});
                }
                var entry = {url: url, time: Date.now(), status: null, body: null};
                window._wbCapture.calls.push(entry);
                return origFetch.call(window, input, init).then(function(resp) {
                    entry.status = resp.status;
                    resp.clone().text().then(function(t) {
                        entry.body = t.slice(0, 400);
                        // Обновляем validateReqs с телом ответа
                        if (url && url.includes('r-point.wb.ru')) {
                            var last = window._wbCapture.validateReqs[window._wbCapture.validateReqs.length-1];
                            if (last && last.url === url) { last.respStatus = resp.status; last.respBody = t.slice(0, 400); }
                        }
                        // Ловим токен из тела ответа login endpoint
                        try {
                            var d = JSON.parse(t);
                            var at = d.token || d.accessToken || d.access_token;
                            var rt = d.refreshToken || d.refresh_token;
                            if (at && at.startsWith('eyJ')) { window._wbCapture.xToken = at; window._wbCapture.xTokenUrl = url + '[resp]'; }
                            if (rt && rt.startsWith('eyJ')) window._wbCapture.refreshToken = rt;
                            // Вложенная структура {"access": {"token": ...}, "refresh": {"token": ...}}
                            if (d.access && d.access.token) { window._wbCapture.xToken = d.access.token; window._wbCapture.xTokenUrl = url + '[resp.access]'; }
                            if (d.refresh && d.refresh.token) window._wbCapture.refreshToken = d.refresh.token;
                        } catch(e) {}
                    }).catch(function(){});
                    return resp;
                });
            };

            // Перехватываем XHR
            var origOpen = XMLHttpRequest.prototype.open;
            var origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
            var origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this._wbUrl = url; this._wbMethod = method;
                return origOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
                if (name.toLowerCase() === 'x-token' && value && value.startsWith('eyJ')) {
                    window._wbCapture.xToken = value;
                    window._wbCapture.xTokenUrl = this._wbUrl + '[xhr-header]';
                }
                return origSetHeader.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(body) {
                var self = this;
                // Логируем тело запроса к r-point.wb.ru
                if (self._wbUrl && self._wbUrl.includes('r-point.wb.ru')) {
                    var reqStr = '';
                    try { reqStr = typeof body === 'string' ? body.slice(0, 300) : (body ? JSON.stringify(body).slice(0, 300) : ''); } catch(e) {}
                    var entry = {url: self._wbUrl, method: self._wbMethod, reqBody: reqStr, respBody: null, respStatus: null};
                    window._wbCapture.validateReqs.push(entry);
                    this.addEventListener('load', function() {
                        entry.respBody = (self.responseText || '').slice(0, 800);
                        entry.respStatus = self.status;
                    });
                }
                this.addEventListener('load', function() {
                    try {
                        var d = JSON.parse(self.responseText || '');
                        var at = d.token || d.accessToken || d.access_token;
                        var rt = d.refreshToken || d.refresh_token;
                        if (at && at.startsWith('eyJ')) { window._wbCapture.xToken = at; window._wbCapture.xTokenUrl = self._wbUrl + '[xhr-resp]'; }
                        if (rt && rt.startsWith('eyJ')) window._wbCapture.refreshToken = rt;
                        if (d.access && d.access.token) { window._wbCapture.xToken = d.access.token; window._wbCapture.xTokenUrl = self._wbUrl + '[xhr-resp.access]'; }
                        if (d.refresh && d.refresh.token) window._wbCapture.refreshToken = d.refresh.token;
                    } catch(e) {}
                });
                return origSend.apply(this, arguments);
            };

            // localStorage.setItem — ловим JWT-подобные значения
            var origSet = Storage.prototype.setItem;
            Storage.prototype.setItem = function(k, v) {
                if (typeof v === 'string' && v.startsWith('eyJ') && v.length > 100) {
                    window._wbCapture.xToken = v;
                    window._wbCapture.xTokenUrl = 'localStorage:' + k;
                }
                return origSet.call(this, k, v);
            };
            """
        })

        # Step 1: Открываем pvz-lk.wb.ru
        status(f"Открываю pvz-lk.wb.ru...")
        try:
            driver.get(WB_PVZ_URL)
        except Exception:
            pass
        time.sleep(4)

        cur_url = driver.current_url
        status(f"URL: {cur_url[:80]}")

        # Step 2: Ищем кнопку входа (pvz-lk.wb.ru/login или кнопка "Войти" на главной)
        status("Ищу форму входа...")

        # Если не на странице входа — ищем кнопку "Войти" и кликаем
        _on_login_form = False
        all_inp = driver.find_elements(By.TAG_NAME, "input")
        if any(i.is_displayed() for i in all_inp):
            _on_login_form = True
        else:
            # Кликаем на кнопку "Войти" / "Вход" / "Авторизация"
            clicked_login = driver.execute_script("""
                var keywords = ['войти', 'вход', 'sign in', 'login', 'авторизация', 'authorize'];
                var btns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                for (var kw of keywords) {
                    for (var b of btns) {
                        var r = b.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && b.textContent.toLowerCase().includes(kw)) {
                            b.click();
                            return b.textContent.trim().slice(0, 40);
                        }
                    }
                }
                return null;
            """)
            if clicked_login:
                status(f"Нажата кнопка: '{clicked_login}'")
                time.sleep(3)
            else:
                # Навигация напрямую на страницу входа
                status("Перехожу на страницу входа напрямую...")
                try:
                    driver.get(f"{WB_PVZ_URL}/login")
                except Exception:
                    pass
                time.sleep(4)
                status(f"URL после /login: {driver.current_url[:80]}")

        # Диагностика что на странице
        page_btns = driver.execute_script("""
            var info = [];
            document.querySelectorAll('button,input,a').forEach(function(el) {
                var r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0)
                    info.push({tag:el.tagName, type:el.getAttribute('type')||'', text:el.textContent.trim().slice(0,30)});
            });
            return info.slice(0,10);
        """)
        status(f"Элементы на странице: {page_btns}")

        # Нормализуем телефон для ввода
        digits_only = "".join(c for c in phone if c.isdigit())
        # WB обычно принимает без +7 — пробуем разные форматы
        if digits_only.startswith("7") and len(digits_only) == 11:
            phone_variants = [digits_only[1:], digits_only, "+" + digits_only, phone]
        else:
            phone_variants = [digits_only, phone]

        typed = False
        for phone_to_type in phone_variants:
            typed = driver.execute_script("""
                function findPhoneInput(root) {
                    var selectors = [
                        'input[type="tel"]',
                        'input[inputmode="tel"]',
                        'input[inputmode="numeric"]',
                        'input[type="text"]',
                        'input:not([type="hidden"])',
                    ];
                    for (var sel of selectors) {
                        var inputs = root.querySelectorAll(sel);
                        for (var inp of inputs) {
                            var rect = inp.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) return inp;
                        }
                    }
                    return null;
                }
                var inp = findPhoneInput(document);
                if (!inp) return false;
                inp.focus();
                var nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                nativeSetter.call(inp, arguments[0]);
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            """, phone_to_type)
            if typed:
                status(f"Ввожу телефон: {phone_to_type}")
                break

        if not typed:
            all_inp = driver.find_elements(By.TAG_NAME, "input")
            inp_info = [(i.get_attribute("type"), i.get_attribute("placeholder"), i.is_displayed()) for i in all_inp]
            status(f"Инпуты: {inp_info}\nURL: {driver.current_url[:80]}")
            raise RuntimeError(
                f"Поле для телефона не найдено.\n"
                f"URL: {driver.current_url[:80]}\n"
                f"Inputs: {inp_info}"
            )

        status("Телефон введён — отмечаю чекбокс и сабмит...")
        time.sleep(0.5)

        # Отмечаем чекбокс "С правилами ознакомлен" если есть (без него кнопка неактивна)
        checked = driver.execute_script("""
            var checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"]'));
            for (var cb of checkboxes) {
                if (!cb.checked) {
                    cb.click();
                    return true;
                }
            }
            // Fallback: кликабельные label рядом с checkbox
            var labels = Array.from(document.querySelectorAll('label'));
            for (var lbl of labels) {
                var r = lbl.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) { lbl.click(); return 'label'; }
            }
            return false;
        """)
        if checked:
            status(f"Чекбокс отмечен: {checked}")
            time.sleep(0.5)

        # Кликаем кнопку "Получить код"
        driver.execute_script("""
            var btns = Array.from(document.querySelectorAll('button'));
            var keywords = ['получить', 'войти', 'далее', 'next', 'continue', 'отправить'];
            for (var kw of keywords) {
                for (var b of btns) {
                    var rect = b.getBoundingClientRect();
                    if (rect.width > 0 && b.textContent.toLowerCase().includes(kw)) {
                        b.click(); return;
                    }
                }
            }
            // Fallback: Enter в поле
            var inp = document.querySelector('input[type="tel"],input[type="text"]');
            if (inp) inp.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', keyCode:13, bubbles:true}));
        """)
        time.sleep(4)

        # Логируем CDP network запросы сделанные при логине
        _log_cdp_interesting(driver, status, label="login-page")

        # Step 3: Ждём поле для SMS кода
        status("Жду SMS код...")
        try:
            WebDriverWait(driver, 30).until(
                lambda d: _has_code_field(d) or _is_logged_in(d)
            )
        except Exception:
            body_text = driver.find_element(By.TAG_NAME, "body").text[:300]
            status(f"Текущий URL: {driver.current_url[:80]}\nТекст страницы: {body_text}")

        if _is_logged_in(driver):
            status("Авторизация прошла без кода — извлекаю токен...")
            try:
                driver.get(f"{WB_PVZ_URL}/payments")
            except Exception:
                pass
            time.sleep(5)
            _log_cdp_interesting(driver, status, label="payments-auto")
            token_data = _extract_token(driver, status)
            if token_data:
                _save_token(driver, token_data)
                return token_data

        # Просим код у пользователя
        asyncio.run_coroutine_threadsafe(get_code(), loop)
        import wildberries.web_login as _self_mod
        _code_fn = _self_mod._get_pending_code_sync_override
        if _code_fn is None:
            from bot.handlers.auth import _get_pending_code_sync as _code_fn
        code = _code_fn(timeout=300)

        if not code or not code.strip():
            raise RuntimeError("SMS код не получен")

        # Step 4: Вводим код
        status("Ввожу SMS код...")
        typed_code = driver.execute_script("""
            function findInput(root) {
                var inputs = root.querySelectorAll('input:not([type="hidden"])');
                for (var inp of inputs) {
                    var rect = inp.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) return inp;
                }
                var all = root.querySelectorAll('*');
                for (var el of all) {
                    if (el.shadowRoot) {
                        var f = findInput(el.shadowRoot);
                        if (f) return f;
                    }
                }
                return null;
            }
            var inp = findInput(document);
            if (!inp) return false;
            inp.focus();
            var nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeSetter.call(inp, arguments[0]);
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        """, code.strip())

        if not typed_code:
            # Пробуем через ActionChains
            try:
                actions = ActionChains(driver)
                for ch in code.strip():
                    actions.send_keys(ch)
                    actions.pause(0.07)
                actions.perform()
                typed_code = True
            except Exception as e:
                raise RuntimeError(f"Поле для SMS кода не найдено: {e}")

        time.sleep(0.5)
        driver.execute_script("""
            var inp = document.querySelector('input:not([type="hidden"])');
            if (inp) {
                inp.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', keyCode:13, bubbles:true}));
                var form = inp.closest('form');
                if (form) form.dispatchEvent(new Event('submit', {bubbles:true}));
            }
            // Кнопка подтверждения
            var btns = Array.from(document.querySelectorAll('button'));
            var kws = ['войти', 'подтвердить', 'confirm', 'далее', 'next'];
            for (var kw of kws) {
                for (var b of btns) {
                    var r = b.getBoundingClientRect();
                    if (r.width > 0 && b.textContent.toLowerCase().includes(kw)) { b.click(); return; }
                }
            }
        """)

        status("Жду авторизацию...")

        # Step 5: Ждём редирект на главную + перехватываем токен
        for _i in range(90):
            time.sleep(1)

            # Проверяем перехваченный токен
            captured = driver.execute_script("return window._wbCapture || null;")
            if captured and captured.get("xToken"):
                x_token = captured["xToken"]
                refresh_token = captured.get("refreshToken", "")
                token_url = captured.get("xTokenUrl", "unknown")
                status(f"x-token перехвачен! URL источника: {token_url[-100:]}")

                # Читаем CDP логи — извлекаем session_token + code, вызываем validate из Python
                _py_validate_done = False
                try:
                    _early_logs = _cdp_get_network_logs(driver)
                    _seen_pairs: set = set()
                    for _r in _early_logs:
                        if "r-point.wb.ru/api/v1/validate" not in _r.get("url", ""):
                            continue
                        _post = _r.get("postData", "")
                        if not _post:
                            continue
                        try:
                            _pd = json.loads(_post)
                        except Exception:
                            continue
                        _sess_tok = _pd.get("token", "")
                        _sms_code = _pd.get("code", "")
                        if not _sess_tok or not _sms_code:
                            continue
                        _pair_key = (_sess_tok[:20], _sms_code)
                        if _pair_key in _seen_pairs:
                            continue
                        _seen_pairs.add(_pair_key)
                        status(f"Вызываю validate из Python: code={_sms_code}, token={_sess_tok[:30]}...")
                        # Синхронный вызов в том же потоке (уже в thread executor)
                        try:
                            import asyncio as _aio
                            _vres = _aio.run_coroutine_threadsafe(
                                _validate_mobile(_sess_tok, _sms_code, status),
                                loop
                            ).result(timeout=30)
                            if _vres and _vres.get("x_token"):
                                _vc = _jwt_decode(_vres["x_token"])
                                status(f"Python validate OK: pid={_vc.get('pid')}, xpid={_vc.get('xpid')}")
                                if _vc.get("xpid") and int(_vc.get("xpid", 0)) != 0:
                                    x_token = _vres["x_token"]
                                    refresh_token = _vres.get("refresh_token", refresh_token)
                                    _py_validate_done = True
                                    break
                        except Exception as _ve:
                            status(f"Python validate error: {_ve}")
                except Exception as _e:
                    status(f"CDP early read error: {_e}")

                # Пробуем прочитать refresh_token из IndexedDB (service worker хранит там)
                if not refresh_token:
                    try:
                        _idb_refresh = driver.execute_script("""
                            return new Promise((resolve) => {
                                var result = null;
                                try {
                                    var req = indexedDB.open('wb-point-sw', undefined);
                                    req.onsuccess = function(e) {
                                        var db = e.target.result;
                                        var names = Array.from(db.objectStoreNames);
                                        if (!names.length) { resolve(null); return; }
                                        var tx = db.transaction(names[0], 'readonly');
                                        var store = tx.objectStore(names[0]);
                                        var all = store.getAll();
                                        all.onsuccess = function(e2) {
                                            var items = e2.target.result || [];
                                            for (var item of items) {
                                                var v = JSON.stringify(item);
                                                var m = v.match(/"(eyJ[A-Za-z0-9_\-]{50,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"/g);
                                                if (m && m.length > 1) { resolve(m[1].replace(/"/g,'')); return; }
                                            }
                                            resolve(null);
                                        };
                                        all.onerror = function() { resolve(null); };
                                    };
                                    req.onerror = function() { resolve(null); };
                                } catch(e) { resolve(null); }
                            });
                        """)
                        if _idb_refresh and len(_idb_refresh) > 50:
                            status(f"IndexedDB refresh_token: {_idb_refresh[:40]}...")
                            refresh_token = _idb_refresh
                    except Exception as _ie:
                        status(f"IndexedDB read error: {_ie}")

                # Если есть общий токен + refresh — обновляем до PVZ-scoped через /api/v1/refresh
                _cur_claims = _jwt_decode(x_token)
                if refresh_token and (not _cur_claims.get("xpid") or int(_cur_claims.get("xpid", 0)) == 0):
                    status(f"Пробую PVZ-upgrade через refresh_token...")
                    try:
                        import asyncio as _aio
                        _pvz_res = _aio.run_coroutine_threadsafe(
                            _select_pvz_mobile(x_token, refresh_token, status),
                            loop
                        ).result(timeout=30)
                        if _pvz_res and _pvz_res.get("x_token"):
                            _pc = _jwt_decode(_pvz_res["x_token"])
                            status(f"PVZ-upgrade OK: pid={_pc.get('pid')}, xpid={_pc.get('xpid')}")
                            x_token = _pvz_res["x_token"]
                            refresh_token = _pvz_res.get("refresh_token", refresh_token)
                    except Exception as _pe:
                        status(f"PVZ-upgrade error: {_pe}")

                # Пробуем несколько страниц подряд — ищем PVZ-scoped токен
                # /payments даёт 403 для некоторых аккаунтов, пробуем другие
                _pvz_pages = ["/", "/supply", "/analytics", "/employees", "/payments"]
                for _page in _pvz_pages:
                    _cur_claims = _jwt_decode(x_token)
                    if _cur_claims.get("xpid") and int(_cur_claims.get("xpid", 0)) != 0:
                        status(f"PVZ-токен уже есть (xpid={_cur_claims['xpid']}), пропускаю навигацию")
                        break
                    try:
                        driver.get(f"{WB_PVZ_URL}{_page}")
                    except Exception:
                        pass
                    time.sleep(8)
                    # Ждём PVZ-scoped токен до 10 секунд
                    for _w in range(10):
                        time.sleep(1)
                        _cap_check = driver.execute_script("return window._wbCapture || null;")
                        if _cap_check and _cap_check.get("xToken"):
                            _claims_check = _jwt_decode(_cap_check["xToken"])
                            if _claims_check.get("xpid") and int(_claims_check.get("xpid", 0)) != 0:
                                x_token = _cap_check["xToken"]
                                refresh_token = _cap_check.get("refreshToken", refresh_token)
                                status(f"PVZ-токен появился на {_page} (xpid={_claims_check['xpid']})")
                                break
                    else:
                        status(f"Страница {_page}: PVZ-токен не появился")

                # Логируем CDP запросы на странице payments (ВСЕ не-статические)
                _log_cdp_interesting(driver, status, label="payments")

                # Пытаемся получить тело ответа от point-balance через CDP
                _try_capture_payments_body(driver, status)

                cap2 = driver.execute_script("return window._wbCapture || null;")
                if cap2:
                    # Показываем тела всех запросов к r-point.wb.ru
                    for vr in cap2.get("validateReqs", []):
                        status(f"r-point [{vr.get('method')}] {vr.get('url','')[-60:]}\n"
                               f"  REQ: {vr.get('reqBody','')[:200]}\n"
                               f"  RESP {vr.get('respStatus')}: {vr.get('respBody','')[:200]}")

                    # Проверяем обновился ли x-token (после выбора PVZ контекста)
                    new_token = cap2.get("xToken")
                    new_url = cap2.get("xTokenUrl", "")
                    if new_token and new_token != x_token:
                        status(f"x-token обновился на /payments! URL: {new_url[-100:]}")
                        x_token = new_token
                        refresh_token = cap2.get("refreshToken", refresh_token)

                    for c in cap2.get("calls", []):
                        url = c.get("url", "")
                        if any(ext in url for ext in [".wasm", ".js", ".css", ".png", ".svg", ".woff"]):
                            continue  # пропускаем статику
                        status(f"JS API: {url[-100:]} → {c.get('status')} → {(c.get('body') or '')[:150]}")

                # Декодируем JWT чтобы проверить pid/xpid
                claims = _jwt_decode(x_token)
                pid_val = claims.get("pid", "?")
                xpid_val = claims.get("xpid", "?")
                status(f"JWT claims: pid={pid_val}, xpid={xpid_val}")

                # Пробуем получить PVZ-scoped токен через browser-context validate
                # (браузер вызывает r-point.wb.ru/api/v1/validate с куками — мы делаем то же самое)
                pvz_token = _try_browser_validate(driver, x_token, status)
                if pvz_token and pvz_token.get("x_token") and pvz_token.get("pickpoint_id"):
                    # Сохраняем PVZ-токен напрямую (token_type=mobile → HEADERS_BASE не используется)
                    _save_token(driver, pvz_token)
                    return pvz_token

                # Fallback: Python-side validate с куками из браузера
                fallback = _try_validate_exchange(x_token, driver, status)
                if fallback and fallback.get("x_token"):
                    x_token = fallback["x_token"]
                    refresh_token = fallback.get("refresh_token", refresh_token)
                    status(f"PVZ-токен (fallback): pid={fallback.get('pid')}, xpid={fallback.get('xpid')}")

                token_data = _build_token_data(x_token, refresh_token)
                _save_token(driver, token_data)
                return token_data

            cur = driver.current_url
            # Залогинились — переходим на /payments чтобы спровоцировать API-запрос
            if _is_logged_in(driver) and "/payments" not in cur:
                status("Авторизован — перехожу на /payments...")
                try:
                    driver.get(f"{WB_PVZ_URL}/payments")
                except Exception:
                    pass
                time.sleep(3)
                continue

            if _i % 15 == 14:
                status(f"Ожидание ({_i+1}с), URL: {cur[:80]}")
                # Дампим что перехвачено
                captured = driver.execute_script("return window._wbCapture || null;")
                if captured:
                    calls = captured.get("calls", [])
                    recent = [(c.get("url", "")[-70:], c.get("status"), (c.get("body") or "")[:80])
                              for c in calls[-5:]]
                    status(f"JS calls: {recent}")

                # Логируем CDP
                _log_cdp_interesting(driver, status, label=f"wait-{_i+1}s")

                # Пробуем вытащить из localStorage
                ls_token = _extract_from_storage(driver)
                if ls_token:
                    status(f"Токен найден в localStorage!")
                    token_data = _build_token_data(ls_token, "")
                    _save_token(driver, token_data)
                    return token_data

        # Финальная попытка
        token_data = _extract_token(driver, status)
        if token_data:
            _save_token(driver, token_data)
            return token_data

        raise RuntimeError(
            f"x-token не удалось получить.\n"
            f"URL: {driver.current_url[:100]}"
        )

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def _log_cdp_interesting(driver, status_fn, label: str = ""):
    """Логирует ВСЕ CDP network запросы (не статику) + тела ответов r-point."""
    try:
        reqs = _cdp_get_network_logs(driver)

        static_ext = (".wasm", ".js", ".css", ".png", ".svg", ".woff", ".ico", ".jpg", ".gif", ".map")
        static_domains = ("cdn.", "static.", "fonts.", "mc.yandex", "google", "ya.ru")

        rpoint_req_ids = []
        all_api = []
        for r in reqs:
            if r.get("_type"):
                continue
            url = r.get("url", "")
            if any(url.endswith(e) for e in static_ext):
                continue
            if any(d in url for d in static_domains):
                continue
            if "/assets/" in url:
                continue
            headers = r.get("headers", {})
            h_lower = {k.lower(): v for k, v in headers.items()}
            has_token = "x-token" in h_lower
            tok = h_lower.get("x-token", "")[:20] if has_token else ""
            all_api.append(f"  [{r.get('method','GET')}] {url[-100:]} tok={tok}")
            if "r-point.wb.ru" in url and r.get("method") in ("POST", "GET") and r.get("requestId"):
                rpoint_req_ids.append((r["requestId"], r.get("method", ""), url, r.get("postData", "")))

        if all_api:
            status_fn(f"CDP [{label}] {len(all_api)} запросов:\n" + "\n".join(all_api[:20]))
        else:
            status_fn(f"CDP [{label}] нет запросов (кроме статики)")

        # Читаем тела запросов и ответов r-point.wb.ru
        for req_id, method, url, post_data in rpoint_req_ids[:4]:
            if post_data:
                status_fn(f"r-point [{method}] req body: {post_data[:300]}")
            body = _cdp_get_response_body(driver, req_id)
            if body:
                status_fn(f"r-point [{method}] resp: {body[:500]}")
    except Exception as e:
        status_fn(f"CDP лог ошибка: {e}")


def _try_browser_validate(driver, current_token: str, status_fn) -> Optional[dict]:
    """
    Вызывает r-point.wb.ru/api/v1/validate из браузерного JS-контекста (fire-and-forget).
    Браузер автоматически добавляет сессионные куки.
    Также читает validateReqs из XHR-перехватчика — там есть РЕАЛЬНЫЙ ответ браузера.
    """
    status_fn("browser-validate: читаю validateReqs из XHR-перехватчика...")
    try:
        # Сначала читаем что браузер УЖЕ получил от r-point.wb.ru/validate (из XHR-перехватчика)
        captured = driver.execute_script("return window._wbCapture || null;")
        if captured:
            for vr in captured.get("validateReqs", []):
                url = vr.get("url", "")
                resp_body = vr.get("respBody", "") or ""
                resp_status = vr.get("respStatus")
                req_body = vr.get("reqBody", "") or ""
                status_fn(f"XHR r-point [{vr.get('method')}] {url[-60:]}\n"
                          f"  REQ: {req_body[:200]}\n"
                          f"  RESP {resp_status}: {resp_body[:400]}")
                if resp_status == 200 and resp_body:
                    try:
                        d = json.loads(resp_body)
                        at = (d.get("access") or {}).get("token") or d.get("token") or d.get("accessToken")
                        rt = (d.get("refresh") or {}).get("token") or d.get("refreshToken")
                        if at and at.startswith("eyJ"):
                            claims = _jwt_decode(at)
                            xpid = claims.get("xpid") or claims.get("pid")
                            status_fn(f"XHR validate → xpid={xpid}")
                            if xpid:
                                return {
                                    "x_token": at,
                                    "exp": _jwt_exp(at) or int(time.time()) + 86400,
                                    "pickpoint_id": xpid,
                                    "token_type": "mobile",
                                    "refresh_token": rt or "",
                                    "refresh_exp": (_jwt_exp(rt) if rt else 0) or int(time.time()) + 90 * 86400,
                                }
                    except Exception:
                        pass

        # Собственный fetch из браузерного контекста (fire-and-forget)
        status_fn("browser-validate: запускаю собственный fetch к validate...")
        driver.execute_script("""
            window._wbOwnVal = null;
            fetch('https://r-point.wb.ru/api/v1/validate', {
                method: 'POST',
                credentials: 'include',
                headers: {'Content-Type': 'application/json'},
                body: '{}'
            }).then(function(r) {
                return r.text().then(function(t) {
                    window._wbOwnVal = {status: r.status, body: t.slice(0, 1000)};
                });
            }).catch(function(e) {
                window._wbOwnVal = {error: String(e)};
            });
        """)
        time.sleep(4)
        own_result = driver.execute_script("return window._wbOwnVal;")
        if own_result:
            status_fn(f"own fetch validate: status={own_result.get('status')}, resp={own_result.get('body', own_result.get('error', ''))[:300]}")
            if own_result.get("status") == 200:
                try:
                    d = json.loads(own_result["body"])
                    at = (d.get("access") or {}).get("token") or d.get("token") or d.get("accessToken")
                    rt = (d.get("refresh") or {}).get("token") or d.get("refreshToken")
                    if at and at.startswith("eyJ"):
                        claims = _jwt_decode(at)
                        xpid = claims.get("xpid") or claims.get("pid")
                        if xpid:
                            return {
                                "x_token": at,
                                "exp": _jwt_exp(at) or int(time.time()) + 86400,
                                "pickpoint_id": xpid,
                                "token_type": "mobile",
                                "refresh_token": rt or "",
                                "refresh_exp": (_jwt_exp(rt) if rt else 0) or int(time.time()) + 90 * 86400,
                            }
                except Exception:
                    pass
        else:
            status_fn("own fetch validate: нет ответа (null)")
    except Exception as e:
        status_fn(f"browser-validate error: {e}")
    return None


def _try_validate_exchange(x_token: str, driver, status_fn) -> Optional[dict]:
    """
    Пробует обменять general web token на PVZ-scoped через r-point.wb.ru/api/v1/validate.
    pvz-lk.wb.ru вызывает этот endpoint БЕЗ x-token — только с браузерными куками.
    Возвращает dict с новым токеном если удалось, иначе None.
    """
    import urllib.request
    import urllib.error
    import http.cookiejar

    pickpoint_id = None
    try:
        with open(TOKEN_FILE) as f:
            saved = json.load(f)
        pickpoint_id = saved.get("pickpoint_id")
    except Exception:
        pass

    status_fn("validate: начинаю обмен токена...")

    # Собираем куки из браузера (pvz-lk.wb.ru использует куки, не x-token для validate)
    cookie_header = ""
    try:
        browser_cookies = driver.get_cookies()
        cookie_pairs = [f"{c['name']}={c['value']}" for c in browser_cookies
                        if "wb.ru" in c.get("domain", "")]
        cookie_header = "; ".join(cookie_pairs)
        status_fn(f"validate: собрано {len(cookie_pairs)} wb.ru куки")
    except Exception as e:
        status_fn(f"validate: не удалось собрать куки: {e}")

    # Пробуем разные варианты вызова validate
    variants = [
        # 1. POST с куками (как делает pvz-lk.wb.ru)
        {
            "method": "POST",
            "url": "https://r-point.wb.ru/api/v1/validate",
            "body": b"{}",
            "use_cookies": True,
            "use_token": False,
        },
        # 2. GET с куками
        {
            "method": "GET",
            "url": "https://r-point.wb.ru/api/v1/validate",
            "body": None,
            "use_cookies": True,
            "use_token": False,
        },
        # 3. POST с x-token (мобильный стиль)
        {
            "method": "POST",
            "url": "https://r-point.wb.ru/api/v1/validate",
            "body": json.dumps({"backoffice": False}).encode(),
            "use_cookies": False,
            "use_token": True,
        },
    ]

    for v in variants:
        try:
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://pvz-lk.wb.ru",
                "Referer": "https://pvz-lk.wb.ru/payments",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
            }
            if v.get("body"):
                headers["Content-Type"] = "application/json"
            if v["use_cookies"] and cookie_header:
                headers["Cookie"] = cookie_header
            if v["use_token"]:
                headers["x-token"] = x_token
                headers["x-app-type"] = "mobile"
                headers["x-app-version"] = "v3.61.0"
                headers["x-device-type"] = "ios"
                headers["User-Agent"] = "WBPoint/14287039 CFNetwork/3826.500.131 Darwin/24.5.0"
            if pickpoint_id:
                headers["x-pickpoint-external-id"] = str(pickpoint_id)

            req = urllib.request.Request(
                v["url"], data=v["body"], headers=headers, method=v["method"]
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read().decode()
                status_fn(f"validate [{v['method']},{'cookie' if v['use_cookies'] else 'token'}] {resp.status}: {raw[:300]}")
                try:
                    d = json.loads(raw)
                    at = (d.get("access") or {}).get("token") or d.get("token") or d.get("accessToken")
                    rt = (d.get("refresh") or {}).get("token") or d.get("refreshToken")
                    if at and at.startswith("eyJ"):
                        claims = _jwt_decode(at)
                        return {
                            "x_token": at,
                            "refresh_token": rt or "",
                            "pid": claims.get("pid"),
                            "xpid": claims.get("xpid"),
                        }
                except Exception:
                    pass
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:300]
            except Exception:
                pass
            status_fn(f"validate [{v['method']},{'cookie' if v['use_cookies'] else 'token'}] HTTP {e.code}: {body}")
        except Exception as e:
            status_fn(f"validate [{v['method']}] ошибка: {type(e).__name__}: {e}")

    return None


def _try_capture_payments_body(driver, status_fn):
    """
    Пытается получить через CDP тело ответа от point-balance.wb.ru.
    Если успешно — логирует URL, заголовки запроса и сохраняет тело.
    """
    try:
        reqs = _cdp_get_network_logs(driver)
        for r in reqs:
            if r.get("_type"):
                continue
            url = r.get("url", "")
            req_id = r.get("requestId")
            if not req_id:
                continue
            # Ищем запросы к point-balance или pvz-lk.wb.ru/api
            if "point-balance.wb.ru" in url or ("pvz-lk.wb.ru" in url and "/api" in url):
                headers = r.get("headers", {})
                h_lower = {k.lower(): v for k, v in headers.items()}
                status_fn(
                    f"📦 payments API: {url[-100:]}\n"
                    f"  x-token: {'✓' if 'x-token' in h_lower else '✗'}\n"
                    f"  x-pickpoint: {h_lower.get('x-pickpoint-external-id', 'нет')}"
                )
                body = _cdp_get_response_body(driver, req_id)
                if body:
                    status_fn(f"  resp: {body[:400]}")
    except Exception as e:
        status_fn(f"capture payments error: {e}")


def _has_code_field(driver) -> bool:
    """Проверяет, появилось ли поле для ввода кода."""
    try:
        body = driver.find_element("tag name", "body").text.lower()
        return any(kw in body for kw in ["код", "code", "sms", "смс", "подтверждение"])
    except Exception:
        return False


def _is_logged_in(driver) -> bool:
    """Проверяет, прошла ли авторизация (нет /login в URL)."""
    cur = driver.current_url
    return (
        "pvz-lk.wb.ru" in cur
        and "/login" not in cur
        and "auth" not in cur
        and "signin" not in cur
    )


def _extract_from_storage(driver) -> str:
    """Ищет JWT-токен в localStorage/sessionStorage."""
    return driver.execute_script("""
        var result = null;
        // localStorage
        for (var i = 0; i < localStorage.length; i++) {
            var k = localStorage.key(i);
            var v = localStorage.getItem(k);
            if (v && v.startsWith('eyJ') && v.length > 100) {
                result = v;
                break;
            }
            // JSON обёртка
            try {
                var d = JSON.parse(v);
                var candidates = [d.token, d.access_token, d.accessToken, d.xToken, d.x_token];
                for (var c of candidates) {
                    if (c && String(c).startsWith('eyJ')) { result = String(c); break; }
                }
                if (result) break;
            } catch(e) {}
        }
        if (!result) {
            // sessionStorage
            for (var i = 0; i < sessionStorage.length; i++) {
                var k = sessionStorage.key(i);
                var v = sessionStorage.getItem(k);
                if (v && v.startsWith('eyJ') && v.length > 100) { result = v; break; }
            }
        }
        return result;
    """)


def _extract_token(driver, status_fn) -> dict:
    """Пытается извлечь токен из всех доступных источников."""
    # 1. JS перехватчик
    captured = driver.execute_script("return window._wbCapture || null;")
    if captured and captured.get("xToken"):
        x_token = captured["xToken"]
        refresh_token = captured.get("refreshToken", "")
        return _build_token_data(x_token, refresh_token)

    # 2. localStorage/sessionStorage
    ls_token = _extract_from_storage(driver)
    if ls_token:
        return _build_token_data(ls_token, "")

    # 3. Cookies (ищем JWT-подобные)
    try:
        for c in driver.get_cookies():
            val = c.get("value", "")
            if val.startswith("eyJ") and len(val) > 100:
                status_fn(f"Токен из cookie '{c['name']}'")
                return _build_token_data(val, "")
    except Exception:
        pass

    return {}


def _build_token_data(x_token: str, refresh_token: str) -> dict:
    exp = _jwt_exp(x_token)
    if not exp:
        exp = int(time.time()) + 24 * 3600

    claims = _jwt_decode(x_token)
    # xpid = external pickpoint id (например 50016046)
    pickpoint_id = claims.get("xpid") or claims.get("pid") or None

    # PVZ-scoped токен (xpid != 0) требует mobile-заголовки для point-balance.wb.ru
    # Общий web-токен (xpid=0) использует web-заголовки
    token_type = "mobile" if pickpoint_id else "web"

    data = {
        "x_token": x_token,
        "exp": exp,
        "pickpoint_id": pickpoint_id,
        "token_type": token_type,
    }
    if refresh_token:
        data["refresh_token"] = refresh_token
        data["refresh_exp"] = _jwt_exp(refresh_token) or int(time.time()) + 90 * 86400

    return data


def _save_token(driver, token_data: dict):
    """Сохраняет токен и wb.ru-куки в data/wb_token.json."""
    # Если pickpoint_id не в JWT — берём из текущего файла (уже настроен)
    if not token_data.get("pickpoint_id"):
        try:
            with open(TOKEN_FILE) as f:
                existing = json.load(f)
            pid = existing.get("pickpoint_id")
            if pid:
                token_data["pickpoint_id"] = pid
                # point-balance.wb.ru требует mobile-заголовки даже с web-токеном
                token_data["token_type"] = "mobile"
        except Exception:
            pass

    # Сохраняем все .wb.ru куки — они общие для всех поддоменов wb.ru,
    # включая point-balance.wb.ru, и нужны для авторизации API-запросов
    try:
        wb_cookies = {
            c["name"]: c["value"]
            for c in driver.get_cookies()
            if ".wb.ru" in c.get("domain", "") or "wb.ru" in c.get("domain", "")
        }
        if wb_cookies:
            token_data["wb_cookies"] = wb_cookies
    except Exception:
        pass

    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)
