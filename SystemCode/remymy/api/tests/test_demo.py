from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from remymy_api.main import app
from remymy_api.db import database_path
from remymy_api.schema import connect, init_database
from remymy_api.users import DEMO_USERNAME

DEMO_LIKES = [
    ("openrecipes:5210", True),
    ("openrecipes:8236", True),
    ("openrecipes:9906", True),
    ("openrecipes:1052", True),
    ("openrecipes:3911", False),
]


@pytest.fixture
def users():
    # no recipes: the demo user is injected without them (conftest gives each test a remymy.db of its own)
    pass


def demo_history() -> list[tuple[str, bool]]:
    connection = connect(database_path())
    try:
        return [
            (row["recipe_id"], bool(row["liked"]))
            for row in connection.execute(
                "SELECT h.recipe_id, h.liked FROM user_history h "
                "JOIN app_user u ON u.user_id = h.user_id WHERE u.username = ? ORDER BY h.history_id",
                (DEMO_USERNAME,),
            )
        ]
    finally:
        connection.close()


def test_demo_user_is_injected_once_with_the_schema(users, cache, monkeypatch, caplog):
    monkeypatch.setenv("SEED_DEMO_USER", "true")

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    with TestClient(app):
        pass

    assert demo_history() == DEMO_LIKES
    connection = connect(database_path())
    try:
        (row,) = connection.execute(
            "SELECT json(preferences) AS preferences FROM app_user WHERE username = ?", (DEMO_USERNAME,)
        ).fetchall()
    finally:
        connection.close()
    assert json.loads(row["preferences"]) == {
        "special_diet": None, "cuisines": ["Italian", "Mexican"], "preferred_nutrient": "protein",
    }
    injected = [record for record in caplog.records if record.getMessage().startswith("Injected the demo user")]
    assert len(injected) == 1


def test_an_existing_demo_history_is_kept(users, monkeypatch):
    monkeypatch.setenv("SEED_DEMO_USER", "true")
    init_database()
    connection = connect(database_path())
    with connection:
        connection.execute("DELETE FROM user_history WHERE recipe_id <> 'openrecipes:5210'")
    connection.close()

    init_database()

    assert demo_history() == [("openrecipes:5210", True)]


def test_demo_user_is_not_injected_by_default(users, cache, monkeypatch):
    monkeypatch.delenv("SEED_DEMO_USER", raising=False)

    with TestClient(app):
        pass

    assert demo_history() == []


def test_swagger_is_served_with_the_demo_username_prefilled(users, cache):
    with TestClient(app) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/docs"
        assert client.get("/docs").status_code == 200
        spec = client.get("/openapi.json").json()

    parameter = spec["paths"]["/api/recommend/{username}"]["get"]["parameters"][0]
    assert parameter["name"] == "username"
    assert parameter["examples"]["demo"]["value"] == DEMO_USERNAME
