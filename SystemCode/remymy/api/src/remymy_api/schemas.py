from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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
