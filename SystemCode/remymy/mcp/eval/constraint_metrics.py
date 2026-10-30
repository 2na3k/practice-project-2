"""Independent post-hoc checks for output constraint violations."""

from __future__ import annotations


# Kept independent from features.screens so these checks can detect filter regressions.
_MEAT = ("beef", "bison", "veal", "pork", "bacon", "ham", "prosciutto", "lard", "tallow", "chicken", "turkey", "duck", "lamb", "mutton", "venison", "sausage", "meat")
_SEAFOOD = ("fish", "salmon", "tuna", "anchovy", "sardine", "cod", "shrimp", "prawn", "shellfish", "crab", "lobster", "oyster", "mussel", "clam", "scallop", "squid", "octopus")
_DAIRY_EGG = ("milk", "cheese", "whey", "casein", "butter", "ghee", "cream", "yogurt", "yoghurt", "egg", "eggs", "albumin", "mayonnaise")
_HALAL_BLOCKED = ("pork", "bacon", "ham", "prosciutto", "lard", "wine", "beer", "rum", "brandy", "cognac", "bourbon", "whiskey", "vodka", "gin", "tequila", "sherry", "port", "vermouth", "liqueur", "sake", "mirin", "alcohol", "gelatin", "collagen", "blood", "animal rennet")


def _violates(request: dict, candidate: dict) -> bool:
    preferences = request.get("preferences", request)
    diet = str(preferences.get("diet", "any")).lower()
    ingredients = [str(item).lower() for item in candidate.get("ingredients", [])]
    text = " | ".join(ingredients)
    if diet == "vegan" and any(term in text for term in (*_MEAT, *_SEAFOOD, *_DAIRY_EGG, "honey", "gelatin", "collagen")):
        return True
    if diet == "vegetarian" and any(term in text for term in (*_MEAT, *_SEAFOOD)):
        return True
    if diet == "halal" and any(term in text for term in _HALAL_BLOCKED):
        return True
    if candidate.get("minutes") is not None and candidate["minutes"] > preferences.get("maxMinutes", float("inf")):
        return True
    exclusions = preferences.get("exclude", [])
    if any(str(term).strip().lower() in text for term in exclusions if str(term).strip()):
        return True
    return False


def constraint_metrics(request: dict, candidates: list[dict], selected_ids: list[str]) -> dict[str, float | None]:
    by_id = {str(item.get("recipe_id", item.get("id"))): item for item in candidates}
    selected = [by_id[str(recipe_id)] for recipe_id in selected_ids if str(recipe_id) in by_id]
    violation_rate = (sum(_violates(request, item) for item in selected) / len(selected)) if selected else None
    limit = request.get("limit", 0)
    return {
        "violation_rate": violation_rate,
        "over_filtering_rate": float(len(selected_ids) < limit),
    }
