import asyncio
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from ozon import auth as ozon_auth
from config import OWNER_CHAT_ID

router = Router()

# Очередь для передачи кода из хендлера в Playwright
_code_queue: asyncio.Queue = asyncio.Queue()


class AuthStates(StatesGroup):
    waiting_code = State()


async def _wait_for_code_from_user() -> str:
    """Колбэк для ozon/auth.py — ждёт кода от пользователя через бота"""
    return await asyncio.wait_for(_code_queue.get(), timeout=120)


# Регистрируем колбэк при старте
ozon_auth.set_code_callback(_wait_for_code_from_user)


@router.message(F.text == "/login")
async def cmd_login(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    await message.answer("🔐 Запускаю авторизацию в Ozon ПВЗ...\nКод из SMS или email придёт тебе — введи его здесь.")
    await state.set_state(AuthStates.waiting_code)

    asyncio.create_task(_do_login(message))


async def _do_login(message: Message):
    try:
        from ozon.auth import clear_session, get_context
        await clear_session()
        await get_context()
        await message.answer("✅ Авторизация успешна! Сессия сохранена.")
    except asyncio.TimeoutError:
        await message.answer("⏰ Время ожидания кода истекло. Попробуй /login снова.")
    except Exception as e:
        await message.answer(f"❌ Ошибка авторизации: {e}\nПопробуй /login снова.")


@router.message(AuthStates.waiting_code)
async def handle_auth_code(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return

    code = message.text.strip()
    await _code_queue.put(code)
    await state.clear()
    await message.answer(f"✅ Код {code} отправлен. Жди подтверждения...")
