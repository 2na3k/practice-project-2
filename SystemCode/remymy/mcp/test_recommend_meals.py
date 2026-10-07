import csv
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from main import recommend_meals


class RecommendMealsTests(unittest.TestCase):
    def test_filters_live_catalog_and_ranks_matches_through_model(self):
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8") as source:
            writer = csv.DictWriter(source, fieldnames=["id", "name", "minutes", "tags", "nutrition", "ingredients", "description", "steps"])
            writer.writeheader()
            writer.writerow({"id": 101, "name": "Tofu Noodle Bowl", "minutes": 20, "tags": "['vegan', 'vegetarian', 'asian', 'main-dish']", "nutrition": "[400, 10, 4, 5, 20, 2, 12]", "ingredients": "['tofu', 'noodles']", "description": "Quick tofu bowl", "steps": "['Cook noodles']"})
            writer.writerow({"id": 202, "name": "Chicken Bowl", "minutes": 15, "tags": "['main-dish']", "nutrition": "[500, 10, 4, 5, 20, 2, 12]", "ingredients": "['chicken', 'rice']", "description": "Chicken rice", "steps": "['Cook chicken']"})
            writer.writerow({"id": 303, "name": "Slow Tofu Stew", "minutes": 90, "tags": "['vegan']", "nutrition": "[400, 10, 4, 5, 20, 2, 12]", "ingredients": "['tofu']", "description": "Tofu stew", "steps": "['Cook tofu']"})
            source.flush()
            with patch.dict(os.environ, {"RECIPE_CATALOG_PATH": source.name}), patch(
                "main.rank_foodcom_meals",
                return_value={"recommendations": [{"recipe_id": 101, "score": 0.8}]},
            ) as rank:
                result = json.loads(recommend_meals(
                    {"diet": "vegan", "maxMinutes": 30, "exclude": [], "goal": "balanced", "cuisine": "any"},
                    "vegan tofu meal",
                ))

        self.assertEqual([item["meal"]["id"] for item in result], ["foodcom:101"])
        self.assertEqual(result[0]["meal"]["title"], "Tofu Noodle Bowl")
        rank.assert_called_once()
        self.assertEqual(rank.call_args.args[0], [101])


if __name__ == "__main__":
    unittest.main()
