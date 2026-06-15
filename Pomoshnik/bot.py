"""
Telegram-бот «Помощник» — модуль заказа воды.
Запуск: python bot.py
"""

import asyncio
import logging
import os
import sys
import tempfile
import requests as _requests

_TMP = tempfile.gettempdir()
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import BOT_TOKEN, WATER_LOGIN, WATER_PASSWORD, WATER_CITY, WATER_HEADLESS
from water import WaterOrderer, parse_date
from voice import transcribe, parse_water_command
from products import CATALOG, DEFAULT_PRODUCT, find_product

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Живые сессии браузера: user_id → WaterOrderer
_sessions: dict[int, WaterOrderer] = {}


# ── FSM states ─────────────────────────────────────────────────────────────

class WaterOrder(StatesGroup):
    bottles       = State()  # ожидаем кол-во бутылок
    choosing_slot = State()  # пользователь выбирает дату+время из keyboard
    confirm       = State()  # ожидаем подтверждения


# ── helpers ────────────────────────────────────────────────────────────────

async def _close_session(user_id: int):
    orderer = _sessions.pop(user_id, None)
    if orderer:
        await orderer.close()


def _slots_keyboard(slots: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=s["label"], callback_data=f"slot:{i}")]
        for i, s in enumerate(slots)
    ]
    rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить заказ", callback_data="confirm")],
        [InlineKeyboardButton(text="↩ Другая дата/время", callback_data="back_to_slots")],
        [InlineKeyboardButton(text="❌ Отменить",          callback_data="cancel")],
    ])


# ── core Playwright flow ────────────────────────────────────────────────────

async def _launch_and_get_slots(message: Message, state: FSMContext):
    """
    Запускаем браузер, логинимся, добавляем в корзину, читаем слоты из календаря.
    Показываем inline-keyboard с вариантами даты+времени.
    """
    user_id = message.from_user.id
    data = await state.get_data()
    qty         = data["qty"]
    product_key = data.get("product_key", DEFAULT_PRODUCT)
    product     = CATALOG[product_key]

    status_msg = await message.answer("⏳ Открываю сайт, вхожу в аккаунт…")

    orderer = WaterOrderer(WATER_LOGIN, WATER_PASSWORD, WATER_CITY)
    _sessions[user_id] = orderer

    try:
        await orderer.start(headless=WATER_HEADLESS)

        if not await orderer.login():
            await status_msg.edit_text(
                "❌ Не удалось войти в аккаунт.\n"
                f"Скриншот: {os.path.join(_TMP, 'water_login_result.png')}"
            )
            await _close_session(user_id)
            await state.clear()
            return

        await status_msg.edit_text(f"✅ Вошёл. Добавляю «{product['name']}» в корзину…")
        await orderer.add_to_cart(qty, product["url"])

        await status_msg.edit_text("📅 Читаю доступные даты доставки…")
        slots = await orderer.get_delivery_slots()

        # If user named a date in voice — show only that day's slots
        voice_date = data.get("voice_date")
        if voice_date and slots:
            target_str = parse_date(voice_date).strftime("%d.%m.%Y")
            filtered = [s for s in slots if s["date_str"] == target_str]
            if filtered:
                slots = filtered
                log.info(f"Voice date '{voice_date}' → filtered to {len(slots)} slots")

        if not slots:
            await status_msg.edit_text(
                "⚠️ Не нашёл доступные даты доставки.\n"
                "Проверь сайт вручную или попробуй позже.\n"
                f"Скриншот: {os.path.join(_TMP, 'water_calendar_open.png')}"
            )
            await _close_session(user_id)
            await state.clear()
            return

        await state.update_data(slots=slots)
        await state.set_state(WaterOrder.choosing_slot)

        await status_msg.edit_text(
            f"🛒 <b>«{product['name']}» × {qty} шт.</b>\n\n"
            "📅 Выбери дату и время доставки:",
            parse_mode="HTML",
            reply_markup=_slots_keyboard(slots),
        )

    except Exception as e:
        log.exception("Ошибка при подготовке заказа")
        err = str(e)[:200]
        await status_msg.edit_text(f"❌ Ошибка: {err}")
        await _close_session(user_id)
        await state.clear()


