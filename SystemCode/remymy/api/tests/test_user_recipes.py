from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from remymy_api.main import app

RECIPE = {
    "user_id": 1,
    "title": "Grandma's chicken curry",
    "ingredients": ["500 g chicken thighs", "1 onion, chopped", "2 tbsp curry paste"],
    "instructions": ["Brown the chicken.", "Add the onion and paste.", "Simmer for 30 minutes."],
    "tags": [{"label": "hearty_filling", "source": "classifier"}, {"label": "family_favourite", "source": "user"}],
    "nutrients": {"energy_kcal": 180, "protein_g": 14},
}


@pytest.fixture
def client(monkeypatch, tmp_path, cache):
    monkeypatch.setenv("USER_DATABASE_PATH", str(tmp_path / "state" / "users.db"))
    with TestClient(app) as test_client:
        yield test_client


def test_create_recipe_is_declared_but_not_implemented(client):
    # Replace with the stored-recipe assertions once create_recipe saves it.
    response = client.post("/api/recipes", json=RECIPE)

    assert response.status_code == 501
    assert response.json() == {"detail": "Saving your own recipes is not implemented yet."}


@pytest.mark.parametrize("change", [
    {"title": ""},
    {"ingredients": []},
    {"ingredients": [""]},
    {"tags": [{"label": "Hearty Filling", "source": "user"}]},
    {"tags": [{"label": "easy", "source": "chef"}]},
    {"tags": [{"label": "easy", "source": "user"}, {"label": "easy", "source": "classifier"}]},
    {"nutrients": {"vitamin_c_mg": 2}},
    {"servings": 4},
])
def test_invalid_recipes_are_422(client, change):
    assert client.post("/api/recipes", json=RECIPE | change).status_code == 422


def test_create_recipe_is_in_the_openapi_schema(client):
    operation = client.get("/openapi.json").json()["paths"]["/api/recipes"]["post"]

    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/UserRecipeCreate")
    assert {"201", "404", "501", "422"} <= set(operation["responses"])
