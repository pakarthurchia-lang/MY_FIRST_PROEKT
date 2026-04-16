"""
Nutrition lookup: FatSecret first, Claude as fallback.

FatSecret gives exact data for known/packaged products.
Claude handles generic foods, cooking variations, colloquial names.
"""
from __future__ import annotations
import asyncio
import json
import re
from anthropic import AsyncAnthropic
import config
from bot.services import fatsecret

_client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

# ── Claude prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — эксперт по питанию. Пользователь описывает что он съел (голосом или текстом, возможны ошибки распознавания).

Твоя задача:
1. Определить конкретный продукт или блюдо.
2. Определить вес в граммах. Если не указан — используй типичную порцию.
3. Рассчитать КБЖУ на 100г продукта (используй официальные таблицы состава продуктов).
4. Рассчитать итоговый КБЖУ для указанного веса.

Верни ТОЛЬКО валидный JSON без markdown, без пояснений:
{
  "food_name": "Название продукта (по-русски, точно)",
  "weight_g": 200,
  "per_100g": {
    "kcal": 116,
    "protein": 2.2,
    "fat": 0.4,
    "carbs": 25.0
  },
  "total": {
    "kcal": 232,
    "protein": 4.4,
    "fat": 0.8,
    "carbs": 50.0
  }
}

Правила:
- ВСЕГДА возвращай food_name — даже если продукт назван кратко («рис», «яйцо», «кофе»).
- food_name = null ТОЛЬКО если текст явно не про еду (например, случайный шум или несвязный текст).
- Учитывай способ приготовления если указан: вареный, жареный, сырой, запечённый.
- По умолчанию считай продукт в варёном/готовом виде.
- Числа округляй до 1 знака после запятой.
"""

EXTRACT_PROMPT = """Из текста пользователя извлеки название продукта и вес для поиска в базе FatSecret.
Верни ТОЛЬКО JSON:
{"query": "english name for search", "weight_g": 100, "food_name_ru": "точное русское название"}

Правила для food_name_ru:
- Сохраняй марку/бренд если указана: "Хлеб Аютинский цельнозерновой", "Творог Простоквашино 5%"
- Сохраняй способ приготовления: "Гречка варёная", "Куриная грудка запечённая"
- Сохраняй жирность: "Кефир 3.2%", "Творог 0%"
- Если бренд не указан — пиши стандартное название: "Хлеб пшеничный", "Гречка варёная"

Примеры:
- "творог простоквашино 5% 200г" → {"query": "cottage cheese 5%", "weight_g": 200, "food_name_ru": "Творог Простоквашино 5%"}
- "вареная куриная грудка 150 грамм" → {"query": "chicken breast boiled", "weight_g": 150, "food_name_ru": "Куриная грудка варёная"}
- "гречка 300г" → {"query": "buckwheat cooked", "weight_g": 300, "food_name_ru": "Гречка варёная"}
- "хлеб аютинский 50г" → {"query": "bread", "weight_g": 50, "food_name_ru": "Хлеб Аютинский"}
- "кефир 3.2% 200мл" → {"query": "kefir 3.2%", "weight_g": 200, "food_name_ru": "Кефир 3.2%"}
"""

SPLIT_PROMPT = """Из фразы пользователя извлеки список отдельных продуктов или блюд.
Верни ТОЛЬКО JSON-массив строк, каждая строка — один продукт с количеством/весом.
Если продукт один — массив из одного элемента.

Примеры:
- "съел 3 жареных яйца и хлеб 50 грамм" → ["3 жареных яйца", "хлеб 50 грамм"]
- "гречка 200г с куриной грудкой 150г" → ["гречка варёная 200г", "куриная грудка 150г"]
- "выпил кефир 200мл и съел творог 100г" → ["кефир 200мл", "творог 100г"]
- "яблоко" → ["яблоко"]
- "овсянка на молоке 300г" → ["овсянка на молоке 300г"]

Важно: сохраняй способ приготовления и количество из исходного текста.
"""


# ── Main entry point ───────────────────────────────────────────────────────────

async def parse_food(user_text: str):
    """
    Parse food description and return nutrition dict, or None if not recognized.

    Tries FatSecret first, falls back to Claude.

    Returns:
        {
            "food_name": str,
            "weight_g": float,
            "source": "fatsecret" | "claude",
            "per_100g": {"kcal", "protein", "fat", "carbs"},
            "total": {"kcal", "protein", "fat", "carbs"},
        }
    """
    # Step 1: Ask Claude to extract search queries + weight
    if config.FATSECRET_CLIENT_ID:
        extracted = await _extract_query(user_text)
        if extracted:
            query_en     = extracted.get("query", "")
            query_ru     = extracted.get("food_name_ru", "")
            weight_g     = float(extracted.get("weight_g", 100))
            food_name_ru = query_ru

            # Step 2: Search FatSecret — русское название первым, английское fallback
            results = []
            if query_ru:
                results = await fatsecret.search_food(query_ru, max_results=3)
            if not results and query_en:
                results = await fatsecret.search_food(query_en, max_results=3)

            if results:
                best = results[0]
                per100_kcal    = best["kcal"]
                per100_protein = best["protein"]
                per100_fat     = best["fat"]
                per100_carbs   = best["carbs"]

                factor = weight_g / 100
                return {
                    "food_name": food_name_ru or best["food_name"],
                    "weight_g": weight_g,
                    "source": "fatsecret",
                    "per_100g": {
                        "kcal": round(per100_kcal, 1),
                        "protein": round(per100_protein, 1),
                        "fat": round(per100_fat, 1),
                        "carbs": round(per100_carbs, 1),
                    },
                    "total": {
                        "kcal": round(per100_kcal * factor, 1),
                        "protein": round(per100_protein * factor, 1),
                        "fat": round(per100_fat * factor, 1),
                        "carbs": round(per100_carbs * factor, 1),
                    },
                }

    # Step 3: Fallback to Claude
    return await _claude_parse(user_text)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _extract_query(user_text: str):
    response = await _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=128,
        system=EXTRACT_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _split_foods(user_text: str) -> list[str]:
    """Use Claude Haiku to split text into individual food descriptions."""
    response = await _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=SPLIT_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        items = json.loads(raw)
        if isinstance(items, list) and items:
            return [str(i).strip() for i in items if str(i).strip()]
    except json.JSONDecodeError:
        pass
    return [user_text]  # fallback: treat whole text as one food


async def parse_multiple_foods(user_text: str) -> list[dict]:
    """
    Parse one or more foods from user text.
    Returns list of nutrition dicts (same format as parse_food).
    """
    food_texts = await _split_foods(user_text)
    # Parse all foods in parallel
    results = await asyncio.gather(*[parse_food(t) for t in food_texts])
    return [r for r in results if r is not None]


async def _claude_parse(user_text: str):
    response = await _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not data.get("food_name"):
        return None

    data["source"] = "claude"
    return data
