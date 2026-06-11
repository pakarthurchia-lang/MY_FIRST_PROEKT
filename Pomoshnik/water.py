"""
Playwright automation for ordering water from вода24.рф
"""

import logging
import os
import tempfile
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

_TMP = tempfile.gettempdir()  # C:\Users\...\AppData\Local\Temp on Windows, /tmp on Mac/Linux

log = logging.getLogger(__name__)

SITE = "https://xn--24-6kcajmz4cyak6czf.xn--p1ai"
URL_LOGIN = f"{SITE}/shop/login/"
URL_CART = f"{SITE}/shop/order/"


class WaterOrderer:
    """Manages a single water order session (one browser per user)."""

    def __init__(self, login: str, password: str, city: str = "Ростов-на-Дону"):
        self._login = login
        self._password = password
        self._city = city
        self._pw = None
        self._browser: Browser = None
        self._ctx: BrowserContext = None
        self.page: Page = None
        self._qty: int = 1
        self._date_str: str = ""

    async def start(self, headless: bool = True):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=headless)
        self._ctx = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ru-RU",
        )
        self.page = await self._ctx.new_page()

    async def close(self):
        try:
            if self._ctx:
                await self._ctx.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    # ── helpers ────────────────────────────────────────────────────────────

    async def _shot(self, name: str) -> str:
        path = os.path.join(_TMP, f"water_{name}.png")
        try:
            await self.page.screenshot(path=path, full_page=False)
        except Exception:
            pass
        return path

    # ── step 1: login ──────────────────────────────────────────────────────

    async def login(self) -> bool:
        await self.page.goto(URL_LOGIN, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1000)

        if "/login/" not in self.page.url:
            log.info("Already logged in")
            return True

        # Fill email — try common Webasyst and generic selectors
        for sel in ['input[name="email"]', 'input[type="email"]', 'input[name="login"]']:
            el = self.page.locator(sel).first
            if await el.count():
                await el.fill(self._login)
                break

        # Fill password
        await self.page.locator('input[type="password"]').first.fill(self._password)

        # Submit
        for sel in ['.wa-login-submit', 'input[type="submit"]', 'button[type="submit"]', 'button.submit']:
            btn = self.page.locator(sel).first
            if await btn.count():
                await btn.click()
                break

        # Wait for navigation away from login page (up to 10 seconds)
        try:
            await self.page.wait_for_url(
                lambda url: "/login/" not in url,
                timeout=10000,
            )
        except Exception:
            pass

        await self.page.wait_for_timeout(1000)
        await self._shot("login_result")

        success = "/login/" not in self.page.url
        log.info(f"Login {'OK' if success else 'FAILED'}, url={self.page.url}")
        return success

    # ── step 2: add to cart ────────────────────────────────────────────────

    async def add_to_cart(self, quantity: int, product_url: str) -> None:
        self._qty = quantity
        await self.page.goto(product_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1500)

        # Set quantity if needed
        if quantity != 1:
            for sel in [
                'input[name="quantity"]',
                'input.quantity',
                '.js-quantity-input',
                'input[type="number"]',
            ]:
                el = self.page.locator(sel).first
                if await el.count():
                    await el.triple_click()
                    await el.fill(str(quantity))
                    break

        await self._shot("product_page")

        # Click "В корзину" button
        added = False
        for sel in [
            'button[name="add_to_cart"]',
            '.js-add-to-cart',
            'button.add-to-cart',
            'button:has-text("В корзину")',
            'button:has-text("корзину")',
            'a:has-text("В корзину")',
        ]:
            btn = self.page.locator(sel).first
            if await btn.count():
                await btn.click()
                added = True
                break

        if not added:
            raise RuntimeError("Не найдена кнопка 'В корзину'. Скриншот: /tmp/water_product_page.png")

        await self.page.wait_for_timeout(2000)
        await self._shot("popup_after_add")

        # Popup: click "Перейти в корзину"
        went_to_cart = False
        for sel in [
            'a:has-text("Перейти в корзину")',
            'button:has-text("Перейти в корзину")',
            'a[href*="/order"]',
            '.js-go-to-cart',
        ]:
            btn = self.page.locator(sel).first
            if await btn.count():
                await btn.click()
                went_to_cart = True
                break

        if went_to_cart:
            await self.page.wait_for_load_state("networkidle")
        else:
            log.warning("Popup 'Перейти в корзину' не найден — перехожу напрямую")
            await self.page.goto(URL_CART, wait_until="networkidle")

        await self._shot("cart_page")

    # ── step 3: fill delivery form, get time slots ─────────────────────────

    async def fill_delivery(self, date_str: str) -> list[str]:
        """Fill quantity, city, date. Returns available time slots."""
        self._date_str = date_str
        await self.page.wait_for_load_state("networkidle")

        # Update quantity in cart (in case product page didn't apply it)
        for sel in [
            '.s-cart-product-quantity input',
            'input[name*="quantity"]',
            '.quantity input',
        ]:
            el = self.page.locator(sel).first
            if await el.count():
                await el.triple_click()
                await el.fill(str(self._qty))
                await el.press("Tab")
                await self.page.wait_for_timeout(1000)
                break

        # Scroll to delivery section
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
        await self.page.wait_for_timeout(800)

        # Fill city
        for sel in [
            'input[name="city"]',
            'input[name="params[city]"]',
            'input[placeholder*="ород"]',
            'input[placeholder*="Город"]',
        ]:
            el = self.page.locator(sel).first
            if await el.count():
                await el.triple_click()
                await el.fill(self._city)
                break

        await self._shot("before_date_pick")

        # Open date picker
        target_dt = parse_date(date_str)
        date_opened = False
        for sel in [
            'input[name="delivery_date"]',
            'input[name="params[delivery_date]"]',
            'input[class*="date"]',
            'input[placeholder*="ату"]',
            'input[placeholder*="Дату"]',
            '.datepicker-input',
        ]:
            el = self.page.locator(sel).first
            if await el.count():
                await el.click()
                await self.page.wait_for_timeout(800)
                date_opened = True
                await self._select_calendar_day(target_dt, el)
                break

        if not date_opened:
            log.warning("Поле даты не найдено, пробую прямой ввод")

        await self.page.wait_for_timeout(1000)
        await self._shot("after_date_pick")

        slots = await self._get_time_slots()
        log.info(f"Time slots: {slots}")
        return slots

    async def _select_calendar_day(self, dt: datetime, date_input):
        day = dt.day
        year = dt.year
        month = dt.month

        # Try to navigate calendar to the right month first (if needed)
        # Most pickers open on current month; if dt is next month, click ">"
        for _ in range(3):
            # Try clicking the day
            for sel in [
                f'.datepicker td:text-is("{day}"):not(.disabled)',
                f'.datepicker-days td.day:text-is("{day}"):not(.disabled)',
                f'td[data-day="{day}"]:not(.disabled)',
                f'[data-date*="{dt.strftime("%Y-%m-%d")}"]',
                f'.calendar td:text-is("{day}")',
                f'td.available:text-is("{day}")',
            ]:
                el = self.page.locator(sel).first
                if await el.count():
                    await el.click()
                    return

            # Navigate to next month
            nxt = self.page.locator('.datepicker .next, .datepicker-days .next').first
            if await nxt.count():
                await nxt.click()
                await self.page.wait_for_timeout(400)
            else:
                break

        # Fallback: type the date directly into the input
        log.warning(f"Не нашёл день {day} в календаре, ввожу текстом")
        await date_input.triple_click()
        await date_input.fill(dt.strftime("%d.%m.%Y"))
        await self.page.keyboard.press("Escape")

    async def _get_time_slots(self) -> list[str]:
        for sel in [
            'select[name="delivery_interval"]',
            'select[name="params[delivery_interval]"]',
            'select[name*="interval"]',
            'select[name*="time"]',
            '.delivery-time select',
        ]:
            select = self.page.locator(sel).first
            if await select.count():
                options = await select.locator("option").all()
                return [
                    (await o.inner_text()).strip()
                    for o in options
                    if (await o.get_attribute("value") or "").strip()
                       and (await o.inner_text()).strip()
                ]
        return []

    # ── step 4: select time, get summary ──────────────────────────────────

    async def select_time(self, time_slot: str) -> dict:
        for sel in [
            'select[name="delivery_interval"]',
            'select[name="params[delivery_interval]"]',
            'select[name*="interval"]',
            'select[name*="time"]',
        ]:
            select = self.page.locator(sel).first
            if await select.count():
                await select.select_option(label=time_slot)
                break

        await self.page.wait_for_timeout(1500)

        # Scrape total price
        price = ""
        for sel in [
            '.s-order-total .price',
            '.s-total-price',
            '.js-total-price',
            '.order-total',
            '[class*="total"]',
        ]:
            el = self.page.locator(sel).last
            if await el.count():
                txt = (await el.inner_text()).strip()
                if txt and any(c.isdigit() for c in txt):
                    price = txt
                    break

        await self._shot("confirm_screen")
        return {
            "qty": self._qty,
            "date": self._date_str,
            "time": time_slot,
            "price": price or "—",
        }

    # ── step 5: confirm ────────────────────────────────────────────────────

    async def confirm_order(self) -> bool:
        for sel in [
            'button:has-text("Подтвердить заказ")',
            'input[value="Подтвердить заказ"]',
            'button[type="submit"]:has-text("Подтвердить")',
            '.js-submit-order',
            'button.submit',
        ]:
            btn = self.page.locator(sel).first
            if await btn.count():
                await btn.click()
                await self.page.wait_for_load_state("networkidle")
                await self._shot("order_placed")
                return True

        await self._shot("confirm_button_not_found")
        return False


# ── date parser ────────────────────────────────────────────────────────────

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def parse_date(s: str) -> datetime:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    s = s.lower().strip()

    if s in ("сегодня",):
        return today
    if s in ("завтра",):
        return today + timedelta(days=1)
    if s in ("послезавтра",):
        return today + timedelta(days=2)

    parts = s.split()
    if len(parts) == 2 and parts[1] in _MONTHS:
        try:
            return today.replace(month=_MONTHS[parts[1]], day=int(parts[0]))
        except ValueError:
            pass

    for fmt in ("%d.%m.%Y", "%d.%m"):
        try:
            d = datetime.strptime(s, fmt)
            if fmt == "%d.%m":
                d = d.replace(year=today.year)
            return d
        except ValueError:
            pass

    return today + timedelta(days=1)
