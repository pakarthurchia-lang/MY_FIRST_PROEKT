"""
Telegram-бот «Помощник» — модуль заказа воды.
Запуск: python bot.py
"""

import asyncio
import logging
import os
import sys
import tempfile

_TMP = tempfile.gettempdir()
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import BOT_TOKEN, WATER_LOGIN, WATER_PASSWORD, WATER_CITY, WATER_HEADLESS
from water import WaterOrderer
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
    bottles = State()
    date    = State()
    time    = State()
    confirm = State()


# ── helpers ────────────────────────────────────────────────────────────────

async def _close_session(user_id: int):
    orderer = _sessions.pop(user_id, None)
    if orderer:
        await orderer.close()


def _time_keyboard(slots: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=slot, callback_data=f"t:{i}")]
            for i, slot in enumerate(slots)]
    rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить заказ", callback_data="confirm"),
        InlineKeyboardButton(text="❌ Отменить",          callback_data="cancel"),
    ]])


# ── shared business logic (called by both text and voice handlers) ─────────

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

    await state.update_data(qty=qty)
    await state.set_state(WaterOrder.date)
    await message.answer(
        "📅 На какую дату?\n"
        "Примеры: <b>завтра</b>, <b>послезавтра</b>, <b>12.06</b>, <b>15 июня</b>",
        parse_mode="HTML",
    )


async def _do_set_date(message: Message, state: FSMContext, date_str: str):
    user_id = message.from_user.id
    data = await state.get_data()
    qty = data["qty"]
    product_key = data.get("product_key", DEFAULT_PRODUCT)
    product = CATALOG[product_key]

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

        await status_msg.edit_text("📋 Заполняю данные доставки…")
        slots = await orderer.fill_delivery(date_str)

        if not slots:
            await status_msg.edit_text(
                "⚠️ Не нашёл доступные интервалы доставки.\n"
                "Попробуй другую дату или проверь сайт вручную.\n"
                f"Скриншот: {os.path.join(_TMP, 'water_after_date_pick.png')}"
            )
            await _close_session(user_id)
            await state.clear()
            return

        await state.update_data(date_str=date_str, slots=slots)
        await state.set_state(WaterOrder.time)
        await status_msg.edit_text(
            "🕐 Выбери удобное время доставки:",
            reply_markup=_time_keyboard(slots),
        )

    except Exception as e:
        log.exception("Ошибка при заполнении доставки")
        await status_msg.edit_text(f"❌ Ошибка: {e}")
        await _close_session(user_id)
        await state.clear()


# ── voice: download → transcribe → route ──────────────────────────────────

async def _handle_voice_input(message: Message, state: FSMContext):
    """Download voice file, transcribe, route based on current FSM state."""
    status = await message.answer("🎙 Распознаю…")

    # Download .ogg from Telegram
    file = await bot.get_file(message.voice.file_id)
    tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    tmp.close()
    try:
        await bot.download_file(file.file_path, destination=tmp.name)
        text = await transcribe(tmp.name)
    except Exception as e:
        await status.edit_text(f"❌ Не удалось распознать голос: {e}")
        return
    finally:
        os.unlink(tmp.name)

    await status.edit_text(f"🎙 «{text}»")

    current = await state.get_state()

    if current == WaterOrder.bottles.state:
        # Expecting bottle count
        parsed = parse_water_command(text)
        await _do_set_bottles(message, state, str(parsed["qty"]))

    elif current == WaterOrder.date.state:
        # Expecting a date — use raw transcribed text as the date string
        parsed = parse_water_command(text)
        date_str = parsed["date"] or text.strip()
        await _do_set_date(message, state, date_str)

    else:
        # No active order — parse the full command
        parsed = parse_water_command(text)
        await _close_session(message.from_user.id)
        await state.clear()
        await state.set_state(WaterOrder.bottles)

        product = parsed["product"]
        base_data = {"qty": parsed["qty"], "product_key": parsed["product_key"]}

        if parsed["date"]:
            # Have qty, product, and date → skip questions
            await state.update_data(**base_data)
            await message.answer(
                f"💧 «{product['name']}» × {parsed['qty']} шт., "
                f"доставка: <b>{parsed['date']}</b>",
                parse_mode="HTML",
            )
            await _do_set_date(message, state, parsed["date"])
        else:
            # Have qty and product, need date
            await state.update_data(**base_data)
            await state.set_state(WaterOrder.date)
            await message.answer(
                f"💧 «{product['name']}» × {parsed['qty']} шт. — понял.\n"
                "📅 На какую дату?\n"
                "Примеры: <b>завтра</b>, <b>послезавтра</b>, <b>15 июня</b>",
                parse_mode="HTML",
            )


