"""
Читает Session_id Яндекса из Safari и сохраняет в data/yandex_token.json.
Запускать из папки pvz_bot/ пока открыт hubs.market.yandex.ru в Safari:
    python yandex/setup_token.py
"""

import json
import os
import browser_cookie3

TOKEN_FILE = "data/yandex_token.json"
PARTNER_ID = 44946604


def main():
    print("Читаю куки Яндекса из Safari...")

    try:
        jar = browser_cookie3.safari(domain_name="yandex.ru")
        cookies = list(jar)
    except Exception as e:
        print(f"❌ Ошибка чтения куки: {e}")
        return

    session_id = next((c.value for c in cookies if c.name == "Session_id"), None)

    if not session_id:
        print("❌ Session_id не найден.")
        print("Убедись что ты залогинен в Safari на hubs.market.yandex.ru")
        return

    print("✅ Session_id найден")

    data = {"session_id": session_id, "partner_id": PARTNER_ID}
    os.makedirs("data", exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)

    print(f"✅ Сохранено в {TOKEN_FILE} (права 600)")
    print("Теперь бот будет автоматически скачивать отчёты ЯМ.")


if __name__ == "__main__":
    main()
