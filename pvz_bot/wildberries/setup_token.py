"""
Ручная установка WB X-Token.

Запуск: python wildberries/setup_token.py

Как получить токен:
1. Открой pvz-lk.wb.ru в Safari и залогинься
2. DevTools → Network → XHR → найди partner-payments
3. В Request Headers скопируй значение X-Token
4. Вставь ниже когда скрипт спросит

Токен живёт 24 часа. После истечения запусти скрипт снова.
Файл data/wb_token.json находится в .gitignore — токен никуда не попадёт.
"""
import os
import sys

# Добавляем корень проекта в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wildberries.safari_token import _save_token, update_token_from_safari


def main():
    print("=== Настройка WB токена ===\n")

    # Сначала пробуем автоматически из Safari
    print("Пробую прочитать токен из Safari автоматически...")
    try:
        token = update_token_from_safari()
        print("✅ Токен успешно прочитан из Safari и сохранён в data/wb_token.json")
        _print_token_info()
        return
    except Exception as e:
        print(f"⚠️  Автоматически не получилось: {e}\n")

    # Ручной ввод
    print("Инструкция для ручного ввода:")
    print("1. Открой pvz-lk.wb.ru в Safari")
    print("2. DevTools (Option+Cmd+I) → Network → XHR")
    print("3. Обнови страницу")
    print("4. Кликни на partner-payments → Headers → скопируй X-Token\n")

    token = input("Вставь X-Token (начинается с eyJ...): ").strip()

    if not token.startswith("eyJ"):
        print("❌ Токен должен начинаться с 'eyJ'. Попробуй ещё раз.")
        sys.exit(1)

    _save_token(token)
    print("\n✅ Токен сохранён в data/wb_token.json (права 600 — только ты)")
    _print_token_info()


def _print_token_info():
    import json, time
    try:
        with open("data/wb_token.json") as f:
            data = json.load(f)
        exp = data.get("exp", 0)
        pid = data.get("pickpoint_id")
        remaining = int(exp - time.time())
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        print(f"   ID ПВЗ: {pid}")
        print(f"   Действует ещё: {hours}ч {minutes}мин")
    except Exception:
        pass


if __name__ == "__main__":
    main()
