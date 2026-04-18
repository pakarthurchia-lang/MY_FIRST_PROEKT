from __future__ import annotations
"""
Identify a food product from a photo using Claude Vision (Haiku).
Works with packaging photos, labels, barcodes, or the food itself.
"""
import base64
import json
import re
import io

import config
from anthropic import AsyncAnthropic

_client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

_PROMPT = (
    "Определи продукт питания на фото (упаковка, этикетка, штрихкод или сама еда). "
    "Верни ТОЛЬКО JSON без markdown:\n"
    '{"food_name": "точное название по-русски с брендом и характеристиками (жирность, вкус и т.д.)"}\n'
    'Если на фото нет еды — верни {"food_name": null}'
)


async def identify_food(image_bytes: bytes) -> str | None:
    """Return Russian product name from photo, or None if not food."""
    image_b64 = base64.standard_b64encode(image_bytes).decode()

    response = await _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=128,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": _PROMPT},
            ],
        }],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw).get("food_name")
    except (json.JSONDecodeError, AttributeError):
        return None
