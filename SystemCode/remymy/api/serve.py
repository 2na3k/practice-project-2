import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

# remymy.db is created on the first start, taking the recipes of the old recipe database and the users of the old users
# database once.
LOCAL_DEFAULTS = {
    "DATABASE_PATH": str(HERE / "data" / "remymy.db"),
    "LEGACY_FOOD_DATABASE_PATH": str(HERE.parent / "data" / "remymy-food.db"),
    "LEGACY_USER_DATABASE_PATH": str(HERE / "data" / "remymy-users.db"),
    "CATALOG_PATH": str(HERE.parent / "data" / "price_mapped_nutrients.csv"),
    "REDIS_URL": "redis://localhost:6379/0",
    "SEED_DEMO_USER": "true",
}


if __name__ == "__main__":
    import uvicorn

    for name, value in LOCAL_DEFAULTS.items():
        os.environ.setdefault(name, value)
    uvicorn.run(
        "remymy_api.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8002")),
    )
