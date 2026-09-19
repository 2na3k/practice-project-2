"""HTTP model service for Remymy's hybrid ranker."""

from __future__ import annotations

import ast
import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from hybrid import Candidate, HybridRanker, TokenMapper


class CandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: int
    content_score: float = Field(default=0.0, ge=0.0, le=1.0)
    eligible: bool = True


class RankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int | None = None
    candidates: list[CandidateInput] = Field(min_length=1, max_length=1000)
    limit: int = Field(default=6, ge=1, le=100)
    collaborative_weight: float = Field(default=0.75, ge=0.0, le=1.0)
    filter_seen: bool = True


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token_ids: list[int] | str = Field(max_length=5000)


app = FastAPI(title="Remymy model service", version="1.0.0")


@lru_cache(maxsize=1)
def ranker() -> HybridRanker:
    path = Path(os.getenv("MODEL_ARTIFACT_PATH", "/models/hybrid-foodcom.npz"))
    if path.is_dir():
        path = path / "hybrid-foodcom.npz"
    return HybridRanker(path)


@lru_cache(maxsize=1)
def token_mapper() -> TokenMapper:
    vocab = os.getenv("TOKEN_VOCAB_PATH")
    return TokenMapper(vocab if vocab and Path(vocab).is_file() else None)


@app.get("/")
@app.get("/health")
def health() -> dict[str, object]:
    artifact = os.getenv("MODEL_ARTIFACT_PATH", "/models/hybrid-foodcom.npz")
    return {"status": "ok", "model_loaded": bool(ranker()), "artifact": artifact}


@app.post("/v1/rank")
def rank(request: RankRequest) -> dict[str, object]:
    candidates = [Candidate(**candidate.model_dump()) for candidate in request.candidates]
    return {
        "user_id": request.user_id,
        "recommendations": ranker().rank(
            request.user_id,
            candidates,
            limit=request.limit,
            collaborative_weight=request.collaborative_weight,
            filter_seen=request.filter_seen,
        ),
        "model": "bpr_matrix_factorization_hybrid_v1",
    }


@app.post("/v1/token-map")
def map_tokens(request: TokenRequest) -> dict[str, object]:
    try:
        token_ids = ast.literal_eval(request.token_ids) if isinstance(request.token_ids, str) else request.token_ids
        if not isinstance(token_ids, list) or not all(isinstance(value, int) for value in token_ids):
            raise ValueError
    except (SyntaxError, ValueError):
        raise HTTPException(status_code=422, detail="token_ids must be a list of integers")
    mapper = token_mapper()
    return {"token_ids": token_ids, "tokens": mapper.tokens(token_ids), "decoded": mapper.text(token_ids)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8001")))