# ── shared business logic ──────────────────────────────────────────────────

async def _do_start_water(message: Message, state: FSMContext):
    await _close_session(message.from_user.id)
    await state.clear()
    await state.set_state(WaterOrder.bottles)
    await message.answer(
        "💧 <b>Заказ воды</b>\n\nСколько бутылок (19 л)?",
        parse_mode="HTML",
    )


async def _do_set_bottles(message: Message, state: FSMContext, text: str):
    qty = 1
    if text.isdigit():
        qty = int(text)
        if not 1 <= qty <= 10:
            await message.answer("Введи число от 1 до 10")
            return
    elif text not in ("", "1", "одну", "один", "одна"):
        await message.answer("Введи число от 1 до 10")
        return

    data = await state.get_data()
    await state.update_data(qty=qty, product_key=data.get("product_key", DEFAULT_PRODUCT))
    await _launch_and_get_slots(message, state)


# ── voice: download → transcribe → route ──────────────────────────────────

async def _handle_voice_input(message: Message, state: FSMContext):
    status = await message.answer("🎙 Распознаю…")

    tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    tmp.close()
    try:
        file = await bot.get_file(message.voice.file_id)
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"

        # requests с proxies={} — полностью обходим SOCKS-прокси
        def _download():
            r = _requests.get(url, proxies={"http": None, "https": None}, timeout=30)
            r.raise_for_status()
            with open(tmp.name, "wb") as f:
                f.write(r.content)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _download)
        text = await transcribe(tmp.name)
    except Exception as e:
        log.exception("Voice download/transcribe failed")
        await status.edit_text(f"❌ Не удалось распознать голос: {str(e)[:200]}")
        return
    finally:
        os.unlink(tmp.name)

    await status.edit_text(f"🎙 «{text}»")

    current = await state.get_state()

    if current in (WaterOrder.bottles.state, None):
        # Parse everything from voice — qty, product, date
        parsed = parse_water_command(text)
        product = parsed["product"]

        await _close_session(message.from_user.id)
        await state.clear()
        await state.set_state(WaterOrder.bottles)
        await state.update_data(
            qty=parsed["qty"],
            product_key=parsed["product_key"],
            voice_date=parsed["date"],
        )

        date_hint = f" на {parsed['date']}" if parsed["date"] else ""
        await message.answer(
            f"💧 «{product['name']}» × {parsed['qty']} шт.{date_hint} — понял. Ищу даты…",
            parse_mode="HTML",
        )
        await _launch_and_get_slots(message, state)

    elif current == WaterOrder.choosing_slot.state:
        await message.answer("Выбери вариант из списка выше 👆")


# ── Telegram handlers ──────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я твой персональный помощник.\n\n"
        "Доступные команды:\n"
        "• /water — заказать воду\n"
        "• или скажи голосом: «Закажи воду 2 бутылки»"
    )


@dp.message(Command("water"))
async def cmd_water(message: Message, state: FSMContext):
    await _do_start_water(message, state)


@dp.message(F.text.lower().regexp(r"закажи\s+воду|заказать\s+воду|нужна\s+вода|закажи\s+\d"))
async def water_nlp(message: Message, state: FSMContext):
    parsed = parse_water_command(message.text)
    await _close_session(message.from_user.id)
    await state.clear()
    await state.set_state(WaterOrder.bottles)

    product = parsed["product"]
    date_hint = f" на {parsed['date']}" if parsed["date"] else ""
    await state.update_data(qty=parsed["qty"], product_key=parsed["product_key"], voice_date=parsed["date"])

    await message.answer(
        f"💧 «{product['name']}» × {parsed['qty']} шт.{date_hint} — понял. Ищу даты…",
        parse_mode="HTML",
    )
    await _launch_and_get_slots(message, state)


@dp.message(F.voice)
async def handle_voice(message: Message, state: FSMContext):
    await _handle_voice_input(message, state)


@dp.message(WaterOrder.bottles)
async def handle_bottles(message: Message, state: FSMContext):
    await _do_set_bottles(message, state, message.text.strip())


