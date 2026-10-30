"""Configuration and versioning for offline recommendation evaluation."""

from __future__ import annotations

import hashlib
import os
import random
from pathlib import Path


def eval_k() -> int:
    value = int(os.getenv("REMYMY_EVAL_K", "6"))
    if value < 1:
        raise ValueError("REMYMY_EVAL_K must be a positive integer")
    return value


def pool_size() -> int:
    value = int(os.getenv("REMYMY_POOL_SIZE", "500"))
    if value < 1:
        raise ValueError("REMYMY_POOL_SIZE must be a positive integer")
    return value


def ranking_version() -> str:
    """Fingerprint model, ranker weight, and catalog version, independently of K."""
    model_path = Path(os.getenv("MODEL_ARTIFACT_PATH", "/models/hybrid-foodcom.npz"))
    if model_path.is_dir():
        model_path = model_path / "hybrid-foodcom.npz"
    if model_path.is_file():
        digest = hashlib.sha256()
        with model_path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
        model_version = digest.hexdigest()
    else:
        model_version = os.getenv("REMYMY_MODEL_VERSION", str(model_path))

    catalog_version = os.getenv("REMYMY_DATA_VERSION")
    if not catalog_version:
        catalog_path = Path(os.getenv("RECIPE_CATALOG_PATH", ""))
        if catalog_path.is_file():
            stat = catalog_path.stat()
            catalog_version = f"{stat.st_size}:{stat.st_mtime_ns}"
        else:
            catalog_version = "unspecified"

    material = "|".join((model_version, os.getenv("REMYMY_COLLABORATIVE_WEIGHT", "0.75"), catalog_version))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def eval_enabled() -> bool:
    if os.getenv("REMYMY_EVAL_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    sample_rate = float(os.getenv("REMYMY_EVAL_SAMPLE_RATE", "1"))
    if not 0 <= sample_rate <= 1:
        raise ValueError("REMYMY_EVAL_SAMPLE_RATE must be between 0 and 1")
    return random.random() < sample_rate
