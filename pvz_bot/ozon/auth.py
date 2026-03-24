import asyncio
import os
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth
from config import OZON_URL, OZON_PHONE, OZON_SESSION_FILE

_browser: Browser = None
_context: BrowserContext = None
_code_callback = None  # async callable — возвращает SMS код от пользователя
_stealth = Stealth()


def set_code_callback(callback):
    global _code_callback
    _code_callback = callback


async def get_browser() -> Browser:
    global _browser
    if _browser is None or not _browser.is_connected():
        pw = await async_playwright().start()
        _browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
    return _browser


async def _new_stealth_context(browser: Browser, **kwargs) -> BrowserContext:
    ctx = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        **kwargs
    )
    return ctx


async def get_context() -> BrowserContext:
    global _context

    if _context is not None:
        return _context

    browser = await get_browser()

    if os.path.exists(OZON_SESSION_FILE):
        _context = await _new_stealth_context(browser, storage_state=OZON_SESSION_FILE)
        return _context

    raise RuntimeError("Сессия не найдена. Запусти import_safari_cookies.py и перезапусти бота.")


async def _login(context: BrowserContext):
    page = await context.new_page()
    await _stealth.apply_stealth_async(page)

    # Открываем кабинет — он редиректит на sso.ozon.ru
    await page.goto(OZON_URL, timeout=30000)
    await page.wait_for_load_state("networkidle")

    # Ждём поля ввода телефона
    await _fill_phone(page)

    # Ждём поле для SMS кода
    await asyncio.sleep(3)
    await _fill_sms_code(page)

    # Ждём редирект обратно в кабинет
    await page.wait_for_url(f"{OZON_URL}/**", timeout=30000)
    await page.wait_for_load_state("networkidle")

    # Сохраняем сессию
    os.makedirs(os.path.dirname(OZON_SESSION_FILE), exist_ok=True)
    await context.storage_state(path=OZON_SESSION_FILE)
    await page.close()


async def _fill_phone(page: Page):
    selectors = [
        "input[type='tel']",
        "input[name='phone']",
        "input[placeholder*='телефон']",
        "input[placeholder*='номер']",
        "input[autocomplete='tel']",
    ]
    for sel in selectors:
        try:
            inp = page.locator(sel).first
            await inp.wait_for(timeout=5000)
            await inp.fill(OZON_PHONE)
            await page.keyboard.press("Enter")
            return
        except Exception:
            continue

    raise RuntimeError("Не нашёл поле ввода телефона на странице логина")


async def _fill_sms_code(page: Page):
    selectors = [
        "input[name='code']",
        "input[autocomplete='one-time-code']",
        "input[placeholder*='код']",
        "input[maxlength='4']",
        "input[maxlength='6']",
        "input[type='number']",
    ]

    code_input = None
    for sel in selectors:
        try:
            inp = page.locator(sel).first
            await inp.wait_for(timeout=8000)
            code_input = inp
            break
        except Exception:
            continue

    if code_input is None:
        raise RuntimeError("Не нашёл поле ввода SMS кода")

    if _code_callback is None:
        raise RuntimeError("Нет обработчика для ввода кода — настрой бота")

    code = await asyncio.wait_for(_code_callback(), timeout=120)
    await code_input.fill(code)
    await page.keyboard.press("Enter")


async def clear_session():
    global _context
    if os.path.exists(OZON_SESSION_FILE):
        os.remove(OZON_SESSION_FILE)
    if _context:
        try:
            await _context.close()
        except Exception:
            pass
        _context = None
