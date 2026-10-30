"""Shared, side-effect-free text normalization and dietary ingredient screens."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

MEAT_TERMS = ("beef", "bison", "veal", "pork", "bacon", "ham", "prosciutto", "lard", "tallow", "chicken", "turkey", "duck", "lamb", "mutton", "venison", "sausage", "meat")
SEAFOOD_TERMS = ("fish", "salmon", "tuna", "anchovy", "sardine", "cod", "shrimp", "prawn", "shellfish", "crab", "lobster", "oyster", "mussel", "clam", "scallop", "squid", "octopus", "fish sauce", "oyster sauce")
DAIRY_TERMS = ("milk", "cheese", "whey", "casein", "butter", "ghee", "cream", "yogurt", "yoghurt", "parmesan", "mozzarella", "feta", "cheddar", "rennet")
EGG_TERMS = ("egg", "eggs", "albumin", "mayonnaise")
VEGAN_EXTRA_TERMS = ("honey", "gelatin", "collagen", "bone broth", "chicken broth", "beef broth", "fish broth")
HALAL_BLOCKED_TERMS = ("pork", "bacon", "ham", "prosciutto", "lard", "wine", "beer", "rum", "brandy", "cognac", "bourbon", "whiskey", "vodka", "gin", "tequila", "sherry", "port", "vermouth", "liqueur", "sake", "mirin", "alcohol", "gelatin", "collagen", "blood", "animal rennet")
PLANT_ALTERNATIVE = re.compile(r"\b(?:vegan|plant based|non dairy|dairy free)\b")
PLANT_MILK = re.compile(r"\b(?:coconut|almond|oat|soy|rice|cashew|hemp|pea|hazelnut|macadamia) milk\b")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


@lru_cache(maxsize=1024)
def _term_re(term: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(normalize(term))}\b")


def has_any_term(norm_ings: tuple[str, ...], terms: tuple[str, ...]) -> bool:
    for text in norm_ings:
        if PLANT_ALTERNATIVE.search(text):
            continue
        for term in terms:
            if term == "milk" and PLANT_MILK.search(text):
                continue
            if _term_re(term).search(text):
                return True
    return False
