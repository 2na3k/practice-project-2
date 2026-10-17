from __future__ import annotations

from collections.abc import Iterable, Mapping

from .schemas import Ingredient


class Recipe:
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
