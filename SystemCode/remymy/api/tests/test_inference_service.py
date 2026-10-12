from __future__ import annotations

from test_recipe_repository import food as csv_food, write_catalog

from remymy_api.inference.service import Recommender, catalog_recipes, ingredient_index, normalize_price_text
from remymy_api.repository.recipe_repository import RecipeRepository
from remymy_api.schemas import CatalogFood, ItemPrice


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


def test_catalog_recipes_are_the_rerankers_input_format():
    apple = catalog_food(3, "Apples, raw", product=["apple", "peel"], sugars_g=None, total_price=0.0,
                         item_prices={"apple": ItemPrice(price=2.5, quantity=500.0),
                                      "peel": ItemPrice(price=1.0, quantity=[100.0, 200.0])})
    recipes = catalog_recipes([catalog_food(1, "Bread, white", sugars_g=4.0), apple,
                               catalog_food(2, "Bread, wheat", sugars_g=6.0)])
    assert [recipe["fdc_id"] for recipe in recipes] == [1, 3, 2]
    recipe = recipes[1]
    assert recipe["name"] == "Apples, raw"
    # every item's pack price, though total_price is 0 while any item is unpriced
    assert recipe["price"] == 3.5
    assert recipe["entities"] == {"product": [["apple"], ["peel"]], "adj": [["fresh"]], "verb": []}
    # sugars FDC has no value for take the catalog median
    assert recipe["nutrients"]["sugar_g"] == 5.0
    assert recipe["nutrients"]["calories_kcal"] == 10.0


def test_ingredient_index_keeps_one_fully_priced_food_per_name():
    index = ingredient_index([
        catalog_food(4, "Bread, rye", total_price=9.0),
        catalog_food(1, "Bread, white", priced_share=0.5, total_price=0.0),
        catalog_food(2, "Bread, wheat", total_price=3.0),
        catalog_food(3, "Bread, multigrain", total_price=3.0, fiber_g=None),
        catalog_food(5, "Apples, raw", total_price=0.0),
    ])
    bread = index[normalize_price_text("Bread")]
    # white is half priced and left out; of 3.0, 3.0 and 9.0 the median is 3.0, a tie the lower fdc_id wins
    assert bread["fdc_id"] == 2 and bread["price"] == 3.0
    assert normalize_price_text("Apples") not in index
    assert "fiber_g" not in ingredient_index([catalog_food(3, "Bread, multigrain", fiber_g=None)])[
        normalize_price_text("Bread")]["nutrient_values"]


def priced_food(fdc_id: int, description: str, product: str, price: float, kcal: float, protein: float) -> dict:
    """A catalog CSV row whose product and numbers are its own, so no two foods embed the same."""
    row = csv_food(fdc_id, description)
    row.update({
        "product": f"['{product}']", "item_prices": f"{{'{product}': {{'price': {price}, 'quantity': 500.0}}}}",
        "item_unit_prices": f"{{'{product}': {price / 5}}}", "item_routes": f"{{'{product}': 'average'}}",
        "total_price": str(price), "basket_per_100": str(price / 5), "energy_kcal": str(kcal), "protein_g": str(protein),
    })
    return row


def test_recommender_scores_the_catalog_with_the_real_model(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "index"))
    rows = [
        priced_food(1, "Chicken breast, roasted", "chicken breast", 6.5, 165, 31),
        priced_food(2, "Tomatoes, red, raw", "tomato", 2.0, 18, 0.9),
        priced_food(3, "Rice, white, cooked", "rice", 3.2, 130, 2.7),
        priced_food(4, "Salmon, raw", "salmon", 12.0, 208, 20),
        priced_food(5, "Spinach, raw", "spinach", 2.6, 23, 2.9),
        priced_food(6, "Chocolate, dark", "chocolate", 4.5, 546, 4.9),
    ]
    repository = RecipeRepository(tmp_path / "catalog.db", write_catalog(tmp_path / "catalog.csv", rows))
    recommender = Recommender.load(repository)

    result = recommender.recommend(
        [{"rating": 5, "date": "2026-09-01", "ingredients": ["chicken breast", "tomatoes", "unknown thing"]}],
        top_k=3,
    )

    assert [[food["fdc_id"] for food in recipe] for recipe in result["ingredients"]] == [[1, 2]]
    items = result["items"]
    assert 0 < len(items) <= 3
    # the history's own foods are not recommended back
    assert {item["fdc_id"] for item in items}.isdisjoint({1, 2})
    assert all(0 <= item["p_like"] <= 1 for item in items)
    assert set(items[0]) == {"fdc_id", "name", "price", "p_like", "similarity"}
