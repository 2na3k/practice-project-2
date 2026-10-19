from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RecipeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    ingredient_count: int
    ingredient_preview: list[str]
    quality_status: str


class RecipeDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    ingredients: list[str]
    instructions: list[str]
    quality_status: str


class RecipeListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RecipeSummary]
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


Diet = Literal["vegetarian", "halal", "Hinduism"]


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    special_diet: Diet | None = None
    cuisines: list[str] = Field(default_factory=list, max_length=40)
    preferred_nutrient: str | None = Field(default=None, max_length=64)


class User(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    username: str = Field(min_length=1)
    preferences: Preferences = Field(default_factory=Preferences)

class UserHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    timestamp: datetime
    like: bool
    recipe_id: str

class HistoryItem(BaseModel):
    """One row of a user's history as a client shows it: what they rated, its title and whether they liked it."""

    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    # "recipe" for one of the recipe database, "food" for a recommended catalog food rated by its fdc_id
    kind: Literal["recipe", "food"]
    # None when its database no longer has it, or is unavailable
    title: str | None
    like: bool
    timestamp: datetime


class Ingredient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fdc_id: int
    name: str
    price: float
    nutrient_values: dict[str, float]


class UserBuyingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    avg_price: float
    avg_nutrient_values: dict[str, float]

class RecommendedRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    score: float


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: UserBuyingProfile | None
    items: list[RecommendedRecipe]


class ItemPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: float
    # the pack size(s), in g / ml, of the listings the price was taken over
    quantity: float | list[float] | None


class CatalogFood(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fdc_id: int
    description: str
    product: list[str]
    item_prices: dict[str, ItemPrice]
    item_unit_prices: dict[str, float]
    item_routes: dict[str, str]
    matched_variations: dict[str, str]
    unpriced_items: list[str]
    priced_items: int
    total_items: int
    priced_share: float
    items_without_unit_price: int
    adj: list[str]
    verb: list[str]
    total_price: float
    basket_per_100: float
    protein_g: float | None
    fat_g: float | None
    carb_g: float | None
    energy_kcal: float | None
    fiber_g: float | None
    sodium_mg: float | None
    cholesterol_mg: float | None
    satfat_g: float | None
    sugars_g: float | None


NutrientName = Literal["protein_g", "fat_g", "carb_g", "energy_kcal", "fiber_g", "sodium_mg", "cholesterol_mg",
                       "satfat_g", "sugars_g"]


class ClassifyRequest(BaseModel):
    """A food to tag with recipe classes. Nutrients are per 100 g, as in the training data; any left out take the
    training medians."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"query": "Grilled chicken breast, skinless, 165 kcal, 31g protein, 3.6g fat, 74mg sodium"},
        {"query": "Waffles, buttermilk, frozen, ready-to-heat", "nutrients": {"energy_kcal": 273, "sugars_g": 4.3}},
        {"query": "fdc:167516"},
    ]})

    # a FoodData Central style description; amounts like "31g protein" or "protein: 31g" in it are read as nutrients,
    # and a bare fdc_id ("167516" / "fdc:167516") looks up a training food instead
    query: str = Field(min_length=1, max_length=500)
    # override the amounts written in the query
    nutrients: dict[NutrientName, float] = Field(default_factory=dict)
    # given terms skip GLiNER / stanza for that field
    product: list[str] | None = Field(default=None, max_length=20)
    adj: list[str] | None = Field(default=None, max_length=20)
    verb: list[str] | None = Field(default=None, max_length=20)


class FoodClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # set when the food was looked up by fdc_id
    fdc_id: int | None = None
    description: str
    product: list[str]
    adj: list[str]
    verb: list[str]
    # per 100 g; None = not given, so the model used the training median
    nutrients: dict[str, float | None]
    # the lowercased terms the encoder read, and how many of them it saw in training
    terms: list[str]
    known_terms: int
    model: str
    # `generic` is exclusive: a generic food has no other label
    labels: list[str]
    score_kind: Literal["probability", "vote share"]
    # every label, highest first
    scores: dict[str, float]
    # the food's labels in the training data, for a food looked up by fdc_id
    training_labels: list[str] | None = None