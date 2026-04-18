from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

# ── Main menu (reply keyboard) ─────────────────────────────────────────────────

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚡ КБЖУ"),     KeyboardButton(text="📊 Дневник")],
            [KeyboardButton(text="📋 Журнал"),   KeyboardButton(text="📈 Статистика")],
            [KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True,
    )


# ── Meal-type selection ────────────────────────────────────────────────────────

MEAL_TYPES = {
    "breakfast": "Завтрак",
    "lunch": "Обед",
    "dinner": "Ужин",
    "snack": "Перекус",
    "other": "Другое",
}


def meal_type_kb(entry_id_placeholder: str) -> InlineKeyboardMarkup:
    """Inline keyboard to choose meal type after food is confirmed."""
    buttons = [
        InlineKeyboardButton(
            text=label,
            callback_data=f"meal:{meal}:{entry_id_placeholder}",
        )
        for meal, label in MEAL_TYPES.items()
    ]
    # 3 + 2 layout
    return InlineKeyboardMarkup(
        inline_keyboard=[buttons[:3], buttons[3:]]
    )


# ── Food confirmation ──────────────────────────────────────────────────────────

def confirm_food_kb(food_key: str) -> InlineKeyboardMarkup:
    """Shown after food is parsed — before saving."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Добавить", callback_data=f"confirm:{food_key}"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel_food"),
            ]
        ]
    )


# ── Diary entry actions ────────────────────────────────────────────────────────

def diary_entry_kb(entry_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Удалить", callback_data=f"del:{entry_id}")]
        ]
    )


def diary_nav_kb(entry_date: str) -> InlineKeyboardMarkup:
    """Navigation buttons at the bottom of the diary."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Удалить запись", callback_data=f"diary_del_pick:{entry_date}"),
            ]
        ]
    )


# ── Settings ──────────────────────────────────────────────────────────────────

def settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить цели", callback_data="edit_goals")],
        ]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="cancel_settings")]
        ]
    )
