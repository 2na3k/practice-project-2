from __future__ import annotations

import csv
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from remymy_api.db import sqlite_engine
from remymy_api.repository.recipe_repository import (
    RecipeRepository,
    ingredient_index,
    init_catalog_database,
    is_food_id,
    name_key,
)
from remymy_api.schemas import CatalogFood, ItemPrice


def food(fdc_id: int, description: str = "Apples, raw", sugars_g: str = "10.4") -> dict[str, str]:
    return {
        "fdc_id": str(fdc_id), "description": description, "product": "['apple']",
        "item_prices": "{'apple': {'price': 3.0, 'quantity': [500.0, 1000.0]}}", "item_unit_prices": "{'apple': 0.45}",
        "item_routes": "{'apple': 'average'}", "matched_variations": "{}", "unpriced_items": "[]",
        "priced_items": "1", "total_items": "1", "priced_share": "1.0", "items_without_unit_price": "0",
        "adj": "['fresh']", "verb": "[]", "total_price": "3.0", "basket_per_100": "0.45",
        "protein_g": "0.26", "fat_g": "0.17", "carb_g": "13.81", "energy_kcal": "52.0", "fiber_g": "2.4",
        "sodium_mg": "1.0", "cholesterol_mg": "0.0", "satfat_g": "0.028", "sugars_g": sugars_g,
    }


def catalog_food(fdc_id: int, description: str, **values: object) -> CatalogFood:
    fields = {
        "fdc_id": fdc_id, "description": description, "product": ["apple"],
        "item_prices": {"apple": ItemPrice(price=2.5, quantity=500.0)}, "item_unit_prices": {"apple": 0.5},
        "item_routes": {"apple": "average"}, "matched_variations": {}, "unpriced_items": [],
        "priced_items": 1, "total_items": 1, "priced_share": 1.0, "items_without_unit_price": 0,
        "adj": ["fresh"], "verb": [], "total_price": 2.5, "basket_per_100": 0.5,
        "protein_g": 1.0, "fat_g": 1.0, "carb_g": 1.0, "energy_kcal": 10.0, "fiber_g": 1.0,
        "sodium_mg": 1.0, "cholesterol_mg": 0.0, "satfat_g": 0.1, "sugars_g": 10.0,
    }
    fields.update(values)
    return CatalogFood(**fields)


