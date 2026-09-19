from __future__ import annotations

from fastapi.testclient import TestClient

import serve
from test_hybrid import artifact


def test_rank_and_token_map_endpoints(tmp_path, monkeypatch):
    path = tmp_path / "model.npz"
    artifact(path)
    monkeypatch.setenv("MODEL_ARTIFACT_PATH", str(path))
    serve.ranker.cache_clear()
    serve.token_mapper.cache_clear()
    client = TestClient(serve.app)

    response = client.post(
        "/v1/rank",
        json={
            "user_id": 10,
            "candidates": [
                {"recipe_id": 200, "content_score": 0.1},
                {"recipe_id": 300, "content_score": 0.9},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["recommendations"][0]["source"] == "hybrid"

    tokens = client.post("/v1/token-map", json={"token_ids": [40480, 40482, 40478, 40481]})
    assert tokens.status_code == 200
    assert tokens.json()["tokens"] == ["<R>", "<s>", "</s>", "</R>"]
