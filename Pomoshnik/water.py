"""
Playwright automation for ordering water from вода24.рф
"""

import logging
import os
import tempfile
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

_TMP = tempfile.gettempdir()
log = logging.getLogger(__name__)

SITE = "https://xn--24-6kcajmz4cyak6czf.xn--p1ai"
URL_LOGIN = f"{SITE}/shop/login/"
URL_CART  = f"{SITE}/shop/order/"

_RU_MONTHS_NOM = {
    "Январь": 1, "Февраль": 2, "Март": 3, "Апрель": 4,
    "Май": 5, "Июнь": 6, "Июль": 7, "Август": 8,
    "Сентябрь": 9, "Октябрь": 10, "Ноябрь": 11, "Декабрь": 12,
}
_WEEKDAYS   = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_MONTH_SHORT = {1:"янв",2:"фев",3:"мар",4:"апр",5:"май",6:"июн",
                7:"июл",8:"авг",9:"сен",10:"окт",11:"ноя",12:"дек"}


class WaterOrderer:
    def __init__(self, login: str, password: str, city: str = "Ростов-на-Дону"):
        self._login    = login
        self._password = password
        self._city     = city
        self._pw       = None
        self._browser: Browser = None
        self._ctx: BrowserContext = None
        self.page: Page = None
        self._qty: int = 1

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
            if self._ctx:    await self._ctx.close()
            if self._browser: await self._browser.close()
            if self._pw:     await self._pw.stop()
        except Exception:
            pass

    async def _shot(self, name: str) -> str:
        path = os.path.join(_TMP, f"water_{name}.png")
        try:
            await self.page.screenshot(path=path, full_page=False)
        except Exception:
            pass
        return path

    # ── 1. login ───────────────────────────────────────────────────────────

    async def login(self) -> bool:
        await self.page.goto(URL_LOGIN, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1000)

        if "/login/" not in self.page.url:
            log.info("Already logged in")
            return True

        for sel in ['input[name="email"]', 'input[type="email"]', 'input[name="login"]']:
            el = self.page.locator(sel).first
            if await el.count():
                await el.fill(self._login)
                break

        await self.page.locator('input[type="password"]').first.fill(self._password)

        for sel in ['.wa-login-submit', 'input[type="submit"]', 'button[type="submit"]', 'button.submit']:
            btn = self.page.locator(sel).first
            if await btn.count():
                await btn.click()
                break

        try:
            await self.page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
        except Exception:
            pass

        await self.page.wait_for_timeout(1000)
        await self._shot("login_result")

        success = "/login/" not in self.page.url
        log.info(f"Login {'OK' if success else 'FAILED'}, url={self.page.url}")
        return success

    # ── 2. add to cart ─────────────────────────────────────────────────────

    async def add_to_cart(self, quantity: int, product_url: str) -> None:
        self._qty = quantity
        await self.page.goto(product_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1500)

        if quantity != 1:
            for sel in ['input[name="quantity"]', 'input.quantity', 'input[type="number"]']:
                el = self.page.locator(sel).first
                if await el.count():
                    await el.fill(str(quantity))
                    break

        await self._shot("product_page")

        added = False
        for sel in [
            'button[name="add_to_cart"]', '.js-add-to-cart',
            'button:has-text("В корзину")', 'button:has-text("корзину")',
        ]:
            btn = self.page.locator(sel).first
            if await btn.count():
                await btn.click()
                added = True
                break

        if not added:
            raise RuntimeError("Кнопка 'В корзину' не найдена")

        await self.page.wait_for_timeout(2000)
        await self._shot("popup_after_add")

        # Skip popup click — it closes too fast. Navigate directly to cart.
        await self.page.goto(URL_CART, wait_until="domcontentloaded", timeout=15000)

        await self.page.wait_for_timeout(2000)
        await self._shot("cart_page")

    # ── 3. get available delivery slots from calendar ──────────────────────

    async def get_delivery_slots(self) -> list[dict]:
        """
        Fill city → open calendar → read available dates → combine with time slots.
        Returns list of {"date_str": "14.06.2026", "label": "14 июн (вс)  9:00-15:00", "time": "..."}
        """
        # Update quantity in cart
        for sel in ['.s-cart-product-quantity input', 'input[name*="quantity"]', '.quantity input']:
            el = self.page.locator(sel).first
            if await el.count():
                await el.fill(str(self._qty))
                await el.press("Tab")
                await self.page.wait_for_timeout(1000)
                break

        # Scroll to delivery section
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
        await self.page.wait_for_timeout(800)

        # Fill city — after this date/time fields appear
        # Use Tab + blur instead of Enter to avoid triggering a form submit crash
        date_sel = 'input[name="details[custom][desired_delivery.date_str]"]'
        for sel in ['input[name="region[city]"]', '.js-city-field', 'input[placeholder*="ород"]']:
            el = self.page.locator(sel).first
            if await el.count():
                await el.fill(self._city)
                await self.page.wait_for_timeout(400)
                await self.page.evaluate("""
                    () => {
                        var sels = ['input[name="region[city]"]', '.js-city-field'];
                        for (var s of sels) {
                            var el = document.querySelector(s);
                            if (el) {
                                el.dispatchEvent(new Event('input',  {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                                el.blur();
                                break;
                            }
                        }
                    }
                """)
                await self.page.wait_for_timeout(500)
                break

        # Wait for date field to appear
        try:
            await self.page.wait_for_selector(date_sel, timeout=8000)
        except Exception:
            log.warning("Date field didn't appear after city fill")
            return []

        await self.page.wait_for_timeout(1000)

        # Get time slots
        time_slots = await self._get_time_slots()
        log.info(f"Time slots found: {time_slots}")

        # Open calendar
        date_el = self.page.locator(date_sel).first
        await date_el.click()
        await self.page.wait_for_timeout(800)
        await self._shot("calendar_open")

        # Read available dates via JavaScript
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        available_dates: list[datetime] = []

        for _ in range(2):  # current month + next
            cal = await self.page.evaluate("""
                () => {
                    var t = document.querySelector('.ui-datepicker-title');
                    var cells = document.querySelectorAll(
                        '.ui-datepicker-calendar td:not(.ui-datepicker-unselectable):not(.ui-state-disabled) a'
                    );
                    return {
                        title: t ? t.textContent.trim() : '',
                        days: Array.from(cells)
                              .map(c => parseInt(c.textContent.trim()))
                              .filter(n => !isNaN(n))
                    };
                }
            """)

            if not cal or not cal.get("title"):
                break

            parts = cal["title"].split()
            if len(parts) < 2:
                break

            month = _RU_MONTHS_NOM.get(parts[0], 0)
            try:
                year = int(parts[1])
            except ValueError:
                break

            if not month:
                break

            for day in cal.get("days", []):
                try:
                    dt = datetime(year, month, day)
                    if dt >= today and len(available_dates) < 7:
                        available_dates.append(dt)
                except ValueError:
                    pass

            if len(available_dates) >= 5:
                break

            await self.page.evaluate(
                "() => { var n=document.querySelector('.ui-datepicker-next'); if(n) n.click(); }"
            )
            await self.page.wait_for_timeout(400)

        # Close calendar
        await self.page.keyboard.press("Escape")
        await self.page.wait_for_timeout(300)

        # Fallback: use next 5 days if calendar read failed
        if not available_dates:
            log.warning("Calendar read failed — using next 5 days as fallback")
            available_dates = [today + timedelta(days=i) for i in range(1, 6)]

        if not time_slots:
            time_slots = ["9:00-15:00", "15:00-21:00"]

        slots = []
        for dt in available_dates:
            date_str  = dt.strftime("%d.%m.%Y")
            day_label = f"{dt.day} {_MONTH_SHORT[dt.month]} ({_WEEKDAYS[dt.weekday()]})"
            for ts in time_slots:
                slots.append({"date_str": date_str, "label": f"{day_label}  {ts}", "time": ts})

        return slots

    async def _get_time_slots(self) -> list[str]:
        for sel in [
            'select[name="details[custom][desired_delivery.interval]"]',
            '#wahtmlcontrol_details_custom_desired_delivery_interval',
            'select[name*="interval"]',
        ]:
            select = self.page.locator(sel).first
            if await select.count():
                options = await select.locator("option").all()
                return [
                    (await o.inner_text()).strip()
                    for o in options
                    if (await o.get_attribute("value") or "").strip()
                ]
        return []

    # ── 4. select date+time slot ───────────────────────────────────────────

    async def select_slot(self, date_str: str, time_slot: str) -> dict:
        date_sel = 'input[name="details[custom][desired_delivery.date_str]"]'
        date_el  = self.page.locator(date_sel).first

        if await date_el.count():
            await date_el.click()
            await self.page.wait_for_timeout(300)
            await date_el.fill(date_str)
            await date_el.press("Tab")
            await self.page.wait_for_timeout(800)

        for sel in [
            'select[name="details[custom][desired_delivery.interval]"]',
            '#wahtmlcontrol_details_custom_desired_delivery_interval',
        ]:
            select = self.page.locator(sel).first
            if await select.count():
                await select.select_option(value=time_slot)
                break

        await self.page.wait_for_timeout(1500)
        price = await self._get_price()
        await self._shot("confirm_screen")

        return {"qty": self._qty, "date_str": date_str, "time": time_slot, "price": price}

    async def _get_price(self) -> str:
        for sel in ['.s-order-total .price', '.s-total-price', '.js-total-price',
                    '.order-total', '[class*="total"]']:
            el = self.page.locator(sel).last
            if await el.count():
                txt = (await el.inner_text()).strip()
                if txt and any(c.isdigit() for c in txt):
                    return txt
        return "—"

    # ── 5. confirm order ───────────────────────────────────────────────────

    async def confirm_order(self) -> bool:
        api_responses: list[dict] = []

        async def _capture(response):
            if "/shop/" in response.url:
                try:
                    body = await response.text()
                except Exception:
                    body = ""
                api_responses.append({
                    "url": response.url,
                    "status": response.status,
                    "body": body[:500],
                })

        self.page.on("response", _capture)

        # Check any unchecked consent/agreement checkboxes
        await self.page.evaluate("""
            () => {
                document.querySelectorAll('input[type=checkbox]').forEach(cb => {
                    if (!cb.checked) {
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                });
            }
        """)
        await self.page.wait_for_timeout(300)

        # Freeze all scrolling so Playwright's native click (isTrusted=true) lands on the button
        await self.page.evaluate("""
            () => {
                window.__sw = window.scrollTo;
                window.__si = Element.prototype.scrollIntoView;
                window.scrollTo = () => {};
                Element.prototype.scrollIntoView = () => {};
                document.documentElement.style.overflow = 'hidden';
            }
        """)
        try:
            btn = self.page.locator('.js-submit-order-button').first
            clicked = bool(await btn.count())
            if clicked:
                await btn.click()
                log.info("Confirm button clicked (scroll frozen)")
            else:
                log.warning("Confirm button not found")
        finally:
            await self.page.evaluate("""
                () => {
                    window.scrollTo = window.__sw;
                    Element.prototype.scrollIntoView = window.__si;
                    document.documentElement.style.overflow = '';
                }
            """)

        if not clicked:
            await self._shot("confirm_button_not_found")
            return False

        await self.page.wait_for_timeout(3000)

        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

        await self.page.wait_for_timeout(1500)
        await self._shot("order_result")

        url = self.page.url

        # Still on the order form page = not confirmed
        if url.rstrip("/#").endswith("/shop/order"):
            log.warning(f"Still on order page. API responses: {api_responses}")
            return False

        # Check page body for error signs
        try:
            body = (await self.page.inner_text("body")).lower()
            if "http error 500" in body or "500 internal" in body:
                log.warning("Server returned HTTP 500 after confirm")
                return False
            if any(w in body for w in ["спасибо", "заказ оформлен", "заказ принят", "ваш заказ"]):
                log.info("Success text found on page")
                return True
        except Exception:
            pass

        # If URL changed away from order form — treat as success
        log.info(f"Order confirm result URL: {url}")
        return True


# ── date parser (used for voice/text commands) ─────────────────────────────

_MONTHS_GEN = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def parse_date(s: str) -> datetime:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    s = s.lower().strip()
    if s == "сегодня":    return today
    if s == "завтра":     return today + timedelta(days=1)
    if s == "послезавтра":return today + timedelta(days=2)
    parts = s.split()
    if len(parts) == 2 and parts[1] in _MONTHS_GEN:
        try:
            return today.replace(month=_MONTHS_GEN[parts[1]], day=int(parts[0]))
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
