"""
Читает pvz-access-token из Safari localStorage.
Использует glob-поиск вместо хардкода пути — работает даже если Safari сменил директорию.
"""
import glob
import json
import os
import shutil
import sqlite3
import tempfile
from typing import Optional

SAFARI_BASE = (
    "/Users/arturpak/Library/Containers/com.apple.Safari/Data/"
    "Library/WebKit/WebsiteData/Default"
)
TOKEN_FILE = "data/ozon_token.json"


def _find_ozon_localstorage() -> Optional[str]:
    """Ищет файл localStorage для turbo-pvz.ozon.ru среди всех Safari хранилищ."""
    pattern = os.path.join(SAFARI_BASE, "*", "*", "LocalStorage", "localstorage.sqlite3")
    for path in glob.glob(pattern):
        try:
            tmp = tempfile.mktemp(suffix=".sqlite3")
            shutil.copy2(path, tmp)
            for ext in ("-wal", "-shm"):
                src = path + ext
                if os.path.exists(src):
                    shutil.copy2(src, tmp + ext)
            conn = sqlite3.connect(tmp)
            keys = [r[0] for r in conn.execute("SELECT key FROM ItemTable").fetchall()]
            conn.close()
            os.unlink(tmp)
            for k in keys:
                key = k.decode() if isinstance(k, bytes) else k
                if "pvz-access-token" in key:
                    return path
        except Exception:
            continue
    return None


def read_token_from_safari() -> dict:
    """Читает pvz-access-token из Safari localStorage. Возвращает словарь с токенами."""
    path = _find_ozon_localstorage()
    if not path:
        raise RuntimeError(
            "pvz-access-token не найден в Safari.\n"
            "Убедись что turbo-pvz.ozon.ru открыт в Safari и ты залогинен."
        )

    tmp_dir = tempfile.mkdtemp()
    try:
        tmp_db = os.path.join(tmp_dir, "ls.sqlite3")
        shutil.copy2(path, tmp_db)
        for ext in ("-wal", "-shm"):
            src = path + ext
            if os.path.exists(src):
                shutil.copy2(src, tmp_db + ext)

        conn = sqlite3.connect(tmp_db)
        rows = conn.execute("SELECT key, value FROM ItemTable").fetchall()
        conn.close()

        for k, v in rows:
            key = k.decode() if isinstance(k, bytes) else k
            if "pvz-access-token" not in key:
                continue
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
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    raise RuntimeError("pvz-access-token не найден в Safari localStorage")


def update_token_from_safari() -> dict:
    """Обновляет ozon_token.json из Safari и возвращает данные."""
    token_data = read_token_from_safari()
    os.makedirs("data", exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)
    return token_data
