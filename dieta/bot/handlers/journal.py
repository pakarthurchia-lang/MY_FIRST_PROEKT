from __future__ import annotations
"""
Journal handler:
  - ⚡ КБЖУ  — quick daily progress card
  - 📋 Журнал — list of past days
  - Начать день / Закрыть день — day lifecycle
"""
from datetime import date, datetime

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from db import database

router = Router()

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class JournalForm(StatesGroup):
    close_note = State()   # optional note when closing a day


# ── Helpers ────────────────────────────────────────────────────────────────────

def _bar(val: float, goal: float, width: int = 10) -> str:
    filled = min(int(round(val / goal * width)), width) if goal else 0
    return "█" * filled + "░" * (width - filled)


def _pct(val: float, goal: float) -> int:
    return int(val / goal * 100) if goal else 0


def _fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    wd = WEEKDAYS[d.weekday()]
    return f"{wd} {d.day:02d}.{d.month:02d}"


# ── Quick КБЖУ card ────────────────────────────────────────────────────────────

async def _kbju_card(user_id: int) -> str:
    today   = date.today().isoformat()
    totals  = await database.get_day_totals(user_id, today)
    goals   = await database.get_user_goals(user_id)
    log     = await database.get_day_log(user_id, today)

    now_str = datetime.now().strftime("%H:%M")
    status  = ""
    if log and log.get("closed_at"):
        status = "  🔒 День закрыт"
    elif log and log.get("started_at"):
        started = log["started_at"][11:16]
        status = f"  ▶ Начат в {started}"

    kcal_left = max(goals["goal_kcal"] - totals["kcal"], 0)

    lines = [
        f"⚡ <b>КБЖУ сегодня</b> {now_str}{status}\n",
        f"Калории:  <b>{totals['kcal']:.0f}</b> / {goals['goal_kcal']} ккал  "
        f"{_bar(totals['kcal'], goals['goal_kcal'])} {_pct(totals['kcal'], goals['goal_kcal'])}%",
        f"Белки:    <b>{totals['protein']:.1f}</b> / {goals['goal_protein']} г  "
        f"{_bar(totals['protein'], goals['goal_protein'])} {_pct(totals['protein'], goals['goal_protein'])}%",
        f"Жиры:     <b>{totals['fat']:.1f}</b> / {goals['goal_fat']} г  "
        f"{_bar(totals['fat'], goals['goal_fat'])} {_pct(totals['fat'], goals['goal_fat'])}%",
        f"Углеводы: <b>{totals['carbs']:.1f}</b> / {goals['goal_carbs']} г  "
        f"{_bar(totals['carbs'], goals['goal_carbs'])} {_pct(totals['carbs'], goals['goal_carbs'])}%",
        f"\nДо цели: <b>{kcal_left:.0f} ккал</b>",
    ]
    return "\n".join(lines)


