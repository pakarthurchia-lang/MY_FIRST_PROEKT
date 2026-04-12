"""Weekly stats — /stats and 📈 Статистика button."""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from db import database

router = Router()

DAYS_RU = {
    "Mon": "Пн", "Tue": "Вт", "Wed": "Ср",
    "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс",
}


def _weekday_ru(iso_date: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso_date)
    return DAYS_RU.get(d.strftime("%a"), d.strftime("%a"))


async def _send_stats(message: Message) -> None:
    user_id = message.from_user.id
    rows = await database.get_week_stats(user_id)
    goals = await database.get_user_goals(user_id)

    if not rows:
        await message.answer("Данных пока нет. Начни вести дневник!")
        return

    def bar(v, g, w=8):
        filled = min(int(round(v / g * w)), w) if g else 0
        return "█" * filled + "░" * (w - filled)

    lines = ["<b>Статистика за 7 дней</b>\n"]
    total_kcal = 0
    for row in rows:
        day = _weekday_ru(row["entry_date"])
        kcal = row["kcal"]
        total_kcal += kcal
        pct = int(kcal / goals["goal_kcal"] * 100) if goals["goal_kcal"] else 0
        lines.append(
            f"<b>{day} {row['entry_date'][5:]}</b>  {kcal:.0f} ккал  "
            f"{bar(kcal, goals['goal_kcal'])} {pct}%\n"
            f"  Б:{row['protein']:.0f}г  Ж:{row['fat']:.0f}г  У:{row['carbs']:.0f}г"
        )

    avg_kcal = total_kcal / len(rows)
    lines.append(f"\n<b>Среднее за период:</b> {avg_kcal:.0f} ккал/день")
    lines.append(f"<b>Цель:</b> {goals['goal_kcal']} ккал/день")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    await _send_stats(message)


@router.message(F.text == "📈 Статистика")
async def btn_stats(message: Message) -> None:
    await _send_stats(message)
