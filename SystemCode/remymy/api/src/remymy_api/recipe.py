from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from .schemas import Ingredient


class DigestedRecipe:
    """A recipe as the recommender reads it: the priced catalog foods its ingredient lines matched, and its nutrient
    values, the mean of theirs per 100 g. RecipeRepository.digest makes one from a recipe's ingredient lines."""

    def __init__(
        self,
        recipe_id: str,
        ingredients: Iterable[Ingredient],
        nutrient_values: Mapping[str, float] | None = None,
    ) -> None:
        self.recipe_id = recipe_id
        # copies, so a list or dict the caller keeps changing does not change the recipe
        self.ingredients = list(ingredients)
        self.nutrient_values = dict(nutrient_values or {})

    def to_dict(self) -> dict[str, Any]:
        """The recipe as plain JSON-ready values: {"recipe_id", "ingredients": [{"fdc_id", "name", "price",
        "nutrient_values"}, ...], "nutrient_values"}."""
        return {
            "recipe_id": self.recipe_id,
            "ingredients": [ingredient.model_dump() for ingredient in self.ingredients],
            "nutrient_values": dict(self.nutrient_values),
        }

    def to_json(self, **kwargs: Any) -> str:
        """to_dict as a JSON string; kwargs go to json.dumps (e.g. indent=2)."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DigestedRecipe:
        """The recipe to_dict made, back; each ingredient is validated as an Ingredient."""
        return cls(
            data["recipe_id"],
            [Ingredient.model_validate(ingredient) for ingredient in data["ingredients"]],
            data.get("nutrient_values"),
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> DigestedRecipe:
        return cls.from_dict(json.loads(text))
