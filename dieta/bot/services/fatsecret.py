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

    # Find "100g" serving or first one
    serving = next(
        (s for s in servings if "100" in s.get("serving_description", "")),
        servings[0] if servings else None,
    )
    if not serving:
        return None

    try:
        return {
            "food_name": food.get("food_name", ""),
            "serving_description": serving.get("serving_description", ""),
            "weight_g": float(serving.get("metric_serving_amount", 100)),
            "kcal": float(serving.get("calories", 0)),
            "protein": float(serving.get("protein", 0)),
            "fat": float(serving.get("fat", 0)),
            "carbs": float(serving.get("carbohydrate", 0)),
        }
    except (ValueError, TypeError):
        return None


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