# ── callbacks ──────────────────────────────────────────────────────────────

@dp.callback_query(WaterOrder.choosing_slot, F.data.startswith("slot:"))
async def handle_slot(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    slots: list[dict] = data.get("slots", [])

    if idx >= len(slots):
        await callback.answer("Устаревшая кнопка, начни заново /water")
        return

    chosen  = slots[idx]
    user_id = callback.from_user.id
    await callback.answer()
    await callback.message.edit_text(f"⏳ Выбираю {chosen['label']}…")

    orderer = _sessions.get(user_id)
    if not orderer:
        await callback.message.edit_text("❌ Сессия истекла. Начни заново: /water")
        await state.clear()
        return

    try:
        summary = await orderer.select_slot(chosen["date_str"], chosen["time"])
        await state.update_data(summary=summary)
        await state.set_state(WaterOrder.confirm)

        product_key  = data.get("product_key", DEFAULT_PRODUCT)
        product_name = CATALOG[product_key]["name"]

        await callback.message.edit_text(
            f"📋 <b>Подтверди заказ:</b>\n\n"
            f"🛒 {product_name} × {summary['qty']} шт.\n"
            f"📅 {summary['date_str']}\n"
            f"🕐 {summary['time']}\n"
            f"💰 Итого: {summary['price']}\n",
            parse_mode="HTML",
            reply_markup=_confirm_keyboard(),
        )
    except Exception as e:
        log.exception("Ошибка при выборе слота")
        await callback.message.edit_text(f"❌ Ошибка: {e}")
        await _close_session(user_id)
        await state.clear()


@dp.callback_query(WaterOrder.confirm, F.data == "back_to_slots")
async def handle_back_to_slots(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    slots = data.get("slots", [])
    product_key = data.get("product_key", DEFAULT_PRODUCT)
    product_name = CATALOG[product_key]["name"]
    qty = data.get("qty", 1)

    if not slots:
        await callback.message.edit_text("❌ Слоты не найдены. Начни заново: /water")
        await state.clear()
        return

    await state.set_state(WaterOrder.choosing_slot)
    await callback.message.edit_text(
        f"🛒 <b>«{product_name}» × {qty} шт.</b>\n\n"
        "📅 Выбери дату и время доставки:",
        parse_mode="HTML",
        reply_markup=_slots_keyboard(slots),
    )


@dp.callback_query(WaterOrder.confirm, F.data == "confirm")
async def handle_confirm(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await callback.answer()
    await callback.message.edit_text("⏳ Оформляю заказ…")

    orderer = _sessions.get(user_id)
    if not orderer:
        await callback.message.edit_text("❌ Сессия истекла. Начни заново: /water")
        await state.clear()
        return

    try:
        ok = await orderer.confirm_order()
        if ok:
            await callback.message.edit_text(
                "✅ <b>Заказ оформлен!</b>\n\nВода едет к тебе 💧",
                parse_mode="HTML",
            )
            shot_path = os.path.join(_TMP, "water_order_result.png")
            if os.path.exists(shot_path):
                try:
                    await callback.message.answer_photo(
                        FSInputFile(shot_path),
                        caption="📸 Страница подтверждения заказа",
                    )
                except Exception:
                    log.exception("Не удалось отправить скриншот подтверждения")
        else:
            await callback.message.edit_text(
                "⚠️ Не нашёл кнопку подтверждения.\n"
                f"Скриншот: {os.path.join(_TMP, 'water_confirm_button_not_found.png')}\n"
                "Проверь корзину на сайте вручную."
            )
    except Exception as e:
        log.exception("Ошибка при подтверждении заказа")
        await callback.message.edit_text(f"❌ Ошибка: {e}")
    finally:
        await _close_session(user_id)
        await state.clear()


@dp.callback_query(F.data == "cancel")
async def handle_cancel(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await callback.answer()
    await _close_session(user_id)
    await state.clear()
    await callback.message.edit_text("❌ Заказ отменён.")


# ── run ────────────────────────────────────────────────────────────────────

async def main():
    log.info("Bot starting…")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
