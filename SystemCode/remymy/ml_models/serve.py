from __future__ import annotations

import os
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from model_inference_reranker_avgemb import normalize_price_text
from model_inference_score_recommend import RetrievalPipeline


HERE = Path(__file__).resolve().parent
CATALOG_PATH = Path(os.getenv("CATALOG_PATH", "/catalog/price_mapped_nutrients.csv"))
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


class HistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fdc_id: int
    rating: float | None = None
    date: str


class RecommendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    history: list[HistoryItem] = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=50)
    candidates: int = Field(default=100, ge=1, le=1000)


class MatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredients: list[str] = Field(max_length=5000)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline = RetrievalPipeline.from_paths(CATALOG_PATH, ARTIFACT_DIR, INDEX_DIR)
    app.state.ingredients = build_ingredient_index(CATALOG_PATH)
    app.state.lock = threading.Lock()
    yield


app = FastAPI(title="Remymy ML models", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health(request: Request) -> dict[str, object]:
    return {
        "status": "ok",
        "catalog_items": len(request.app.state.pipeline.recipes),
        "ingredient_keys": len(request.app.state.ingredients),
    }


@app.post("/v1/ingredients/match")
def match_ingredients(body: MatchRequest, request: Request) -> dict[str, object]:
    index = request.app.state.ingredients
    return {"matches": [index.get(normalize_price_text(text)) for text in body.ingredients]}


@app.post("/v1/recommend")
def recommend(body: RecommendRequest, request: Request) -> dict[str, object]:
    pipeline = request.app.state.pipeline
    unknown = sorted({item.fdc_id for item in body.history if item.fdc_id not in pipeline.row_of})
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown fdc_id: {unknown[:10]}")
    history = [item.model_dump() for item in body.history]
    with request.app.state.lock:
        results = pipeline.recommend({"history": history}, n_candidates=body.candidates, top_k=body.top_k)
    return {
        "items": [
            {
                "fdc_id": result["fdc_id"],
                "name": result["name"],
                "price": result["price"],
                "p_like": result["p_like"],
                "similarity": result["similarity"],
            }
            for result in results
        ]
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8003")))
