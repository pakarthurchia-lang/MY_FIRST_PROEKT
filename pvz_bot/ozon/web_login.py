"""
Автоматический логин в Ozon PVZ через undetected Chrome.

Проходит WAF id.ozon.ru (который блокирует curl/aiohttp/playwright),
получает Web PVZ токен (ClientType=Web) — нужен для reports/fines.

Флоу:
  1. Открывает Chrome → id.ozon.ru с redirect token
  2. Вводит телефон/email → Ozon отправляет код
  3. Ждёт код от пользователя (через callback)
  4. Вводит код → получает Web PVZ токен из localStorage
  5. Сохраняет токен + SSO куки

Используется из bot/handlers/auth.py при команде /login.
"""
import asyncio
import json
import os
import time
import base64
from typing import Callable, Awaitable, Optional

TOKEN_FILE = "data/ozon_token.json"


def _jwt_decode(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.b64decode(payload))
    except Exception:
        return {}


async def login_ozon_web(
    phone: str,
    get_code: Callable[[], Awaitable[str]],
    on_status: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    """
    Выполняет логин в Ozon PVZ через Chrome и возвращает Web PVZ токен.

    Args:
        phone: номер телефона (+7...)
        get_code: async callback, вызывается когда нужен код — должен вернуть строку с кодом
        on_status: async callback для статусных сообщений (опционально)

    Returns:
        dict с access_token, refresh_token, expire_time, refresh_expire_time

    Raises:
        RuntimeError если логин не удался
    """
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    async def status(msg: str):
        if on_status:
            await on_status(msg)

    # Запускаем Chrome в отдельном потоке (selenium — синхронный)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _chrome_login_sync(
            phone, loop, get_code, on_status
        ),
    )
    return result


