"""
Импортирует сессию из Safari:
1. Куки — автоматически через browser_cookie3
2. Токен — вставь значение pvz-access-token из localStorage Safari
"""
import json
import os
import browser_cookie3
from config import OZON_SESSION_FILE

TOKEN_FILE = "data/ozon_token.json"


def import_cookies():
    print("Читаю куки из Safari...")
    try:
        jar = browser_cookie3.safari(domain_name="ozon.ru")
        cookies = list(jar)
    except Exception as e:
        print(f"Ошибка чтения куки: {e}")
        return False

    if not cookies:
        print("Куки не найдены")
        return False

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
            "expires": int(c.expires) if c.expires and c.expires > 0 else -1,
        }
        playwright_cookies.append(cookie)

    os.makedirs(os.path.dirname(OZON_SESSION_FILE), exist_ok=True)
    with open(OZON_SESSION_FILE, "w") as f:
        json.dump({"cookies": playwright_cookies, "origins": []}, f, indent=2)

    print(f"✅ Сохранено {len(playwright_cookies)} куки")
    return True


def import_token():
    print("\nТеперь нужен токен из Safari localStorage.")
    print("В Safari DevTools → Хранилище → Локальная память → turbo-pvz.ozon.ru")
    print("Кликни на строку 'pvz-access-token' → скопируй ПОЛНОЕ значение (весь JSON)")
    print()
    raw = input("Вставь значение pvz-access-token: ").strip()

    try:
        data = json.loads(raw)
    except Exception:
        print("❌ Ошибка: не удалось распарсить JSON")
        return False

    token_data = {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expire_time": data.get("expire_time"),
        "refresh_expire_time": data.get("refresh_expire_time"),
    }

    if not token_data["access_token"]:
        print("❌ Не найден access_token в данных")
        return False

    os.makedirs("data", exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)

    print(f"✅ Токен сохранён в {TOKEN_FILE}")
    return True


if __name__ == "__main__":
    ok1 = import_cookies()
    ok2 = import_token()

    if ok1 and ok2:
        print("\n✅ Сессия импортирована! Запусти бота: python main.py")
    else:
        print("\n⚠️  Что-то пошло не так, проверь ошибки выше")
