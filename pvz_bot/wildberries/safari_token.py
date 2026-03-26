"""
Читает X-Token для pvz-lk.wb.ru из Safari localStorage.

Безопасность:
- Токен читается ТОЛЬКО локально с этого Mac
- Не логируется, не отправляется никуда кроме point-balance.wb.ru
- Хранится в data/wb_token.json (в .gitignore)
- Права на файл выставляются 600 (только владелец)
"""
import json
import os
import shutil
import sqlite3
import tempfile
import glob


TOKEN_FILE = "data/wb_token.json"
SAFARI_BASE = (
    "/Users/arturpak/Library/Containers/com.apple.Safari/Data/"
    "Library/WebKit/WebsiteData/Default"
)


def _find_wb_localstorage():
    """Ищет файл localStorage для pvz-lk.wb.ru среди всех Safari хранилищ."""
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
            # WB хранит токен под ключом содержащим 'token' или 'x-token'
            for k in keys:
                key = k.decode() if isinstance(k, bytes) else k
                if "token" in key.lower() or "auth" in key.lower():
                    return path
        except Exception:
            continue
    return None


def read_token_from_safari() -> str:
    """
    Читает X-Token из Safari localStorage pvz-lk.wb.ru.
    Возвращает строку токена.
    """
    path = _find_wb_localstorage()
    if not path:
        raise RuntimeError(
            "Токен WB не найден в Safari.\n"
            "Убедись что pvz-lk.wb.ru открыт и ты залогинен в Safari.\n"
            "Или запусти: python wildberries/setup_token.py"
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
            if "token" not in key.lower() and "auth" not in key.lower():
                continue
            raw = v.decode("utf-16-le") if isinstance(v, bytes) else v
            # Может быть строка напрямую или JSON-объект
            if isinstance(raw, str):
                raw = raw.strip()
                if raw.startswith('"'):
                    raw = json.loads(raw)  # строка в JSON
                elif raw.startswith("{"):
                    data = json.loads(raw)
                    # Ищем поле с токеном
                    for field in ("token", "x-token", "xToken", "accessToken", "access_token"):
                        if field in data:
                            return data[field]
                # Если это сам JWT (начинается с eyJ)
                if raw.startswith("eyJ"):
                    return raw
        raise RuntimeError("X-Token не найден в Safari localStorage для pvz-lk.wb.ru")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def update_token_from_safari() -> str:
    """Читает токен из Safari и сохраняет в data/wb_token.json. Возвращает токен."""
    token = read_token_from_safari()
    _save_token(token)
    return token


def _save_token(token: str):
    import time
    import base64

    os.makedirs("data", exist_ok=True)

    # Декодируем JWT для получения exp и pickpoint_id
    exp = 0
    pickpoint_id = None
    try:
        payload_b64 = token.split(".")[1]
        # Добавляем padding
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        exp = payload.get("exp", 0)
        pickpoint_id = payload.get("xpid")
    except Exception:
        pass

    data = {
        "x_token": token,
        "exp": exp,
        "pickpoint_id": pickpoint_id,
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)

    # Права только для владельца (безопасность)
    os.chmod(TOKEN_FILE, 0o600)