def _kbju_kb(today: str, log: dict | None) -> InlineKeyboardMarkup:
    is_closed  = bool(log and log.get("closed_at"))
    is_started = bool(log and log.get("started_at"))
    rows = []
    if not is_started and not is_closed:
        rows.append([InlineKeyboardButton(text="▶ Начать день", callback_data="day_start")])
    if not is_closed:
        rows.append([InlineKeyboardButton(text="🔒 Закрыть день", callback_data="day_close_ask")])
    rows.append([InlineKeyboardButton(text="📋 Открыть журнал", callback_data="journal_show")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_kbju(message: Message) -> None:
    user_id = message.from_user.id
    today   = date.today().isoformat()
    await database.ensure_user(user_id, message.from_user.username)
    log  = await database.get_day_log(user_id, today)
    text = await _kbju_card(user_id)
    await message.answer(text, parse_mode="HTML", reply_markup=_kbju_kb(today, log))


@router.message(F.text == "⚡ КБЖУ")
async def btn_kbju(message: Message) -> None:
    await _send_kbju(message)


@router.message(Command("kbju"))
async def cmd_kbju(message: Message) -> None:
    await _send_kbju(message)


# ── Start day ──────────────────────────────────────────────────────────────────

async def _start_day(user_id: int) -> str:
    today = date.today().isoformat()
    log   = await database.get_day_log(user_id, today)
    if log and log.get("started_at"):
        started = log["started_at"][11:16]
        return f"День уже начат в {started}."
    await database.day_start(user_id, today)
    return f"▶ День начат в {datetime.now().strftime('%H:%M')}."


@router.callback_query(F.data == "day_start")
async def cb_day_start(callback: CallbackQuery) -> None:
    msg = await _start_day(callback.from_user.id)
    await callback.answer(msg, show_alert=False)
    # Refresh КБЖУ card
    today = date.today().isoformat()
    log   = await database.get_day_log(callback.from_user.id, today)
    text  = await _kbju_card(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_kbju_kb(today, log))


# ── Close day ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "day_close_ask")
async def cb_day_close_ask(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(JournalForm.close_note)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Закрыть без заметки", callback_data="day_close_now:")],
        [InlineKeyboardButton(text="Отмена", callback_data="day_close_cancel")],
    ])
    await callback.message.edit_text(
        "🔒 <b>Закрыть день?</b>\n\n"
        "Напиши заметку (как себя чувствовал, заметки по питанию) или нажми «Закрыть без заметки».",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(JournalForm.close_note)
async def fsm_close_note(message: Message, state: FSMContext) -> None:
    note = message.text.strip()
    await state.clear()
    await _do_close_day(message.from_user.id, note)
    totals = await database.get_day_totals(message.from_user.id, date.today().isoformat())
    goals  = await database.get_user_goals(message.from_user.id)
    await message.answer(
        _close_summary(totals, goals, note),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("day_close_now:"))
async def cb_day_close_now(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    note = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    await _do_close_day(user_id, note)
    totals = await database.get_day_totals(user_id, date.today().isoformat())
    goals  = await database.get_user_goals(user_id)
    await callback.message.edit_text(
        _close_summary(totals, goals, note),
        parse_mode="HTML",
    )
    await callback.answer("День закрыт!")


@router.callback_query(F.data == "day_close_cancel")
async def cb_day_close_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    today = date.today().isoformat()
    log   = await database.get_day_log(callback.from_user.id, today)
    text  = await _kbju_card(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_kbju_kb(today, log))
    await callback.answer("Отменено.")


async def _do_close_day(user_id: int, note: str = "") -> None:
    today = date.today().isoformat()
    log   = await database.get_day_log(user_id, today)
    if not log or not log.get("started_at"):
        await database.day_start(user_id, today)
    await database.day_close(user_id, today, note)


def _close_summary(totals: dict, goals: dict, note: str) -> str:
    pct_kcal = _pct(totals["kcal"], goals["goal_kcal"])
    verdict  = "отлично" if 85 <= pct_kcal <= 110 else ("маловато" if pct_kcal < 85 else "многовато")
    lines = [
        f"🔒 <b>День закрыт</b>  ({datetime.now().strftime('%H:%M')})\n",
        f"Калории:  <b>{totals['kcal']:.0f}</b> / {goals['goal_kcal']} ккал — {verdict}",
        f"Белки:    <b>{totals['protein']:.1f}</b> г",
        f"Жиры:     <b>{totals['fat']:.1f}</b> г",
        f"Углеводы: <b>{totals['carbs']:.1f}</b> г",
    ]
    if note:
        lines.append(f"\n📝 Заметка: {note}")
    return "\n".join(lines)


# ── Journal list ───────────────────────────────────────────────────────────────

async def _send_journal(target) -> None:
    """target is Message or used for edit_text via CallbackQuery."""
    if isinstance(target, CallbackQuery):
        user_id = target.from_user.id
        send_fn = target.message.edit_text
    else:
        user_id = target.from_user.id
        send_fn = target.answer

    rows  = await database.get_journal(user_id, limit=14)
    goals = await database.get_user_goals(user_id)

    if not rows:
        await send_fn("📋 Журнал пуст — начни вести дневник!", parse_mode="HTML")
        return

    lines = ["📋 <b>Журнал питания</b> (последние 14 дней)\n"]
    for r in rows:
        status = "🔒" if r.get("closed_at") else ("▶" if r.get("started_at") else "·")
        pct    = _pct(r["kcal"], goals["goal_kcal"])
        bar    = _bar(r["kcal"], goals["goal_kcal"], width=8)
        note_icon = " 📝" if r.get("note") else ""
        lines.append(
            f"{status} <b>{_fmt_date(r['entry_date'])}</b>  "
            f"{r['kcal']:.0f} ккал  {bar} {pct}%{note_icon}\n"
            f"   Б:{r['protein']:.0f} Ж:{r['fat']:.0f} У:{r['carbs']:.0f}  "
            f"({r['entries_count']} записей)"
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Текущий день", callback_data="kbju_refresh")]
    ])
    await send_fn("\n".join(lines), parse_mode="HTML", reply_markup=kb)


@router.message(F.text == "📋 Журнал")
async def btn_journal(message: Message) -> None:
    await _send_journal(message)


@router.message(Command("journal"))
async def cmd_journal(message: Message) -> None:
    await _send_journal(message)


@router.callback_query(F.data == "journal_show")
async def cb_journal_show(callback: CallbackQuery) -> None:
    await _send_journal(callback)
    await callback.answer()


@router.callback_query(F.data == "kbju_refresh")
async def cb_kbju_refresh(callback: CallbackQuery) -> None:
    today = date.today().isoformat()
    log   = await database.get_day_log(callback.from_user.id, today)
    text  = await _kbju_card(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_kbju_kb(today, log))
    await callback.answer()


# ── Public helpers for voice routing ──────────────────────────────────────────

async def handle_show_kbju(message: Message) -> None:
    await _send_kbju(message)


async def handle_start_day(message: Message) -> None:
    msg = await _start_day(message.from_user.id)
    today = date.today().isoformat()
    log   = await database.get_day_log(message.from_user.id, today)
    text  = await _kbju_card(message.from_user.id)
    await message.answer(
        f"{msg}\n\n{text}",
        parse_mode="HTML",
        reply_markup=_kbju_kb(today, log),
    )


async def handle_close_day(message: Message, state: FSMContext) -> None:
    await state.set_state(JournalForm.close_note)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Закрыть без заметки", callback_data="day_close_now:")],
        [InlineKeyboardButton(text="Отмена", callback_data="day_close_cancel")],
    ])
    await message.answer(
        "🔒 <b>Закрыть день?</b>\n\n"
        "Напиши заметку или нажми «Закрыть без заметки».",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def handle_show_journal(message: Message) -> None:
    await _send_journal(message)
