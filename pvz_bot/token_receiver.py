"""
Локальный сервер для получения токена из браузера.
После запуска выполни JS-код в консоли Safari.
"""
import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import browser_cookie3
from config import OZON_SESSION_FILE

TOKEN_FILE = "data/ozon_token.json"


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        data = json.loads(body)

        token_data = {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expire_time": data.get("expire_time"),
            "refresh_expire_time": data.get("refresh_expire_time"),
        }

        os.makedirs("data", exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f, indent=2)

        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')
        print(f"\n✅ Токен получен и сохранён в {TOKEN_FILE}")
        print("Можешь закрыть этот скрипт (Ctrl+C) и запустить бота: python main.py")

    def log_message(self, *args):
        pass


def import_cookies():
    print("Читаю куки из Safari...")
    try:
        jar = browser_cookie3.safari(domain_name="ozon.ru")
        cookies = list(jar)
        playwright_cookies = []
        for c in cookies:
            playwright_cookies.append({
                "name": c.name,
                "value": c.value,
                "domain": c.domain if c.domain.startswith(".") else f".{c.domain}",
                "path": c.path or "/",
                "httpOnly": bool(getattr(c, "_rest", {}).get("HttpOnly", False)),
                "secure": bool(c.secure),
                "sameSite": "Lax",
                "expires": int(c.expires) if c.expires and c.expires > 0 else -1,
            })
        os.makedirs(os.path.dirname(OZON_SESSION_FILE), exist_ok=True)
        with open(OZON_SESSION_FILE, "w") as f:
            json.dump({"cookies": playwright_cookies, "origins": []}, f, indent=2)
        print(f"✅ Сохранено {len(playwright_cookies)} куки")
    except Exception as e:
        print(f"⚠️  Куки: {e}")


if __name__ == "__main__":
    import_cookies()

    print("\n" + "="*55)
    print("Теперь открой Safari DevTools → Консоль")
    print("и вставь туда этот код:")
    print("="*55)
    print("""
fetch('http://localhost:8765', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: localStorage.getItem('pvz-access-token')
})
""")
    print("="*55)
    print("\nОжидаю токен...")

    server = HTTPServer(("localhost", 8765), Handler)
    server.handle_request()
