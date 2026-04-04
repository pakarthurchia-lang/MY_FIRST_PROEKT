"""
Получение деталей претензии Ozon через headless Chrome.

Страница требует Web токен с StoreId (не Mobile) — скрапим через браузер
с SSO куками из ozon_session.json.

Возвращает:
{
  "claim_id": "21780592",
  "message": "Текст сообщения от Озон...",
  "date_issued": "31.03.2026",
  "direction": "Прямой поток",
  "amount": "854.29",
  "time_to_respond": "9 дней",
  "reason": "Утеря",
  "shipping_number": "129466183",
  "shipping_date": "29.03.2026",
}
"""
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "https://turbo-pvz.ozon.ru"
_executor = ThreadPoolExecutor(max_workers=1)


def _scrape_claim_sync(claim_id: str, store_id: str, request_type: str = "Claim") -> dict:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.page_load_strategy = "eager"

    driver = None
    try:
        driver = uc.Chrome(options=options, version_main=146)
        driver.set_page_load_timeout(45)

        # Инжектируем SSO куки через CDP
        driver.get("https://turbo-pvz.ozon.ru/blank.html")
        try:
            session_file = "data/ozon_session.json"
            with open(session_file) as f:
                state = json.load(f)
            for c in state.get("cookies", []):
                try:
                    cdp = {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", ".ozon.ru"),
                        "path": c.get("path", "/"),
                        "secure": c.get("secure", True),
                        "httpOnly": c.get("httpOnly", False),
                    }
                    if c.get("expires") and c["expires"] > 0:
                        cdp["expires"] = int(c["expires"])
                    driver.execute_cdp_cmd("Network.setCookie", cdp)
                except Exception:
                    pass
        except Exception:
            pass

        # Открываем страницу претензии
        url = f"{BASE_URL}/claims/detail/{claim_id}?storeId={store_id}&requestType={request_type}"
        driver.get(url)

        wait = WebDriverWait(driver, 20)

        # Ждём появления текста сообщения
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "p, .claim-message, [class*='message'], [class*='Message']")))
        except Exception:
            pass

        result = {
            "claim_id": claim_id,
            "message": "",
            "date_issued": "",
            "direction": "",
            "amount": "",
            "time_to_respond": "",
            "reason": "",
            "shipping_number": "",
            "shipping_date": "",
        }

        # Извлекаем текст через JS — ищем все текстовые блоки на странице
        page_text = driver.execute_script("""
            // Ищем основной блок с сообщением от Озон
            var msg = '';

            // Пробуем найти параграфы внутри карточки сообщения
            var selectors = [
                '[class*="message"] p',
                '[class*="Message"] p',
                '[class*="card"] p',
                '[class*="Card"] p',
                'main p',
                'article p',
            ];

            for (var i = 0; i < selectors.length; i++) {
                var els = document.querySelectorAll(selectors[i]);
                if (els.length > 0) {
                    var texts = [];
                    els.forEach(function(el) {
                        var t = el.innerText.trim();
                        if (t.length > 20) texts.push(t);
                    });
                    if (texts.length > 0) {
                        msg = texts.join('\\n');
                        break;
                    }
                }
            }

            // Если не нашли — берём весь main
            if (!msg) {
                var main = document.querySelector('main') || document.body;
                msg = main.innerText;
            }

            return msg;
        """)

        # Извлекаем боковую панель через JS
        sidebar = driver.execute_script("""
            var result = {};

            // Ищем пары label:value в боковой панели
            var allText = document.body.innerText;
            return allText;
        """)

        result["message"] = (page_text or "").strip()

        # Парсим боковую панель из текста страницы
        full_text = sidebar or ""
        lines = [l.strip() for l in full_text.split("\n") if l.strip()]

        label_map = {
            "Дата выставления": "date_issued",
            "Направление": "direction",
            "Сумма": "amount",
            "Время на ответ": "time_to_respond",
            "Причина": "reason",
            "Номер перевозки": "shipping_number",
            "Время перевозки": "shipping_date",
        }

        for i, line in enumerate(lines):
            for label, key in label_map.items():
                if label in line and i + 1 < len(lines):
                    result[key] = lines[i + 1]

        return result

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


async def get_claim_detail(claim_id: str, store_id: str, request_type: str = "Claim") -> dict:
    """Асинхронная обёртка над синхронным скрапером."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        _scrape_claim_sync,
        str(claim_id), str(store_id), request_type,
    )
