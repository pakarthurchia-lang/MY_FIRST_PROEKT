"""
Авторизация Ozon через бот.

Флоу /login (Web токен через Chrome):
  /login → бот просит телефон → Chrome открывает id.ozon.ru →
  → Ozon отправляет код на почту/SMS → пользователь вводит код →
  → бот получает Web PVZ токен → reports и fines работают

Работает на Mac/Linux с установленным Chrome.
"""
import html
import json
import os
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import OWNER_CHAT_ID, OZON_PHONE

router = Router()


class OzonLoginState(StatesGroup):
    waiting_phone = State()
    waiting_code = State()


# ── /login ────────────────────────────────────────────────────────────────────

@router.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    await state.clear()

    if OZON_PHONE:
        await _start_web_login(message, state, OZON_PHONE)
    else:
        await state.set_state(OzonLoginState.waiting_phone)
        await message.answer(
            "📱 <b>Авторизация Ozon</b>\n\n"
            "Введи номер телефона или email аккаунта Ozon PVZ\n"
            "(например: +79991234567 или user@mail.ru):",
            parse_mode="HTML",
        )


@router.message(OzonLoginState.waiting_phone)
async def handle_phone(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    await _start_web_login(message, state, message.text.strip())


async def _start_web_login(message: Message, state: FSMContext, phone: str):
    """Запускает Chrome-логин и ждёт код."""
    # Нормализуем телефон (email не трогаем)
    if "@" not in phone:
        phone = "".join(c for c in phone if c.isdigit() or c == "+")
        if not phone.startswith("+"):
            phone = "+7" + phone.lstrip("78") if phone.startswith(("7", "8")) else "+" + phone

    await message.answer(
        f"⏳ Открываю страницу логина Ozon для <b>{phone}</b>...\n"
        f"(Chrome headless)",
        parse_mode="HTML",
    )

    # Запускаем Chrome-логин
    try:
        from ozon.web_login import login_ozon_web
    except ImportError as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Chrome не настроен:</b>\n<code>{e}</code>\n\n"
            f"Установи: pip install undetected-chromedriver",
            parse_mode="HTML",
        )
        return

    await state.set_state(OzonLoginState.waiting_code)

    # callback — вызывается когда Chrome ждёт код
    # Просто отправляет сообщение; реальный код берётся через _get_pending_code_sync
    async def get_code() -> str:
        await message.answer(
            "📨 <b>Ozon отправил код подтверждения</b>\n\n"
            "Введи код из SMS или email:",
            parse_mode="HTML",
        )

    # callback — статусные сообщения
    async def on_status(msg: str):
        try:
            await message.answer(f"🔄 {msg}")
        except Exception:
            pass

    try:
        token_data = await login_ozon_web(
            phone=phone,
            get_code=get_code,
            on_status=on_status,
        )
    except RuntimeError as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Ошибка логина:</b>\n\n<code>{html.escape(str(e))}</code>\n\n"
            f"Попробуй /login снова или /ozon_token для ручного обновления.",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Неожиданная ошибка:</b>\n\n<code>{html.escape(f'{type(e).__name__}: {e}')}</code>",
            parse_mode="HTML",
        )
        return

    await state.clear()

    # Сбрасываем кэш токена
    from ozon import http_client
    http_client._token_data = {}

    # Определяем тип токена
    from ozon.http_client import _jwt_decode
    claims = _jwt_decode(token_data.get("access_token", ""))
    client_type = claims.get("ClientType", "?")

    await message.answer(
        f"✅ <b>Ozon авторизация успешна!</b>\n\n"
        f"Тип токена: <b>{client_type}</b>\n"
        f"{'📊 Reports и прибыль доступны!' if client_type == 'Web' else '⚠️ Mobile токен — reports ограничены'}\n\n"
        f"Токен будет автоматически обновляться.",
        parse_mode="HTML",
    )


@router.message(OzonLoginState.waiting_code)
async def handle_code(message: Message, state: FSMContext):
    """Получает код от пользователя и передаёт в Chrome."""
    if message.from_user.id != OWNER_CHAT_ID:
        return

    if not message.text:
        # Фото/файл/стикер — игнорируем, ждём текстовый код
        return

    code = message.text.strip()
    data = await state.get_data()

    # Находим Future и устанавливаем результат
    # Future передаётся через замыкание в _start_web_login
    # Здесь мы не можем напрямую обратиться к Future,
    # поэтому используем глобальный механизм
    _set_pending_code(code)

    await message.answer("⏳ Проверяю код...")


# Механизм передачи кода между async handler и Chrome thread
import concurrent.futures as _cf
_code_future: "_cf.Future | None" = None


def _set_pending_code(code: str):
    global _code_future
    if _code_future is not None and not _code_future.done():
        _code_future.set_result(code)


def _get_pending_code_sync(timeout: float = 300) -> str:
    global _code_future
    _code_future = _cf.Future()
    try:
        return _code_future.result(timeout=timeout)
    except Exception:
        return ""
    finally:
        _code_future = None


# ── /setup — установка закладок ───────────────────────────────────────────────

@router.message(Command("setup"))
async def cmd_setup(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "setup", "bookmarklets.html")
    html_path = os.path.normpath(html_path)

    await message.answer(
        "📎 <b>Установка кнопок для браузера</b>\n\n"
        "Открой файл ниже на компьютере в браузере и перетащи кнопки в панель закладок.\n\n"
        "После этого обновлять токен будет просто:\n"
        "нажать кнопку → скопировать → вставить в бота.",
        parse_mode="HTML",
    )
    await message.answer_document(
        FSInputFile(html_path, filename="Настройка ПВЗ бота.html"),
    )


# ── /ozon_token — ручное обновление (запасной вариант) ────────────────────────

@router.message(Command("ozon_token"))
async def cmd_ozon_token(message: Message):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "🔑 <b>Ручное обновление Ozon токена</b>\n\n"
            "Основной способ — через бота: /login\n\n"
            "Запасной (если /login не работает):\n"
            "1. Открой <b>turbo-pvz.ozon.ru</b> в браузере\n"
            "2. DevTools → Консоль (F12 или ⌘+Option+C)\n"
            "3. Введи: <code>localStorage.getItem('pvz-access-token')</code>\n"
            "4. Скопируй результат и отправь:\n"
            "<code>/ozon_token {вставь сюда}</code>",
            parse_mode="HTML",
        )
        return

    raw = parts[1].strip()

    if raw.startswith('"') and raw.endswith('"'):
        try:
            raw = json.loads(raw)
        except Exception:
            pass

    try:
        data = json.loads(raw)
    except Exception:
        await message.answer(
            "❌ Не удалось разобрать JSON.\n"
            "Убедись что скопировал полное значение включая фигурные скобки."
        )
        return

    token_data = {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expire_time": data.get("expire_time"),
        "refresh_expire_time": data.get("refresh_expire_time"),
    }
    if not token_data["access_token"]:
        await message.answer("❌ Поле access_token не найдено в данных.")
        return

    from ozon.http_client import _save_token
    _save_token(token_data)

    from ozon import http_client
    http_client._token_data = {}

    try:
        await message.delete()
    except Exception:
        pass

    await message.answer("✅ Ozon токен обновлён и сохранён.")
