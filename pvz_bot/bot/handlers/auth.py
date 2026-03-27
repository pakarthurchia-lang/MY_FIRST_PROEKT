"""
Авторизация Ozon через бот.

Флоу:
  /login → бот просит телефон → пользователь вводит → SMS → пользователь вводит код
         → токены сохранены → бот работает без браузера

Работает с любого устройства (телефон, десктоп).
Не требует Playwright, Safari, DevTools.
"""
import json
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
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

    # Если телефон уже есть в конфиге — сразу отправляем код
    if OZON_PHONE:
        await _request_code(message, state, OZON_PHONE)
    else:
        await state.set_state(OzonLoginState.waiting_phone)
        await message.answer(
            "📱 <b>Авторизация Ozon</b>\n\n"
            "Введи номер телефона или email аккаунта Ozon PVZ:",
            parse_mode="HTML",
        )


@router.message(OzonLoginState.waiting_phone)
async def handle_phone(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    await _request_code(message, state, message.text.strip())


async def _request_code(message: Message, state: FSMContext, login: str):
    await message.answer(f"⏳ Отправляю код на {login}...")
    try:
        from ozon.login import send_login_code
        ctx = await send_login_code(login)
    except RuntimeError as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Не удалось отправить код:</b>\n\n<code>{e}</code>\n\n"
            f"Попробуй /login снова или используй /ozon_token для ручного обновления.",
            parse_mode="HTML",
        )
        return

    await state.update_data(login_ctx=ctx)
    await state.set_state(OzonLoginState.waiting_code)
    await message.answer(
        f"✅ Код отправлен на <b>{login}</b>\n\n"
        f"Введи код из SMS или email:",
        parse_mode="HTML",
    )


@router.message(OzonLoginState.waiting_code)
async def handle_code(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    code = message.text.strip()
    data = await state.get_data()
    ctx = data.get("login_ctx")
    if not ctx:
        await state.clear()
        await message.answer("❌ Сессия устарела. Начни заново: /login")
        return

    await message.answer("⏳ Проверяю код...")
    try:
        from ozon.login import verify_login_code
        token_data = await verify_login_code(ctx, code)
    except RuntimeError as e:
        await state.clear()
        await message.answer(
            f"❌ <b>Ошибка подтверждения кода:</b>\n\n<code>{e}</code>\n\n"
            f"Попробуй /login снова.",
            parse_mode="HTML",
        )
        return

    await state.clear()

    # Сбрасываем кэш токена в http_client
    from ozon import http_client
    http_client._token_data = {}

    await message.answer(
        "✅ <b>Ozon авторизация успешна!</b>\n\n"
        "Токен сохранён. Бот будет автоматически обновлять его через refresh_token.",
        parse_mode="HTML",
    )


# ── /ozon_token — ручное обновление (запасной вариант) ────────────────────────

@router.message(Command("ozon_token"))
async def cmd_ozon_token(message: Message):
    """
    Ручное обновление токена.
    Использование: /ozon_token <JSON из localStorage>

    Как получить (десктоп, запасной вариант):
    1. Открой turbo-pvz.ozon.ru в браузере
    2. DevTools → Консоль → localStorage.getItem('pvz-access-token')
    3. /ozon_token <вставь>
    """
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

    # Убираем обёрточные кавычки если скопировали с ними
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
