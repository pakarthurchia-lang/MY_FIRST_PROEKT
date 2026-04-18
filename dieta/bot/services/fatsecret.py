"""
FatSecret Platform API v2 (OAuth 2.0 client_credentials).
Используется для поиска КБЖУ по названию продукта.
"""
from __future__ import annotations
import time
import aiohttp
import config

_token: dict = {"access_token": "", "expires_at": 0}

TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
API_URL   = "https://platform.fatsecret.com/rest/server.api"


async def _get_token() -> str:
    """Get or refresh OAuth2 access token."""
    if _token["access_token"] and time.time() < _token["expires_at"] - 60:
        return _token["access_token"]

    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "scope": "basic",
            },
            auth=aiohttp.BasicAuth(
                config.FATSECRET_CLIENT_ID,
                config.FATSECRET_CLIENT_SECRET,
            ),
        ) as resp:
            data = await resp.json()
            _token["access_token"] = data["access_token"]
            _token["expires_at"] = time.time() + data.get("expires_in", 86400)
            return _token["access_token"]


async def search_food(query: str, max_results: int = 5) -> list[dict]:
    """
    Search FatSecret for a food by name.
    Returns list of dicts with keys: food_name, kcal, protein, fat, carbs, serving_description.
    """
    if not config.FATSECRET_CLIENT_ID:
        return []

    token = await _get_token()

    params = {
        "method": "foods.search",
        "search_expression": query,
        "format": "json",
        "max_results": max_results,
        "language": "ru",
        "region": "RU",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            API_URL,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            data = await resp.json()

    foods_data = data.get("foods", {}).get("food", [])
    if isinstance(foods_data, dict):
        foods_data = [foods_data]

    results = []
    for food in foods_data:
        desc = food.get("food_description", "")
        # desc looks like: "Per 100g - Calories: 116kcal | Fat: 0.37g | Carbs: 25.08g | Protein: 2.69g"
        parsed = _parse_description(desc)
        if parsed:
            results.append({
                "food_id": food.get("food_id"),
                "food_name": food.get("food_name", ""),
                "serving_description": desc,
                **parsed,
            })

    return results


async def get_food_by_id(food_id: str) -> dict | None:
    """Get detailed nutrition for a specific food_id."""
    if not config.FATSECRET_CLIENT_ID:
        return None

    token = await _get_token()

    params = {
        "method": "food.get.v2",
        "food_id": food_id,
        "format": "json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            API_URL,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            data = await resp.json()

    food = data.get("food", {})
    servings = food.get("servings", {}).get("serving", [])
    if isinstance(servings, dict):
        servings = [servings]

    if not servings:
        return None

    # Prefer 100g serving for accurate normalization; fall back to first serving
    base = next(
        (s for s in servings if "100" in s.get("serving_description", "")),
        servings[0],
    )
    # Producer's typical portion (first serving that is NOT the 100g reference)
    producer = next(
        (s for s in servings if s is not base and "100" not in s.get("serving_description", "")),
        None,
    )

    try:
        base_weight = float(base.get("metric_serving_amount", 100)) or 100
        factor = 100.0 / base_weight  # normalize everything to per-100g

        producer_g: float | None = None
        if producer:
            try:
                v = float(producer.get("metric_serving_amount", 0))
                if v > 0:
                    producer_g = v
            except (ValueError, TypeError):
                pass

        return {
            "food_name": food.get("food_name", ""),
            "serving_description": base.get("serving_description", ""),
            "producer_serving_g": producer_g,
            "weight_g": 100,
            "kcal":    round(float(base.get("calories", 0))      * factor, 1),
            "protein": round(float(base.get("protein", 0))       * factor, 1),
            "fat":     round(float(base.get("fat", 0))           * factor, 1),
            "carbs":   round(float(base.get("carbohydrate", 0))  * factor, 1),
        }
    except (ValueError, TypeError):
        return None


async def find_by_barcode(barcode: str) -> dict | None:
    """
    Look up product by barcode via FatSecret.
    Returns nutrition per 100g or None if not found.
    """
    if not config.FATSECRET_CLIENT_ID:
        return None

    token = await _get_token()

    # Step 1: barcode → food_id
    async with aiohttp.ClientSession() as session:
        async with session.get(
            API_URL,
            params={
                "method": "food.find_id_for_barcode",
                "barcode": barcode,
                "format": "json",
            },
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            data = await resp.json()

    food_id = data.get("food_id", {}).get("value")
    if not food_id:
        return None

    # Step 2: food_id → nutrition
    return await get_food_by_id(food_id)


def _parse_description(desc: str) -> dict | None:
    """Parse 'Per 100g - Calories: 116kcal | Fat: 0.37g | Carbs: 25.08g | Protein: 2.69g'"""
    import re
    try:
        kcal    = float(re.search(r"Calories:\s*([\d.]+)", desc).group(1))
        fat     = float(re.search(r"Fat:\s*([\d.]+)", desc).group(1))
        carbs   = float(re.search(r"Carbs:\s*([\d.]+)", desc).group(1))
        protein = float(re.search(r"Protein:\s*([\d.]+)", desc).group(1))
        return {"kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs}
    except (AttributeError, ValueError):
        return None