# ── Telegram handlers ──────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я твой персональный помощник.\n\n"
        "Доступные команды:\n"
        "• /water — заказать воду\n"
        "• или скажи голосом: «Закажи воду 2 бутылки завтра»"
    )


@dp.message(Command("water"))
async def cmd_water(message: Message, state: FSMContext):
    await _do_start_water(message, state)


@dp.message(F.text.lower().regexp(r"закажи\s+воду|заказать\s+воду|нужна\s+вода|закажи\s+\d"))
async def water_nlp(message: Message, state: FSMContext):
    parsed = parse_water_command(message.text)
    await _close_session(message.from_user.id)
    await state.clear()

    product = parsed["product"]
    base_data = {"qty": parsed["qty"], "product_key": parsed["product_key"]}

    if parsed["date"]:
        await state.set_state(WaterOrder.bottles)
        await state.update_data(**base_data)
        await message.answer(
            f"💧 «{product['name']}» × {parsed['qty']} шт., "
            f"доставка: <b>{parsed['date']}</b>",
            parse_mode="HTML",
        )
        await _do_set_date(message, state, parsed["date"])
    else:
        await state.set_state(WaterOrder.date)
        await state.update_data(**base_data)
        await message.answer(
            f"💧 «{product['name']}» × {parsed['qty']} шт. — понял.\n"
            "📅 На какую дату? (завтра / послезавтра / 15 июня)",
            parse_mode="HTML",
        )


# Voice — works in any state
@dp.message(F.voice)
async def handle_voice(message: Message, state: FSMContext):
    await _handle_voice_input(message, state)


# Text fallbacks for each FSM state
@dp.message(WaterOrder.bottles)
async def handle_bottles(message: Message, state: FSMContext):
    await _do_set_bottles(message, state, message.text.strip())


@dp.message(WaterOrder.date)
async def handle_date(message: Message, state: FSMContext):
    await _do_set_date(message, state, message.text.strip())


# ── callbacks ──────────────────────────────────────────────────────────────

@dp.callback_query(WaterOrder.time, F.data.startswith("t:"))
async def handle_time(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    slots: list[str] = data["slots"]

    if idx >= len(slots):
        await callback.answer("Устаревшая кнопка, начни заново /water")
        return

    chosen = slots[idx]
    user_id = callback.from_user.id
    await callback.answer()
    await callback.message.edit_text(f"⏳ Выбираю время {chosen}…")

    orderer = _sessions.get(user_id)
    if not orderer:
        await callback.message.edit_text("❌ Сессия истекла. Начни заново: /water")
        await state.clear()
        return

    try:
        summary = await orderer.select_time(chosen)
        await state.update_data(summary=summary)
        await state.update_data(summary=summary)
        await state.set_state(WaterOrder.confirm)

        product_key = data.get("product_key", DEFAULT_PRODUCT)
        product_name = CATALOG[product_key]["name"]

        await callback.message.edit_text(
            f"📋 <b>Подтверди заказ:</b>\n\n"
            f"🛒 {product_name} × {summary['qty']} шт.\n"
            f"📅 Дата: {summary['date']}\n"
            f"🕐 Время: {summary['time']}\n"
            f"💰 Итого: {summary['price']}\n",
            parse_mode="HTML",
            reply_markup=_confirm_keyboard(),
        )
    except Exception as e:
        log.exception("Ошибка при выборе времени")
        await callback.message.edit_text(f"❌ Ошибка: {e}")
        await _close_session(user_id)
        await state.clear()


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
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    # Playwright requires ProactorEventLoop on Windows
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
