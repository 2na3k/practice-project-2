import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .cache import connect_cache
from .db import database_engine, has_catalog
from .repository.recipe_repository import RecipeRepository, add_foodcom_recipes
from .routes import router
from .schema import init_database

logger = logging.getLogger(__name__)


def load_term_extractor():
    """GLiNER and stanza, which the classifier and the clusterer both read the terms of a description with, loaded
    once: TermExtractor is a singleton, so their TermExtractor() is then this instance. None when their packages
    (requirements-classifier.txt) are not installed or the models cannot be loaded; the classifier and the clusterer
    then try loading them again themselves."""
    started = time.perf_counter()
    try:
        from .inference.food_query import TermExtractor

        # the singleton's first construction, with GLiNER and stanza loaded (downloaded the first time)
        extractor = TermExtractor().load()
    except ImportError as error:
        logger.warning("The term extractor's packages are not installed (%s).", error)
        return None
    except Exception:
        # e.g. GLiNER or stanza could not be downloaded
        logger.exception("Loading the term extractor failed.")
        return None
    logger.info("Term extractor ready in %.1fs.", time.perf_counter() - started)
    return extractor


def load_recommender():
    """The inference service over the database's catalog, or None when there is no catalog to load."""
    with Session(database_engine()) as session:
        if not has_catalog(session):
            # the recipe and user routes do not need the catalog, so the API still starts
            logger.warning("No food catalog is loaded; recommendations answer 503.")
            return None
        started = time.perf_counter()
        # the fully priced Food.com recipes join the catalog now, after startup filled it from its CSV, so the
        # recommender's index holds both
        add_foodcom_recipes(session)
        # Imported here so torch, hnswlib and the weights load when the API starts, not whenever this module is imported.
        from .inference.service import Recommender

        # the singleton's first construction: reads the catalog's foods and builds its ingredient index, which the
        # requests' repositories then share; the routes' Recommender() is then this instance
        recommender = Recommender(RecipeRepository(session))
    logger.info("Recommender ready in %.1fs.", time.perf_counter() - started)
    return recommender


def load_classifier():
    """The recipe classifier with all its models loaded and warmed up, or None when its weights, packages or models are
    missing (the classify routes then answer 503). Its packages (requirements-classifier.txt) are not in the API image, so it is optional."""
    started = time.perf_counter()
    try:
        from .inference.classification_service import FoodClassifier, classifier_dir

        if not classifier_dir().is_dir():
            logger.warning("No classifier weights at %s; classification answers 503.", classifier_dir())
            return None
        # the singleton's first construction: the routes' FoodClassifier() is then this instance, its models loaded
        classifier = FoodClassifier()
    except ImportError as error:
        logger.warning("The classifier's packages are not installed (%s); classification answers 503.", error)
        return None
    except Exception:
        # e.g. GLiNER, stanza or the encoder could not be downloaded
        logger.exception("Loading the classifier failed; classification answers 503.")
        return None
    logger.info("Classifier ready in %.1fs.", time.perf_counter() - started)
    return classifier


def load_clusterer():
    """The recipe clusterer with all its models loaded and warmed up, or None when its weights, packages or models are
    missing (the cluster routes then answer 503). It needs the classifier's packages, and shares the classifier's term
    extractor (TermExtractor is a singleton too), so GLiNER and stanza are loaded once."""
    started = time.perf_counter()
    try:
        from .inference.service import Clusterer, clustering_dir

        if not clustering_dir().is_dir():
            logger.warning("No clustering weights at %s; clustering answers 503.", clustering_dir())
            return None
        # the singleton's first construction: the routes' Clusterer() is then this instance, its models loaded
        clusterer = Clusterer()
    except ImportError as error:
        logger.warning("The clusterer's packages are not installed (%s); clustering answers 503.", error)
        return None
    except Exception:
        # e.g. GLiNER, stanza or the encoder could not be downloaded
        logger.exception("Loading the clusterer failed; clustering answers 503.")
        return None
    logger.info("Clusterer ready in %.1fs.", time.perf_counter() - started)
    return clusterer


def load_models() -> None:
    """Load (and warm up) the term extractor, the recommender, the clusterer and the classifier, one after another: each
    is a singleton, so a route's Recommender(), FoodClassifier() or Clusterer() is then the loaded instance, and a
    request only infers. The term extractor goes first, as the clusterer and the classifier share it. Until one is
    loaded, and if it cannot load, its routes answer 503."""
    started = time.perf_counter()
    logger.info("Loading the models on thread %s.", threading.current_thread().name)
    loaded = []

    # each on its own: one that fails does not keep the others from loading
    for name, load in (("term extractor", load_term_extractor), ("recommender", load_recommender),
                       ("clusterer", load_clusterer), ("classifier", load_classifier)):
        try:
            logger.info(f"Loading {name}")
            loaded.append(load())
            logger.info(f"Loaded {name} successfully")
        except Exception:
            logger.exception("Loading the %s failed; its routes answer 503.", name)
            loaded.append(None)
            continue
    # in the order loaded above
    extractor, recommender, clusterer, classifier = loaded
    logger.info("Models loaded in %.1fs; term extractor %s, recommendations %s, classification %s, clustering %s.",
                time.perf_counter() - started,
                "on" if extractor else "off",
                "on" if recommender else "off (no catalog)",
                "on" if classifier else "off",
                "on" if clusterer else "off")


def start_model_loading() -> threading.Thread:
    """Start loading the models on a thread of their own, and return it."""
    thread = threading.Thread(target=load_models, name="remymy-model-loading", daemon=True)
    thread.start()
    return thread


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting the API.")
    app.state.cache = connect_cache()
    # creates remymy.db's tables, imports the old databases the first time, migrates the users' data (and with
    # SEED_DEMO_USER injects the demo user), and adds the catalog CSV's new foods
    init_database()
    # the models load on a thread of their own, which startup does not wait for: the API answers (and passes its health
    # check) at once, and each model's routes answer 503 until it is loaded. Loading them takes minutes the first time
    # (GLiNER, stanza and the encoder download).
    start_model_loading()
    logger.info("API ready; the models are loading in the background.")
    try:
        yield
    finally:
        app.state.cache.close()
        database_engine().dispose()
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
