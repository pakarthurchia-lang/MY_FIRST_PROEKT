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

SYSTEM_PROMPT = """Ты — ассистент дневника питания. Тебе дан дневник пользователя за сегодня и его фраза (возможно транскрибированная с голоса — могут быть опечатки и разговорный стиль).

Верни ТОЛЬКО валидный JSON, без пояснений и markdown.

━━━ НАМЕРЕНИЯ ━━━

ДОБАВИТЬ новую еду:
{"intent": "add_food"}
Признаки: называет продукт без ссылки на существующую запись.
Примеры: «съел рис 200 грамм», «добавь яблоко», «выпил кефир».

УДАЛИТЬ запись из дневника:
{"intent": "delete", "entry_id": <int>}
Признаки: слова «удали», «убери», «удалить», «стёр», «лишнее», «не то» + название продукта.
Примеры: «удали рис», «убери последнее», «стёр яйцо», «удали обед».

УДАЛИТЬ ВСЕ записи за сегодня:
{"intent": "delete_all"}
Признаки: «удали все», «очисти дневник», «сброс», «удалить всё», «стёр всё за сегодня».
Примеры: «удали все записи», «очисти дневник», «удали всё за сегодня», «сброс дня».

ИЗМЕНИТЬ ВЕС существующей записи:
{"intent": "edit_weight", "entry_id": <int>, "new_weight_g": <float>}
Признаки: «не X а Y», «было X стало Y», «измени на», «поменяй граммы», «перевес».
Примеры: «рис было не 200 а 150», «измени курицу на 180 грамм», «яйцо 55 а не 60».

ИЗМЕНИТЬ ПРИЁМ ПИЩИ существующей записи:
{"intent": "edit_meal", "entry_id": <int>, "new_meal_type": "breakfast"|"lunch"|"dinner"|"snack"|"other"}
Признаки: «перенеси на», «это был», «отнеси к», «не ужин а», название приёма пищи.
Примеры: «рис перенеси на завтрак», «это был обед», «курица — перекус», «не ужин а обед».
Маппинг: завтрак=breakfast, обед=lunch, ужин=dinner, перекус=snack, другое/прочее=other.

ПЕРЕИМЕНОВАТЬ существующую запись:
{"intent": "edit_name", "entry_id": <int>, "new_name": "<строка>"}
Признаки: «переименуй», «назови», «называй это».
Примеры: «переименуй рис в бурый рис», «назови это "греческий салат"».

ПОКАЗАТЬ КБЖУ / прогресс дня:
{"intent": "show_kbju"}
Примеры: «сколько калорий», «покажи кбжу», «что съел сегодня», «сколько осталось», «мои макросы», «прогресс».

НАЧАТЬ ДЕНЬ:
{"intent": "start_day"}
Примеры: «начать день», «старт», «начинаю», «начало дня».

ЗАКРЫТЬ ДЕНЬ:
{"intent": "close_day"}
Примеры: «закрыть день», «завершить день», «конец дня», «всё на сегодня».

ЖУРНАЛ / история дней:
{"intent": "show_journal"}
Примеры: «журнал», «история», «прошлые дни», «покажи историю».

НЕПОНЯТНО (fallback):
{"intent": "unknown"}

━━━ ПРАВИЛА ━━━
1. entry_id ВСЕГДА из дневника (поле ID) — не придумывай.
2. «последнее» / «последнюю» / «только что» = запись с НАИБОЛЬШИМ ID.
3. Продукт сопоставляй нечётко: «рис» = «Вареный рис», «курица» = «Куриная грудка», «яйцо» = «Яйцо жареное».
4. Если дневник пуст и пользователь хочет редактировать/удалять → {"intent": "unknown"}.
5. Если фраза явно похожа на команду редактирования/удаления, но продукт не найден → {"intent": "unknown"}.
6. Возвращай ТОЛЬКО JSON.
"""


def _format_diary(entries: list[dict]) -> str:
    if not entries:
        return "Дневник пуст."
    lines = []
    for e in entries:
        meal = MEAL_LABELS.get(e["meal_type"], e["meal_type"])
        lines.append(
            f"[ID:{e['id']}] {e['food_name']} {e['weight_g']:.0f}г "
            f"{e['kcal']:.0f}ккал — {meal}"
        )
    return "\n".join(lines)


def format_diary_readable(entries: list[dict]) -> str:
    """Human-readable diary for error messages in bot."""
    if not entries:
        return "Дневник пуст."
    lines = []
    for e in entries:
        meal = MEAL_LABELS.get(e["meal_type"], e["meal_type"])
        lines.append(f"• {e['food_name']} {e['weight_g']:.0f}г — {meal}")
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
