from __future__ import annotations

import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "inference"))

from model_inference_reranker_avgemb import normalize_price_text
from model_inference_score_recommend import RetrievalPipeline


CATALOG_PATH = Path(
    os.getenv("CATALOG_PATH", str(HERE.parent / "data" / "price_mapped_nutrients.csv"))
)
ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", str(HERE / "weights" / "gru_xattn_reranker")))
INDEX_DIR = Path(os.getenv("INDEX_DIR", str(Path(tempfile.gettempdir()) / "remymy-retrieval-index")))
NUTRIENT_COLUMNS = (
    "energy_kcal",
    "protein_g",
    "fat_g",
    "satfat_g",
    "carb_g",
    "sugars_g",
    "fiber_g",
    "sodium_mg",
    "cholesterol_mg",
)


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: float | None = None
    date: str
    ingredients: list[str] = Field(max_length=500)


class RecommendRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "history": [
                        {
                            "rating": 5,
                            "date": "2026-09-01",
                            "ingredients": ["chicken breast", "tomatoes", "salt", "butter"],
                        },
                        {"rating": 2, "date": "2026-09-10", "ingredients": ["apples", "butter"]},
                    ],
                    "top_k": 5,
                }
            ]
        },
    )

    history: list[HistoryEntry] = Field(min_length=1, max_length=500)
    top_k: int = Field(default=10, ge=1, le=50)
    candidates: int = Field(default=100, ge=1, le=1000)


def build_ingredient_index(catalog_path: Path) -> dict[str, dict[str, object]]:
    frame = pd.read_csv(catalog_path)
    frame["key"] = (
        frame["description"].fillna("").str.split(",", n=1).str[0].str.strip().map(normalize_price_text)
    )
    priced = frame[
        frame["key"].ne("") & frame["priced_share"].eq(1) & frame["total_price"].gt(0)
    ].sort_values("fdc_id")
    index = {}
    for key, group in priced.groupby("key", sort=False):
        row = group.loc[(group["total_price"] - group["total_price"].median()).abs().idxmin()]
        index[key] = {
            "fdc_id": int(row["fdc_id"]),
            "name": str(row["description"]),
            "price": float(row["total_price"]),
            "nutrient_values": {
                column: float(row[column]) for column in NUTRIENT_COLUMNS if pd.notna(row[column])
            },
        }
    return index


def match_entry(index: dict[str, dict[str, object]], texts: list[str]) -> list[dict[str, object]]:
    keys = dict.fromkeys(normalize_price_text(text) for text in texts)
    return [index[key] for key in keys if key in index]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline = RetrievalPipeline.from_paths(CATALOG_PATH, ARTIFACT_DIR, INDEX_DIR)
    app.state.ingredients = build_ingredient_index(CATALOG_PATH)
    yield


app = FastAPI(title="Remymy ML models", version="1.0.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def docs_redirect() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["health"])
def health(request: Request) -> dict[str, object]:
    return {
        "status": "ok",
        "catalog_items": len(request.app.state.pipeline.recipes),
        "ingredient_keys": len(request.app.state.ingredients),
    }


@app.post("/v1/recommend", tags=["recommendations"])
def recommend(body: RecommendRequest, request: Request) -> dict[str, object]:
    matched = [match_entry(request.app.state.ingredients, entry.ingredients) for entry in body.history]
    history = [
        {"fdc_id": ingredient["fdc_id"], "rating": entry.rating, "date": entry.date}
        for entry, ingredients in zip(body.history, matched)
        for ingredient in ingredients
    ]
    results = []
    if history:
        results = request.app.state.pipeline.recommend(
            {"history": history}, n_candidates=body.candidates, top_k=body.top_k
        )
    return {
        "ingredients": matched,
        "items": [
            {
                "fdc_id": result["fdc_id"],
                "name": result["name"],
                "price": result["price"],
                "p_like": result["p_like"],
                "similarity": result["similarity"],
            }
            for result in results
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8003")))