def write_catalog(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def open_catalog():
    """A RecipeRepository on the catalog database at a path, as a request gets one; its session is closed after the
    test."""
    sessions: list[Session] = []

    def open_(database: Path) -> RecipeRepository:
        sessions.append(Session(sqlite_engine(database)))
        return RecipeRepository(sessions[-1])

    yield open_
    for session in sessions:
        session.close()


def test_builds_the_database_when_there_is_none(tmp_path, open_catalog):
    database = tmp_path / "state" / "catalog.db"
    assert init_catalog_database(database, write_catalog(tmp_path / "catalog.csv", [food(1), food(2)])) is True
    assert database.is_file()
    assert open_catalog(database).fdc_ids() == {1, 2}


def test_injects_only_the_ids_the_database_does_not_hold(tmp_path, open_catalog):
    repository = open_catalog(tmp_path / "catalog.db")
    assert repository.from_csv_to_db(write_catalog(tmp_path / "first.csv", [food(1), food(2)])) == 2
    grown = write_catalog(tmp_path / "grown.csv", [food(1), food(2, description="Apples, renamed"), food(3)])

    assert repository.from_csv_to_db(grown) == 1
    assert repository.fdc_ids() == {1, 2, 3}
    # 2 was already stored, so the CSV's newer row for it is not written over it
    assert repository.food(2).description == "Apples, raw"
    assert repository.from_csv_to_db(grown) == 0


def test_uses_an_existing_database_without_a_csv(tmp_path, open_catalog):
    database = tmp_path / "catalog.db"
    init_catalog_database(database, write_catalog(tmp_path / "catalog.csv", [food(1)]))
    assert init_catalog_database(database, tmp_path / "missing.csv") is True
    assert open_catalog(database).fdc_ids() == {1}


def test_without_a_database_or_a_csv_there_is_no_catalog(tmp_path):
    assert init_catalog_database(tmp_path / "catalog.db", tmp_path / "missing.csv") is False
    # nothing is created empty
    assert not (tmp_path / "catalog.db").exists()


def test_rejects_a_csv_missing_a_column_before_writing(tmp_path, open_catalog):
    row = food(1)
    del row["sugars_g"]
    with pytest.raises(ValueError, match="sugars_g"):
        open_catalog(tmp_path / "catalog.db").from_csv_to_db(write_catalog(tmp_path / "catalog.csv", [row]))
    assert not (tmp_path / "catalog.db").exists()


@pytest.fixture
def repository(tmp_path, open_catalog) -> RecipeRepository:
    rows = [food(3, "Chicken, breast, roasted"), food(1), food(2, "Pineapple, raw", sugars_g="")]
    init_catalog_database(tmp_path / "catalog.db", write_catalog(tmp_path / "catalog.csv", rows))
    return open_catalog(tmp_path / "catalog.db")


def test_foods_come_back_with_their_lists_dicts_and_missing_nutrients(repository):
    food = repository.food(2)
    assert food.product == ["apple"]
    assert food.adj == ["fresh"]
    assert food.item_prices["apple"].price == 3.0
    assert food.item_prices["apple"].quantity == [500.0, 1000.0]
    assert food.item_unit_prices == {"apple": 0.45}
    assert food.priced_items == 1
    assert food.sugars_g is None
    assert repository.food(99) is None


def test_foods_by_ids_keep_the_order_asked_and_skip_unknown_ids(repository):
    assert [food.fdc_id for food in repository.foods_by_ids([3, 99, 1, 3])] == [3, 1]
    assert repository.foods_by_ids([]) == []


def test_all_foods_are_in_fdc_id_order(repository):
    assert [food.fdc_id for food in repository.all_foods()] == [1, 2, 3]


def test_search_matches_the_description_case_insensitively(repository):
    assert [food.fdc_id for food in repository.search("APPLE")] == [1, 2]
    assert [food.fdc_id for food in repository.search("apple", limit=1, offset=1)] == [2]
    assert repository.search("tofu") == []


def test_ingredient_index_keeps_one_fully_priced_food_per_name():
    index = ingredient_index([
        catalog_food(4, "Bread, rye", total_price=9.0),
        catalog_food(1, "Bread, white", priced_share=0.5, total_price=0.0),
        catalog_food(2, "Bread, wheat", total_price=3.0),
        catalog_food(3, "Bread, multigrain", total_price=3.0, fiber_g=None),
        catalog_food(5, "Apples, raw", total_price=0.0),
    ])
    bread = index[name_key("Bread")]
    # white is half priced and left out; of 3.0, 3.0 and 9.0 the median is 3.0, a tie the lower fdc_id wins
    assert bread.fdc_id == 2 and bread.price == 3.0
    assert name_key("Apples") not in index
    assert "fiber_g" not in ingredient_index([catalog_food(3, "Bread, multigrain", fiber_g=None)])[
        name_key("Bread")].nutrient_values


def test_digest_matches_each_line_once_and_averages_the_matched_nutrients(tmp_path, open_catalog):
    bread = food(2, "Bread, white", sugars_g="")
    bread.update(energy_kcal="266.0", total_price="2.0")
    init_catalog_database(tmp_path / "catalog.db", write_catalog(tmp_path / "catalog.csv", [food(1), bread]))
    repository = open_catalog(tmp_path / "catalog.db")
    # apple and bread
    assert repository.load_ingredient_index() == 2

    toast = repository.digest("openrecipes:7", ["apples", "Apples", "bread", "unknown thing"])

    assert toast.recipe_id == "openrecipes:7"
    assert [(ingredient.fdc_id, ingredient.price) for ingredient in toast.ingredients] == [(1, 3.0), (2, 2.0)]
    assert toast.nutrient_values["energy_kcal"] == (52.0 + 266.0) / 2
    # bread has no sugars value, so the apple's is the mean
    assert toast.nutrient_values["sugars_g"] == 10.4
    assert repository.digest("openrecipes:8", ["unknown thing"]).ingredients == []


def test_the_ingredient_index_is_shared_and_rebuilt_once_new_foods_come(tmp_path, open_catalog):
    database = tmp_path / "catalog.db"
    init_catalog_database(database, write_catalog(tmp_path / "first.csv", [food(1)]))
    first = open_catalog(database)
    assert first.load_ingredient_index() == 1
    # another request's repository on the same database gets the same index, not a new one
    assert open_catalog(database)._ingredients() is first._ingredients()

    open_catalog(database).from_csv_to_db(write_catalog(tmp_path / "grown.csv", [food(1), food(2, "Bread, white")]))

    assert open_catalog(database).load_ingredient_index() == 2
    assert [ingredient.fdc_id for ingredient in first.digest("r", ["bread"]).ingredients] == [2]


def test_name_key_drops_case_asides_punctuation_and_stop_words_then_singularizes():
    assert name_key("Tomatoes (ripe), chopped") == "tomato chopped"
    assert name_key("2 cups of the Apples") == "2 cup apple"
    # negations are kept, and a lone % is dropped
    assert name_key("No-salt butter, 100% %") == "no salt butter 100%"
    assert name_key("(optional)") == ""


def test_a_rated_food_is_digested_as_itself(repository):
    apple = repository.digest_food("1")
    # its price is the sum of its items' pack prices
    assert [(ingredient.fdc_id, ingredient.price) for ingredient in apple.ingredients] == [(1, 3.0)]
    assert apple.nutrient_values["energy_kcal"] == 52.0
    assert repository.digest_food("99").ingredients == []


def test_food_ids_are_fdc_ids_and_recipe_ids_are_not():
    assert is_food_id("169286")
    assert not is_food_id("openrecipes:5210")
    assert not is_food_id("١٢٣")


def test_startup_builds_the_catalog_database_from_the_csv(tmp_path, monkeypatch, cache, open_catalog):
    from fastapi.testclient import TestClient

    from remymy_api.main import app

    database = tmp_path / "state" / "catalog.db"
    monkeypatch.setenv("CATALOG_PATH", str(write_catalog(tmp_path / "catalog.csv", [food(1), food(2)])))
    monkeypatch.setenv("CATALOG_DATABASE_PATH", str(database))
    monkeypatch.setenv("USER_DATABASE_PATH", str(tmp_path / "state" / "users.db"))

    with TestClient(app) as client:
        found = client.get("/api/catalog/2")

    assert found.status_code == 200
    assert open_catalog(database).fdc_ids() == {1, 2}


def test_the_catalog_route_serves_one_item(repository, cache, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from remymy_api.main import app

    monkeypatch.setenv("USER_DATABASE_PATH", str(tmp_path / "state" / "users.db"))
    monkeypatch.setenv("CATALOG_DATABASE_PATH", str(tmp_path / "catalog.db"))
    with TestClient(app) as client:
        found = client.get("/api/catalog/2")
        missing = client.get("/api/catalog/99")
        invalid = client.get("/api/catalog/openrecipes:1")
        monkeypatch.setenv("CATALOG_DATABASE_PATH", str(tmp_path / "gone.db"))
        unloaded = client.get("/api/catalog/2")

    assert found.status_code == 200
    body = found.json()
    assert (body["fdc_id"], body["description"], body["product"]) == (2, "Pineapple, raw", ["apple"])
    assert body["item_prices"]["apple"] == {"price": 3.0, "quantity": [500.0, 1000.0]}
    assert body["sugars_g"] is None
    assert (missing.status_code, missing.json()) == (404, {"detail": "Catalog item not found."})
    assert invalid.status_code == 422
    assert unloaded.status_code == 503
    # no empty catalog database was created for the missing one
    assert not (tmp_path / "gone.db").exists()
