from __future__ import annotations

import csv
from pathlib import Path

import pytest

from remymy_api.repository.recommend_repository import RecommendRepository


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


def write_catalog(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_builds_the_database_when_there_is_none(tmp_path):
    database = tmp_path / "state" / "catalog.db"
    repository = RecommendRepository(database, write_catalog(tmp_path / "catalog.csv", [food(1), food(2)]))
    assert database.is_file()
    assert repository.injected == 2
    assert repository.fdc_ids() == {1, 2}


def test_injects_only_the_ids_the_database_does_not_hold(tmp_path):
    database = tmp_path / "catalog.db"
    RecommendRepository(database, write_catalog(tmp_path / "first.csv", [food(1), food(2)]))
    grown = write_catalog(tmp_path / "grown.csv", [food(1), food(2, description="Apples, renamed"), food(3)])
    repository = RecommendRepository(database, grown)
    assert repository.injected == 1
    assert repository.fdc_ids() == {1, 2, 3}
    # 2 was already stored, so the CSV's newer row for it is not written over it
    assert repository.food(2).description == "Apples, raw"
    assert RecommendRepository(database, grown).injected == 0


def test_uses_an_existing_database_without_a_csv(tmp_path):
    database = tmp_path / "catalog.db"
    RecommendRepository(database, write_catalog(tmp_path / "catalog.csv", [food(1)]))
    repository = RecommendRepository(database, tmp_path / "missing.csv")
    assert repository.injected == 0
    assert repository.fdc_ids() == {1}


def test_needs_a_database_or_a_csv(tmp_path):
    with pytest.raises(FileNotFoundError):
        RecommendRepository(tmp_path / "catalog.db", tmp_path / "missing.csv")


def test_rejects_a_csv_missing_a_column(tmp_path):
    row = food(1)
    del row["sugars_g"]
    with pytest.raises(ValueError, match="sugars_g"):
        RecommendRepository(tmp_path / "catalog.db", write_catalog(tmp_path / "catalog.csv", [row]))


@pytest.fixture
def repository(tmp_path) -> RecommendRepository:
    rows = [food(3, "Chicken, breast, roasted"), food(1), food(2, "Pineapple, raw", sugars_g="")]
    return RecommendRepository(tmp_path / "catalog.db", write_catalog(tmp_path / "catalog.csv", rows))


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
