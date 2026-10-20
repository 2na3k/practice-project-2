import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from .cache import connect_cache
from .db import users_engine
from .repository.recipe_repository import recipe_repository
from .routes import router
from .users import init_user_database

logger = logging.getLogger(__name__)


def load_recommender():
    """The inference service over the shared recipe_repository, or None when there is no catalog to load."""
    if recipe_repository is None:
        # the recipe and user routes do not need the catalog, so the API still starts
        logger.warning("No food catalog is loaded; recommendations answer 503.")
        return None
    started = time.perf_counter()
    # Imported here so torch, hnswlib and the weights load when the API starts, not whenever this module is imported.
    from .inference.service import Recommender

    recommender = Recommender.load(recipe_repository)
    logger.info("Recommender ready in %.1fs.", time.perf_counter() - started)
    return recommender


def load_classifier():
    """The recipe classifier, or None when its weights or packages are missing (the classify routes then answer 503).
    Its packages (requirements-classifier.txt) are not in the API image, so it is optional."""
    started = time.perf_counter()
    try:
        from .inference.classification_service import FoodClassifier, classifier_dir

        if not classifier_dir().is_dir():
            logger.warning("No classifier weights at %s; classification answers 503.", classifier_dir())
            return None
        classifier = FoodClassifier.load()
    except ImportError as error:
        logger.warning("The classifier's packages are not installed (%s); classification answers 503.", error)
        return None
    logger.info("Classifier ready in %.1fs.", time.perf_counter() - started)
    return classifier


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting the API.")
    app.state.cache = connect_cache()
    # with SEED_DEMO_USER, this also injects the demo user
    init_user_database()
    app.state.recommender = load_recommender()
    app.state.classifier = load_classifier()
    logger.info("API ready; recommendations %s, classification %s.",
                "on" if app.state.recommender else "off (no catalog)",
                "on" if app.state.classifier else "off")
    try:
        yield
    finally:
        app.state.cache.close()
        users_engine().dispose()
        logger.info("API stopped.")


app = FastAPI(
    title="Remymy Recipe API",
    version="1.0.0",
    description="Recipes and personalised recommendations. Try `GET /api/recommend/demo` "
    "for the seeded demo user.",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/", include_in_schema=False)
def docs_redirect() -> RedirectResponse:
    return RedirectResponse(url="/docs")
