"""Starlette handlers for blind, token-protected candidate review."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .evaluation import evaluation_db

_BLIND_FIELDS = {
    "score", "content_score", "collaborative_score", "popularity", "source", "rank",
    "trace_id", "grade", "grades", "previous_grade",
}


def _authorized(request: Request) -> JSONResponse | None:
    token = os.getenv("REMYMY_EVALUATION_TOKEN", "").strip()
    if not token:
        return JSONResponse({"error": "evaluation review is disabled"}, status_code=503)
    authorization = request.headers.get("authorization", "")
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or not provided or not secrets.compare_digest(provided, token):
        return JSONResponse({"error": "invalid reviewer token"}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})
    return None


def _get_snapshot(evaluation_id: str):
    with evaluation_db() as db:
        row = db.execute("SELECT snapshot, grades FROM ranking_evaluations WHERE id = ?",
                         (evaluation_id,)).fetchone()
    return row


async def review_get(request: Request) -> JSONResponse:
    denied = _authorized(request)
    if denied:
        return denied
    evaluation_id = request.path_params["id"]
    row = _get_snapshot(evaluation_id)
    if row is None:
        return JSONResponse({"error": "unknown evaluation"}, status_code=404)
    snapshot = json.loads(row[0])
    candidates = [dict(candidate) for candidate in snapshot.get("candidates", [])]

    catalog = Path(os.getenv("RECIPE_CATALOG_PATH", "data/raw/food-com/RAW_recipes.csv"))
    if catalog.is_file() and candidates:
        from .judge_eval import hydrate_catalog

        hydrated = hydrate_catalog({str(item["recipe_id"]) for item in candidates}, catalog)
        for item in candidates:
            extra = hydrated[str(item["recipe_id"])]
            item.setdefault("title", extra["title"])
            item["ingredients"] = extra["ingredients"]
            item["steps"] = extra["steps"]

    blinded = [{key: value for key, value in candidate.items() if key not in _BLIND_FIELDS}
               for candidate in candidates]
    secrets.SystemRandom().shuffle(blinded)
    return JSONResponse({
        "evaluation_id": evaluation_id,
        "request": snapshot.get("request", {}),
        "candidates": blinded,
        "grade_range": [0, 3],
        "relevant_min_grade": 2,
    })


async def grades_post(request: Request) -> JSONResponse:
    denied = _authorized(request)
    if denied:
        return denied
    evaluation_id = request.path_params["id"]
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    grades = payload.get("grades") if isinstance(payload, dict) else None
    if not isinstance(grades, dict) or any(type(grade) is not int or not 0 <= grade <= 3 for grade in grades.values()):
        return JSONResponse({"error": "grades must map recipe ids to integers from 0 to 3"}, status_code=400)

    with evaluation_db() as db:
        row = db.execute("SELECT snapshot FROM ranking_evaluations WHERE id = ?", (evaluation_id,)).fetchone()
        if row is None:
            return JSONResponse({"error": "unknown evaluation"}, status_code=404)
        snapshot = json.loads(row[0])
        candidate_ids = {str(candidate["recipe_id"]) for candidate in snapshot.get("candidates", [])}
        if not set(map(str, grades)).issubset(candidate_ids):
            return JSONResponse({"error": "grade ids must belong to the candidate pool"}, status_code=400)
        db.execute("UPDATE ranking_evaluations SET grades = ? WHERE id = ?",
                   (json.dumps({str(key): value for key, value in grades.items()}), evaluation_id))
    return JSONResponse({"evaluation_id": evaluation_id, "saved": len(grades)})


def create_review_app() -> Starlette:
    return Starlette(routes=[
        Route("/eval/{id}", review_get, methods=["GET"]),
        Route("/eval/{id}/grades", grades_post, methods=["POST"]),
    ])