async def _get_redirect_token() -> str:
    """Получает redirect token через curl_cffi. Возвращает '' если не удалось."""
    try:
        from curl_cffi.requests import AsyncSession
        from ozon.http_client import (
            _get_cookies, HEADERS_BASE, REQUEST_TOKEN_URL, BASE_URL, IMPERSONATE
        )

        cookies = _get_cookies()
        if not cookies:
            return ""
        headers = {k: v for k, v in HEADERS_BASE.items() if k != "Content-Type"}
        async with AsyncSession(impersonate=IMPERSONATE, timeout=15) as s:
            r = await s.get(
                REQUEST_TOKEN_URL,
                params={"returnUrl": BASE_URL},
                headers=headers,
                cookies=cookies,
            )
            if r.status_code == 200:
                return r.json().get("token", "")
        return ""
    except Exception:
        return ""


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
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    def status(msg):
        if on_status:
            asyncio.run_coroutine_threadsafe(on_status(msg), loop)

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1280,720")

    driver = None
    try:
        driver = uc.Chrome(options=options, version_main=146)

        # Внедряем перехватчик fetch+XHR ДО любой навигации — будет работать на всех страницах
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': """
            window._pvzCapture = {calls: [], storage: {}};
            // Перехватываем fetch
            var origFetch = window.fetch;
            window.fetch = function(input, init) {
                var url = typeof input === 'string' ? input : (input && input.url ? input.url : String(input));
                var body = init && init.body ? String(init.body).slice(0, 200) : null;
                var entry = {url: url, method: (init && init.method) || 'GET', body_sent: body, time: Date.now(), status: null, body: null};
                window._pvzCapture.calls.push(entry);
                return origFetch.call(window, input, init).then(function(resp) {
                    entry.status = resp.status;
                    resp.clone().text().then(function(t){ entry.body = t.slice(0, 300); }).catch(function(){});
                    return resp;
                }, function(err) { entry.body = String(err); throw err; });
            };
            // Перехватываем XHR (React SPA может использовать XHR)
            var origOpen = XMLHttpRequest.prototype.open;
            var origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this._pvzUrl = url; this._pvzMethod = method;
                return origOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(body) {
                var entry = {url: this._pvzUrl || '', method: this._pvzMethod || 'XHR', body_sent: body ? String(body).slice(0, 200) : null, time: Date.now(), status: null, body: null};
                window._pvzCapture.calls.push(entry);
                this.addEventListener('load', function() {
                    entry.status = this.status;
                    entry.body = this.responseText ? this.responseText.slice(0, 300) : null;
                }.bind(this));
                return origSend.apply(this, arguments);
            };
            // Перехватываем localStorage.setItem
            var origSet = Storage.prototype.setItem;
            Storage.prototype.setItem = function(k, v) {
                window._pvzCapture.storage[k] = v.slice ? v.slice(0, 80) : v;
                return origSet.call(this, k, v);
            };
            """
        })

        # Step 0: Открываем turbo-pvz.ozon.ru/login и нажимаем "Войти через Ozon ID"
        # ВАЖНО: приложение само генерирует state/nonce и сохраняет в sessionStorage.
        # Если идти напрямую на id.ozon.ru — state не совпадёт при callback → обмен токена не работает.
        status("Открываю turbo-pvz.ozon.ru/login...")
        driver.get("https://turbo-pvz.ozon.ru/login")
        time.sleep(4)

        # Нажимаем кнопку "Войти через Ozon ID" — приложение само делает redirect на id.ozon.ru
        status("Нажимаю 'Войти через Ozon ID'...")
        clicked = driver.execute_script("""
            var texts = ['ozon id', 'ozonid', 'войти через'];
            var els = Array.from(document.querySelectorAll('button, a'));
            for (var t of texts) {
                for (var el of els) {
                    if (el.textContent.toLowerCase().includes(t)) {
                        el.click();
                        return el.textContent.trim().slice(0, 50);
                    }
                }
            }
            return null;
        """)
        if not clicked:
            # Fallback: первая видимая кнопка
            clicked = driver.execute_script("""
                var btns = Array.from(document.querySelectorAll('button'));
                var vis = btns.filter(function(b) {
                    var r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0;
                });
                if (vis.length > 0) { vis[0].click(); return vis[0].textContent.trim().slice(0, 50); }
                return null;
            """)
        status(f"Нажата кнопка: '{clicked}' — жду перехода на id.ozon.ru...")

        # Ждём пока Chrome окажется на id.ozon.ru ИЛИ sso.ozon.ru
        # Кнопка может редиректить сначала на sso.ozon.ru/auth/ozonid, потом на id.ozon.ru
        _on_login_page = False
        for _ in range(30):
            time.sleep(1)
            cur = driver.current_url
            if "id.ozon.ru" in cur or ("sso.ozon.ru" in cur and "auth" in cur):
                _on_login_page = True
                break
        if not _on_login_page:
            status(f"Не перешёл на id.ozon.ru/sso.ozon.ru — текущий URL: {driver.current_url[:80]}")
            raise RuntimeError(
                f"Chrome не перешёл на страницу Ozon ID после нажатия кнопки.\n"
                f"URL: {driver.current_url[:100]}"
            )

        # Если на sso.ozon.ru — ждём редиректа на id.ozon.ru (sso может само редиректить)
        if "sso.ozon.ru" in driver.current_url:
            status(f"На sso.ozon.ru: {driver.current_url[:80]} — жду перехода на id.ozon.ru...")
            for _ in range(10):
                time.sleep(1)
                if "id.ozon.ru" in driver.current_url:
                    break
            status(f"URL после ожидания: {driver.current_url[:80]}")

        time.sleep(2)
        status(f"На странице входа: {driver.current_url[:80]}")

        # Step 1: Получаем email
        email = phone if "@" in phone else ""
        if not email:
            try:
                from config import OZON_EMAIL
                email = OZON_EMAIL
            except (ImportError, AttributeError):
                pass
        if not email:
            raise RuntimeError(
                "OZON_EMAIL не задан в .env.\n"
                "Добавь OZON_EMAIL=твой@email.com в .env"
            )

        # Step 2: Переключаемся на email-форму
        from selenium.webdriver.common.action_chains import ActionChains
        status("Переключаюсь на вход по почте...")
        try:
            email_btn = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//button[contains(., 'почте') or contains(., 'Почте')]")
                )
            )
            ActionChains(driver).move_to_element(email_btn).click().perform()
            time.sleep(2)
            status("Форма email открыта")
        except Exception:
            status("Кнопка 'по почте' не найдена — уже на email форме")

        # Step 4: Вводим email
        status("Ввожу email...")
        # Ждём пока появится email-инпут (React делает transition после клика)
        time.sleep(2)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "input:not([type='hidden']):not([type='tel'])"))
            )
        except Exception:
            pass
        time.sleep(1)

        # Проверяем что инпут есть (диагностика)
        all_inp = driver.find_elements(By.TAG_NAME, "input")
        inp_dump = [{
            "type": i.get_attribute("type"),
            "name": i.get_attribute("name"),
            "inputmode": i.get_attribute("inputmode"),
            "displayed": i.is_displayed(),
        } for i in all_inp]
        status(f"Inputs after email form: {inp_dump}")

        # Вводим email ПОЛНОСТЬЮ через JS — без Selenium WebElement reference
        # (избегаем StaleElementReferenceException при React re-render)
        typed = driver.execute_script("""
            function findEmailInput(root) {
                var selectors = ['input[type="email"]', 'input[type="text"]',
                    'input:not([type="hidden"]):not([type="tel"]):not([type="number"])'];
                for (var sel of selectors) {
                    var inputs = root.querySelectorAll(sel);
                    for (var inp of inputs) {
                        var rect = inp.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) return inp;
                    }
                }
                var all = root.querySelectorAll('*');
                for (var el of all) {
                    if (el.shadowRoot) {
                        var found = findEmailInput(el.shadowRoot);
                        if (found) return found;
                    }
                }
                return null;
            }
            var inp = findEmailInput(document);
            if (!inp) return false;
            inp.focus();
            inp.value = '';
            // Симулируем нативный ввод для React
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inp, arguments[0]);
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        """, email)

        if not typed:
            raise RuntimeError(f"Email input не найден\nURL: {driver.current_url[:80]}\nAll inputs: {inp_dump}")

        time.sleep(0.5)

        # Step 5: Сабмитим форму через Enter (JS, без stale ref)
        driver.execute_script("""
            function findEmailInput(root) {
                var selectors = ['input[type="email"]', 'input[type="text"]',
                    'input:not([type="hidden"]):not([type="tel"]):not([type="number"])'];
                for (var sel of selectors) {
                    var inputs = root.querySelectorAll(sel);
                    for (var inp of inputs) {
                        var rect = inp.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) return inp;
                    }
                }
                return null;
            }
            var inp = findEmailInput(document);
            if (inp) {
                inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
                inp.dispatchEvent(new KeyboardEvent('keyup',  {key: 'Enter', keyCode: 13, bubbles: true}));
                var form = inp.closest('form');
                if (form) form.submit();
            }
        """)
        time.sleep(5)

        # Step 4: Ждём поле для кода
        status("Ожидаю код подтверждения...")
        try:
            WebDriverWait(driver, 25).until(
                lambda d: "код" in d.find_element(By.TAG_NAME, "body").text.lower()
            )
        except Exception:
            body_text = driver.find_element(By.TAG_NAME, "body").text[:300]
            raise RuntimeError(f"Страница с кодом не появилась.\nТекст: {body_text}")
        time.sleep(1)

        # Step 5: Получаем код от пользователя
        # Отправляем сообщение в бот через async callback
        asyncio.run_coroutine_threadsafe(get_code(), loop)
        # Ждём код через threading Event (из auth.py handler)
        from bot.handlers.auth import _get_pending_code_sync
        code = _get_pending_code_sync(timeout=300)

        if not code or not code.strip():
            raise RuntimeError("Код не получен")

        status("Ввожу код...")

        # Возвращаемся в main frame (могли быть в iframe)
        try:
            driver.switch_to.default_content()
        except Exception:
            pass

        # Ждём появления поля для кода (OTP страница)
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "input"))
            )
        except Exception:
            pass

        # Вводим код через JS — без stale WebElement ref
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
                        var found = findInput(el.shadowRoot);
                        if (found) return found;
                    }
                }
                return null;
            }
            var inp = findInput(document);
            if (!inp) return false;
            inp.focus();
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inp, arguments[0]);
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
            return true;
        """, code.strip())
        if not typed_code:
            all_inp = driver.find_elements(By.TAG_NAME, "input")
            raise RuntimeError(f"Поле для кода не найдено\nURL:{driver.current_url[:80]}\nInputs:{[(i.get_attribute('type'), i.is_displayed()) for i in all_inp]}")

        # Step 6: Ждём редирект на turbo-pvz.ozon.ru + параллельно проверяем localStorage
        # Обрабатываем промежуточную страницу "новое устройство" (otpResponseToken)
        status("Ожидаю авторизацию...")
        _sms_handled = False
        _early_token = None
        _sso_seconds = 0
        _login_page_dump_done = False
        for _ in range(120):
            time.sleep(1)
            current_url = driver.current_url

            # Проверяем токен в localStorage на ЛЮБОЙ странице turbo-pvz.ozon.ru
            if "turbo-pvz.ozon.ru" in current_url:
                _sso_seconds = 0
                try:
                    # Проверяем localStorage
                    t = driver.execute_script("return localStorage.getItem('pvz-access-token');")
                    if t:
                        _early_token = t
                        break
                    # Проверяем sessionStorage тоже
                    t2 = driver.execute_script("return sessionStorage.getItem('pvz-access-token');")
                    if t2:
                        _early_token = t2
                        break
                except Exception:
                    pass

            # Ждём главную страницу PVZ (без /login и без ?token=)
            if "turbo-pvz.ozon.ru" in current_url and "/login" not in current_url and "?token=" not in current_url:
                break

            # На /login?token= — через 5с дампим состояние страницы (SSR Nuxt)
            if "turbo-pvz.ozon.ru/login" in current_url and "token=" in current_url and not _login_page_dump_done and _ >= 5:
                _login_page_dump_done = True
                try:
                    page_info = driver.execute_script("""
                        var info = {};
                        // Текст страницы (что видит пользователь)
                        info.body_text = document.body ? document.body.innerText.slice(0, 400) : 'no body';
                        // Nuxt SSR состояние (содержит auth data если SSR обработал токен)
                        try {
                            var nuxt = window.__NUXT__ || window.__nuxt__;
                            info.nuxt_state = nuxt ? JSON.stringify(nuxt).slice(0, 500) : 'no __NUXT__';
                        } catch(e) { info.nuxt_state = 'err: ' + e; }
                        // Все куки (может быть auth cookie)
                        info.cookies = document.cookie.slice(0, 300);
                        // sessionStorage
                        var ss = {};
                        for (var i = 0; i < sessionStorage.length; i++) {
                            var k = sessionStorage.key(i);
                            ss[k] = sessionStorage.getItem(k).slice(0, 80);
                        }
                        info.session_storage = ss;
                        // Все localStorage ключи
                        info.ls_keys = Object.keys(localStorage);
                        // Все fetch/XHR вызовы
                        var cap = window._pvzCapture;
                        info.api_calls = cap ? cap.calls.map(function(c) {
                            return {u: c.url.slice(-80), m: c.method, s: c.status, b: (c.body||'').slice(0,100)};
                        }) : 'no capture';
                        return info;
                    """)
                    status(
                        f"=== ДИАГНОСТИКА /login?token=... ===\n"
                        f"Текст: {page_info.get('body_text','?')[:200]}\n"
                        f"__NUXT__: {page_info.get('nuxt_state','?')[:200]}\n"
                        f"Cookies: {page_info.get('cookies','?')[:150]}\n"
                        f"sessionStorage: {page_info.get('session_storage','?')}\n"
                        f"LS keys: {page_info.get('ls_keys','?')}\n"
                        f"API calls: {page_info.get('api_calls','?')}"
                    )
                except Exception as _de:
                    status(f"Диагностика /login не удалась: {_de}")

            # SSO редирект — просто ждём (не форсируем навигацию — это ломало цикл)
            if "sso.ozon.ru" in current_url:
                _sso_seconds += 1
                if _sso_seconds % 10 == 0:
                    status(f"На sso.ozon.ru уже {_sso_seconds}с — ожидаю редиректа...")
            else:
                _sso_seconds = 0

            # Страница "Вы заходите с нового устройства"
            if "otpResponseToken" in current_url and not _sms_handled:
                _sms_handled = True
                status("Новое устройство — нужен SMS код...")

                # Ждём полной загрузки страницы
                time.sleep(3)

                # Кликаем кнопку через ActionChains + JS поиск (Shadow DOM)
                clicked = driver.execute_script("""
                    function findBtn(root) {
                        var btns = root.querySelectorAll('button');
                        // Ищем кнопку с нужным текстом
                        var keywords = ['войти', 'подтвердить', 'получить', 'отправить', 'confirm'];
                        for (var kw of keywords) {
                            for (var b of btns) {
                                if (b.textContent.toLowerCase().includes(kw)) {
                                    var rect = b.getBoundingClientRect();
                                    if (rect.width > 0) return b;
                                }
                            }
                        }
                        // Fallback: последняя видимая кнопка (обычно это CTA)
                        var visible = Array.from(btns).filter(function(b) {
                            var r = b.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        });
                        return visible.length > 0 ? visible[visible.length - 1] : null;
                    }
                    var btn = findBtn(document);
                    if (!btn) {
                        // Поиск в Shadow DOM
                        var all = document.querySelectorAll('*');
                        for (var el of all) {
                            if (el.shadowRoot) {
                                btn = findBtn(el.shadowRoot);
                                if (btn) break;
                            }
                        }
                    }
                    if (btn) { btn.click(); return btn.textContent.trim().slice(0,30); }
                    return null;
                """)
                status(f"Кнопка нажата: '{clicked}' — ждём SMS...")
                time.sleep(4)

                # Просим SMS код у пользователя
                if on_status:
                    asyncio.run_coroutine_threadsafe(
                        on_status("📱 Ozon запросил SMS-код для нового устройства.\nВведи код из SMS:"),
                        loop,
                    )
                from bot.handlers.auth import _get_pending_code_sync
                sms_code = _get_pending_code_sync(timeout=300)
                if not sms_code or not sms_code.strip():
                    raise RuntimeError("SMS код не получен")

                # Вводим SMS код — повторяем при необходимости
                for _sms_attempt in range(3):
                    status(f"Ввожу SMS код (попытка {_sms_attempt+1})...")
                    # JS находит и фокусирует поле (Shadow DOM), ActionChains печатает
                    focused = driver.execute_script("""
                        function findInput(root) {
                            var inputs = root.querySelectorAll('input:not([type="hidden"])');
                            for (var inp of inputs) {
                                var rect = inp.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    inp.click(); inp.focus(); return true;
                                }
                            }
                            var all = root.querySelectorAll('*');
                            for (var el of all) {
                                if (el.shadowRoot) {
                                    var f = findInput(el.shadowRoot);
                                    if (f) return true;
                                }
                            }
                            return false;
                        }
                        return findInput(document);
                    """)
                    if focused:
                        time.sleep(0.3)
                        from selenium.webdriver.common.action_chains import ActionChains
                        actions = ActionChains(driver)
                        # Очищаем поле
                        actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
                        actions.send_keys(Keys.DELETE)
                        # Вводим посимвольно
                        for ch in sms_code.strip():
                            actions.send_keys(ch)
                            actions.pause(0.08)
                        actions.perform()
                        time.sleep(0.5)
                        # Кликаем кнопку подтверждения
                        driver.execute_script("""
                            var btns = document.querySelectorAll('button');
                            var keywords = ['войти', 'подтвердить', 'confirm', 'отправить'];
                            for (var kw of keywords) {
                                for (var b of btns) {
                                    if (b.textContent.toLowerCase().includes(kw)) {
                                        var r = b.getBoundingClientRect();
                                        if (r.width > 0) { b.click(); return; }
                                    }
                                }
                            }
                            // Fallback: Enter
                            document.activeElement && document.activeElement.dispatchEvent(
                                new KeyboardEvent('keydown', {key:'Enter', keyCode:13, bubbles:true}));
                        """)
                        time.sleep(5)
                        # Проверяем переход
                        if "otpResponseToken" not in driver.current_url:
                            break  # Код принят, выходим из retry loop
                        # Ещё на той же странице — просим новый код
                        if _sms_attempt < 2:
                            if on_status:
                                asyncio.run_coroutine_threadsafe(
                                    on_status("❌ Код не принят. Введи новый SMS код:"),
                                    loop,
                                )
                            from bot.handlers.auth import _get_pending_code_sync
                            new_sms = _get_pending_code_sync(timeout=120)
                            if new_sms and new_sms.strip():
                                sms_code = new_sms
                    else:
                        status("Поле SMS не найдено")
                        break
        else:
            page_source = driver.page_source
            if "Неверный код" in page_source or "неправильный" in page_source.lower():
                raise RuntimeError("Неверный код подтверждения")
            raise RuntimeError(
                f"Редирект на turbo-pvz.ozon.ru не произошёл. URL: {driver.current_url[:100]}"
            )

        # Step 7: Берём токен (уже найден в шаге 6 или ждём ещё)
        status("Получаю токен...")
        # Дамп всего localStorage — возможно токен под другим ключом
        all_ls = driver.execute_script("""
            var res = {};
            for (var i = 0; i < localStorage.length; i++) {
                var k = localStorage.key(i);
                var v = localStorage.getItem(k);
                if (v && (v.startsWith('eyJ') || v.includes('access_token'))) res[k] = v.slice(0,80);
            }
            return res;
        """)
        if all_ls:
            status(f"JWT-like LS entries: {all_ls}")
            # Если нашли что-то похожее на access_token под другим ключом
            for k, v in all_ls.items():
                if 'access_token' in v or (v.startswith('eyJ') and len(v) > 100):
                    try:
                        parsed = json.loads(v)
                        if parsed.get('access_token'):
                            status(f"Токен найден под ключом '{k}'!")
                            # Сохраняем через _save_pvz_token
                            token_data_raw = parsed
                            token_data = {
                                "access_token": token_data_raw.get("access_token"),
                                "refresh_token": token_data_raw.get("refresh_token"),
                                "expire_time": token_data_raw.get("expire_time"),
                                "refresh_expire_time": token_data_raw.get("refresh_expire_time"),
                            }
                            if token_data["access_token"]:
                                _save_browser_cookies(driver)
                                _save_pvz_token(token_data)
                                return token_data
                    except Exception:
                        pass

        # Пробуем auth endpoints напрямую через Chrome (с его куками)
        status("Пробую auth endpoints...")
        cur_url = driver.current_url
        ozon_id_token = ""
        if "token=" in cur_url:
            try:
                from urllib.parse import urlparse, parse_qs
                ozon_id_token = parse_qs(urlparse(cur_url).query).get("token", [""])[0]
            except Exception:
                pass
        if ozon_id_token:
            probe_result = driver.execute_async_script("""
                var cb = arguments[arguments.length - 1];
                var token = arguments[0];
                var eps = [
                    '/api2/auth/callback',
                    '/api2/auth/exchange',
                    '/api2/auth/pvz',
                    '/api2/auth/sso/callback',
                    '/api2/auth/ozonid',
                    '/api2/auth/login',
                ];
                Promise.all(eps.map(function(ep){
                    return fetch('https://turbo-pvz.ozon.ru' + ep, {
                        method: 'POST',
                        headers: {'Content-Type':'application/json','Accept':'application/json'},
                        body: JSON.stringify({token: token}),
                        credentials: 'include'
                    }).then(function(r){
                        return r.text().then(function(b){ return {ep:ep, s:r.status, b:b.slice(0,150)}; });
                    }).catch(function(e){ return {ep:ep, s:0, b:String(e).slice(0,60)}; });
                })).then(cb);
            """, ozon_id_token)
            if probe_result:
                status(f"Probe results: {[(p['ep'], p['s'], p['b'][:60]) for p in probe_result]}")
                # Ищем ответ с access_token
                for p in probe_result:
                    try:
                        d = json.loads(p.get('b', ''))
                        if d.get('access_token') or d.get('accessToken'):
                            at = d.get('access_token') or d.get('accessToken')
                            status(f"ТОКЕН НАЙДЕН через {p['ep']}!")
                            token_data = {
                                "access_token": at,
                                "refresh_token": d.get('refresh_token') or d.get('refreshToken'),
                                "expire_time": d.get('expire_time'),
                                "refresh_expire_time": d.get('refresh_expire_time'),
                            }
                            _save_browser_cookies(driver)
                            _save_pvz_token(token_data)
                            return token_data
                    except Exception:
                        pass

        token_json = _early_token
        if not token_json:
            for i in range(60):
                time.sleep(1)
                token_json = driver.execute_script(
                    "return localStorage.getItem('pvz-access-token');"
                )
                if token_json:
                    break
                # Диагностика каждые 15 сек
                if i % 15 == 14:
                    cur = driver.current_url[:80]
                    keys = driver.execute_script("return Object.keys(localStorage);") or []
                    # Показываем ВСЕ перехваченные fetch/XHR-вызовы SPA
                    captured = driver.execute_script("return window._pvzCapture || null;")
                    if captured:
                        calls = captured.get("calls", [])
                        # Все вызовы (последние 5) — не только auth
                        all_calls = [(c['url'][-70:], c.get('method',''), c.get('status'), c.get('body','')[:60]) for c in calls[-5:]]
                        status(f"URL: {cur}\nLS keys: {keys}\nВсе вызовы: {all_calls}")
                    else:
                        status(f"URL: {cur}\nLS keys: {keys}\n_pvzCapture=None (перехватчик не работает)")
        if not token_json:
            current_url = driver.current_url
            all_keys = driver.execute_script(
                "return Object.keys(localStorage);"
            )
            raise RuntimeError(
                f"pvz-access-token не найден в localStorage\n"
                f"URL: {current_url[:120]}\n"
                f"localStorage keys: {all_keys}"
            )

        token_data_raw = json.loads(token_json)
        token_data = {
            "access_token": token_data_raw.get("access_token"),
            "refresh_token": token_data_raw.get("refresh_token"),
            "expire_time": token_data_raw.get("expire_time"),
            "refresh_expire_time": token_data_raw.get("refresh_expire_time"),
        }

        if not token_data["access_token"]:
            raise RuntimeError("access_token пустой в localStorage")

        # Fallback: если expire_time не задан — берём из JWT claims
        if not token_data["expire_time"]:
            claims_at = _jwt_decode(token_data["access_token"])
            exp = claims_at.get("exp", 0)
            token_data["expire_time"] = exp * 1000 if exp else int(time.time() + 8 * 3600) * 1000
        if not token_data["refresh_expire_time"] and token_data.get("refresh_token"):
            claims_rt = _jwt_decode(token_data["refresh_token"])
            exp_rt = claims_rt.get("exp", 0)
            token_data["refresh_expire_time"] = exp_rt * 1000 if exp_rt else int(time.time() + 7 * 86400) * 1000

        # Проверяем ClientType и StoreId
        claims = _jwt_decode(token_data["access_token"])
        client_type = claims.get("ClientType", "?")
        store_id_in_token = claims.get("StoreId", "")
        status(f"Токен получен (ClientType={client_type}, StoreId={store_id_in_token or 'нет'})")

        # Step 8: Если нет StoreId — нужно открыть страницу магазина, чтобы получить токен с StoreId
        # turbo-pvz.ozon.ru после входа выдаёт общий токен. Только при переходе на конкретный магазин
        # (URL /<uuid>) SPA обновляет pvz-access-token и добавляет StoreId.
        if not store_id_in_token:
            status("StoreId отсутствует — открываю страницу магазина...")
            store_uuid = None
            try:
                from config import OZON_STORE_UUID
                store_uuid = OZON_STORE_UUID
            except (ImportError, AttributeError):
                pass

            if store_uuid:
                status(f"Перехожу на магазин из конфига: {store_uuid}")
                driver.get(f"https://turbo-pvz.ozon.ru/{store_uuid}")
            else:
                # Идём на главную и кликаем первую ссылку с UUID-подобным href
                status("OZON_STORE_UUID не задан — ищу магазин на главной странице...")
                driver.get("https://turbo-pvz.ozon.ru/")
                time.sleep(4)
                clicked_store = driver.execute_script("""
                    var uuidRe = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
                    var links = Array.from(document.querySelectorAll('a'));
                    for (var el of links) {
                        var href = el.getAttribute('href') || '';
                        if (uuidRe.test(href)) {
                            el.click();
                            return href;
                        }
                    }
                    var btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                    for (var btn of btns) {
                        var r = btn.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) { btn.click(); return 'first-button'; }
                    }
                    return null;
                """)
                status(f"Кликнул на магазин: {clicked_store}")

            # Ждём новый pvz-access-token со StoreId (до 30 сек)
            for _si in range(30):
                time.sleep(1)
                t = driver.execute_script("return localStorage.getItem('pvz-access-token');")
                if t:
                    try:
                        d = json.loads(t)
                        new_claims = _jwt_decode(d.get("access_token", ""))
                        if new_claims.get("StoreId"):
                            status(f"Получен токен со StoreId={new_claims['StoreId']}")
                            token_data = {
                                "access_token": d.get("access_token"),
                                "refresh_token": d.get("refresh_token"),
                                "expire_time": d.get("expire_time"),
                                "refresh_expire_time": d.get("refresh_expire_time"),
                            }
                            if not token_data["expire_time"]:
                                exp = new_claims.get("exp", 0)
                                token_data["expire_time"] = exp * 1000 if exp else int(time.time() + 8 * 3600) * 1000
                            if not token_data["refresh_expire_time"] and token_data.get("refresh_token"):
                                claims_rt = _jwt_decode(token_data["refresh_token"])
                                exp_rt = claims_rt.get("exp", 0)
                                token_data["refresh_expire_time"] = exp_rt * 1000 if exp_rt else int(time.time() + 7 * 86400) * 1000
                            break
                    except Exception:
                        pass
                if _si % 10 == 9:
                    cur = driver.current_url[:80]
                    status(f"Жду StoreId ({_si+1}с), URL: {cur}")
            else:
                status("⚠️ StoreId не появился — сохраняю токен без StoreId (отчёты могут не работать)")

        # Step 9: Сохраняем SSO куки из браузера
        _save_browser_cookies(driver)

        # Step 10: Сохраняем PVZ токен
        _save_pvz_token(token_data)

        return token_data

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def _save_browser_cookies(driver):
    """Сохраняет SSO куки из Chrome в ozon_session.json."""
    from config import OZON_SESSION_FILE

    browser_cookies = driver.get_cookies()
    sso_cookies = []
    for c in browser_cookies:
        if c.get("domain", "").endswith("ozon.ru"):
            sso_cookies.append({
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ".ozon.ru"),
                "path": c.get("path", "/"),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", True),
                "sameSite": c.get("sameSite", "Lax"),
                "expires": int(c.get("expiry", time.time() + 365 * 86400)),
            })

    if sso_cookies:
        os.makedirs(os.path.dirname(OZON_SESSION_FILE), exist_ok=True)
        with open(OZON_SESSION_FILE, "w") as f:
            json.dump({"cookies": sso_cookies, "origins": []}, f, indent=2)
        os.chmod(OZON_SESSION_FILE, 0o600)


def _save_pvz_token(token_data: dict):
    """Сохраняет PVZ токен."""
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)
