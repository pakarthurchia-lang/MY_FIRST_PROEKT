from __future__ import annotations
"""
Detect user intent from voice/text + today's diary context.
Uses Claude Haiku for speed.
"""
import json
import re
from anthropic import AsyncAnthropic
import config

_client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

MEAL_LABELS = {
    "breakfast": "Завтрак",
    "lunch":     "Обед",
    "dinner":    "Ужин",
    "snack":     "Перекус",
    "other":     "Другое",
}

SYSTEM_PROMPT = """Ты — ассистент дневника питания. Тебе дан список записей пользователя за сегодня и его фраза (возможно транскрибированная с голоса).

Определи намерение и верни ТОЛЬКО валидный JSON.

Если пользователь хочет ДОБАВИТЬ еду (новый приём пищи):
{"intent": "add_food"}

Если хочет УДАЛИТЬ запись (слова: удали, убери, стёр, удалить):
{"intent": "delete", "entry_id": <число>}

Если хочет ИЗМЕНИТЬ ВЕС (слова: измени вес, поменяй граммы, было/стало, не X а Y грамм):
{"intent": "edit_weight", "entry_id": <число>, "new_weight_g": <число>}

Если хочет ИЗМЕНИТЬ ПРИЁМ ПИЩИ (слова: перенеси на, это был завтрак/обед/ужин/перекус):
{"intent": "edit_meal", "entry_id": <число>, "new_meal_type": "breakfast"|"lunch"|"dinner"|"snack"|"other"}

Если хочет ПЕРЕИМЕНОВАТЬ запись:
{"intent": "edit_name", "entry_id": <число>, "new_name": "новое название"}

Если хочет УЗНАТЬ текущее КБЖУ / сколько съел / сколько осталось (слова: сколько калорий, покажи кбжу, что съел, сколько осталось, мои макросы, прогресс):
{"intent": "show_kbju"}

Если хочет НАЧАТЬ ДЕНЬ (слова: начать день, начинаю день, старт, начало дня):
{"intent": "start_day"}

Если хочет ЗАКРЫТЬ ДЕНЬ (слова: закрыть день, завершить день, конец дня, закончить день):
{"intent": "close_day"}

Если хочет посмотреть ЖУРНАЛ / историю дней (слова: журнал, история, прошлые дни, покажи историю):
{"intent": "show_journal"}

Если непонятно или дневник пуст при попытке редактировать:
{"intent": "unknown"}

Правила:
- entry_id бери строго из списка дневника.
- Если упоминается «последнее», «последнюю» — бери запись с наибольшим ID.
- Если пользователь называет продукт неточно — найди ближайшее совпадение по смыслу.
- Верни ТОЛЬКО JSON, без пояснений.
"""


def _format_diary(entries: list[dict]) -> str:
    if not entries:
        return "Дневник пуст."
    lines = []
    for e in entries:
        meal = MEAL_LABELS.get(e["meal_type"], e["meal_type"])
        lines.append(f"[ID:{e['id']}] {e['food_name']} {e['weight_g']:.0f}г — {meal}")
    return "\n".join(lines)


async def detect_intent(user_text: str, diary_entries: list[dict]) -> dict:
    """
    Returns one of:
      {"intent": "add_food"}
      {"intent": "delete",      "entry_id": int}
      {"intent": "edit_weight", "entry_id": int, "new_weight_g": float}
      {"intent": "edit_meal",   "entry_id": int, "new_meal_type": str}
      {"intent": "edit_name",   "entry_id": int, "new_name": str}
      {"intent": "unknown"}
    """
    diary_context = _format_diary(diary_entries)

    prompt = (
        f"Дневник пользователя на сегодня:\n{diary_context}\n\n"
        f"Пользователь сказал: «{user_text}»"
    )

    response = await _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=128,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
        return result if "intent" in result else {"intent": "unknown"}
    except json.JSONDecodeError:
        return {"intent": "unknown"}
