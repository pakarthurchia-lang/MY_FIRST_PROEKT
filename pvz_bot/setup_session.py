"""
Открывает браузер — войди вручную, сессия сохранится автоматически.
"""
import asyncio
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from config import OZON_SESSION_FILE

OZON_URL = "https://turbo-pvz.ozon.ru"


async def setup():
    os.makedirs(os.path.dirname(OZON_SESSION_FILE), exist_ok=True)
    stealth = Stealth()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        # Загружаем Safari куки если есть — они содержат cf_clearance для Cloudflare
        ctx_kwargs = dict(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1280, "height": 800},
        )
        if os.path.exists(OZON_SESSION_FILE):
            ctx_kwargs["storage_state"] = OZON_SESSION_FILE
            print("Загружаю существующие куки...")

        ctx = await browser.new_context(**ctx_kwargs)
        page = await ctx.new_page()
        await stealth.apply_stealth_async(page)

        print("=" * 50)
        print("Открываю браузер...")
        print("Войди под аккаунтом сотрудника (89185430692)")
        print("После входа нажми Enter в этом окне")
        print("=" * 50)

        await page.goto(OZON_URL, timeout=30000)

        input("\n⏳ Войди в браузере, затем нажми Enter здесь...")

        current_url = page.url
        if "login" in current_url or "sso" in current_url:
            print(f"⚠️  Похоже вход не завершён (URL: {current_url})")
            input("Попробуй войти ещё раз и снова нажми Enter...")

        await ctx.storage_state(path=OZON_SESSION_FILE)
        print(f"\n✅ Сессия сохранена в {OZON_SESSION_FILE}")
        print("Теперь запусти бота: python main.py")

        await browser.close()


asyncio.run(setup())
