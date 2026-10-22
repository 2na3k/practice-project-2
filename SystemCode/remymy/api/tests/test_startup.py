from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from remymy_api import main
from remymy_api.main import app, start_model_loading


def test_the_api_answers_while_the_models_load_in_the_background(monkeypatch, cache):
    # the real one, which conftest swaps for loading them before startup ends
    monkeypatch.setattr(main, "start_model_loading", start_model_loading)
    release = threading.Event()
    loaded = []

    def slow_recommender():
        # a model that takes as long as the test lets it
        release.wait(timeout=10)
        loaded.append(("recommender", threading.current_thread().name))

    monkeypatch.setattr(main, "load_term_extractor",
                        lambda: loaded.append(("term extractor", threading.current_thread().name)))
    monkeypatch.setattr(main, "load_recommender", slow_recommender)
    monkeypatch.setattr(main, "load_classifier", lambda: loaded.append(("classifier", threading.current_thread().name)))
    monkeypatch.setattr(main, "load_clusterer", lambda: loaded.append(("clusterer", threading.current_thread().name)))

    with TestClient(app) as client:
        # startup is over while the recommender is still loading
        health = client.get("/health")
        loading = list(loaded)
        release.set()
        loader = next(thread for thread in threading.enumerate() if thread.name == "remymy-model-loading")
        loader.join(timeout=10)

    assert health.status_code == 200
    # the term extractor loaded, the recommender not yet
    assert loading == [("term extractor", "remymy-model-loading")]
    # one after another, on their own thread, the term extractor first
    assert loaded == [("term extractor", "remymy-model-loading"), ("recommender", "remymy-model-loading"),
                      ("clusterer", "remymy-model-loading"), ("classifier", "remymy-model-loading")]


def test_a_model_that_fails_to_load_does_not_keep_the_others_from_loading(monkeypatch, cache):
    loaded = []

    def broken():
        raise RuntimeError("no weights")

    monkeypatch.setattr(main, "load_recommender", broken)
    monkeypatch.setattr(main, "load_classifier", lambda: loaded.append("classifier"))
    monkeypatch.setattr(main, "load_clusterer", lambda: loaded.append("clusterer"))

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert loaded == ["clusterer", "classifier"]
