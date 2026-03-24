"""
Автоматически читает актуальный токен из Safari localStorage.
Работает без участия пользователя пока Safari открыт и залогинен.
"""
import json
import os
import shutil
import sqlite3
import tempfile

SAFARI_LS_PATH = (
    "/Users/arturpak/Library/Containers/com.apple.Safari/Data/Library/WebKit/"
    "WebsiteData/Default/18Ivx3YRqJnYlxvCWlfgr7yZsk5nsAA0P_JR3Ebynko/"
    "18Ivx3YRqJnYlxvCWlfgr7yZsk5nsAA0P_JR3Ebynko/LocalStorage/localstorage.sqlite3"
)

TOKEN_FILE = "data/ozon_token.json"


def read_token_from_safari() -> dict:
    """Читает pvz-access-token из Safari localStorage и возвращает словарь с токенами."""
    tmp_dir = tempfile.mkdtemp()
    try:
        tmp_db = os.path.join(tmp_dir, "ls.sqlite3")
        shutil.copy2(SAFARI_LS_PATH, tmp_db)
        # Копируем WAL и SHM файлы если есть
        for ext in ("-wal", "-shm"):
            src = SAFARI_LS_PATH + ext
            if os.path.exists(src):
                shutil.copy2(src, tmp_db + ext)

        conn = sqlite3.connect(tmp_db)
        rows = conn.execute("SELECT key, value FROM ItemTable").fetchall()
        conn.close()

        for k, v in rows:
            key = k.decode() if isinstance(k, bytes) else k
            if "pvz-access-token" in key:
                raw = v.decode("utf-16-le") if isinstance(v, bytes) else v
                if isinstance(raw, str):
                    data = json.loads(raw)
                else:
                    data = raw
                return {
                    "access_token": data.get("access_token"),
                    "refresh_token": data.get("refresh_token"),
                    "expire_time": data.get("expire_time"),
                    "refresh_expire_time": data.get("refresh_expire_time"),
                }
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать токен из Safari: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    raise RuntimeError("pvz-access-token не найден в Safari localStorage")


def update_token_from_safari() -> dict:
    """Обновляет ozon_token.json из Safari и возвращает данные."""
    token_data = read_token_from_safari()
    os.makedirs("data", exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    return token_data
