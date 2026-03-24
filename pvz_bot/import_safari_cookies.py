"""
Читает куки ozon.ru из Safari и сохраняет в формат Playwright (ozon_session.json).
Запускай пока открыт turbo-pvz.ozon.ru в Safari под аккаунтом сотрудника.
"""
import json
import os
import browser_cookie3
from config import OZON_SESSION_FILE

DOMAINS = ["ozon.ru", "turbo-pvz.ozon.ru", "sso.ozon.ru"]


def main():
    print("Читаю куки из Safari...")

    try:
        jar = browser_cookie3.safari(domain_name="ozon.ru")
        cookies = list(jar)
    except Exception as e:
        print(f"Ошибка: {e}")
        return

    if not cookies:
        print("Куки не найдены. Убедись что ты залогинен в Safari на turbo-pvz.ozon.ru")
        return

    print(f"Найдено {len(cookies)} куки для ozon.ru")

    # Конвертируем в формат Playwright storage_state
    playwright_cookies = []
    for c in cookies:
        cookie = {
            "name": c.name,
            "value": c.value,
            "domain": c.domain if c.domain.startswith(".") else f".{c.domain}",
            "path": c.path or "/",
            "httpOnly": bool(getattr(c, "_rest", {}).get("HttpOnly", False)),
            "secure": bool(c.secure),
            "sameSite": "Lax",
        }
        if c.expires and c.expires > 0:
            cookie["expires"] = int(c.expires)
        else:
            cookie["expires"] = -1
        playwright_cookies.append(cookie)

    storage_state = {
        "cookies": playwright_cookies,
        "origins": []
    }

    os.makedirs(os.path.dirname(OZON_SESSION_FILE), exist_ok=True)
    with open(OZON_SESSION_FILE, "w") as f:
        json.dump(storage_state, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Сохранено {len(playwright_cookies)} куки в {OZON_SESSION_FILE}")
    print("Теперь запусти бота: python main.py")


if __name__ == "__main__":
    main()
