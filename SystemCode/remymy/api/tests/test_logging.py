from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from remymy_api.logs import FORMAT
from remymy_api.main import app

SRC = Path(__file__).resolve().parents[1] / "src"


def import_api(log_level: str) -> subprocess.CompletedProcess:
    """Import the package in a fresh interpreter, as uvicorn does: pytest's own handlers sit on this process's root
    logger, which would make configure_logging (rightly) leave it alone."""
    code = ("import logging, remymy_api; root = logging.getLogger(); "
            "print(logging.getLevelName(root.level)); print(root.handlers[0].formatter._fmt)")
    env = {**os.environ, "PYTHONPATH": str(SRC), "LOG_LEVEL": log_level}
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)


def test_log_level_and_format_come_from_the_environment():
    assert import_api("debug").stdout.splitlines() == ["DEBUG", FORMAT]


def test_an_unknown_log_level_falls_back_to_info():
    result = import_api("loud")
    assert result.stdout.splitlines() == ["INFO", FORMAT]
    assert "WARNING remymy_api.logs: Unknown LOG_LEVEL 'LOUD'; logging at INFO." in result.stderr


def test_a_missing_recipe_database_is_logged_but_not_shown_to_callers(monkeypatch, tmp_path, cache, caplog):
    missing = tmp_path / "missing.db"
    monkeypatch.setenv("DATABASE_PATH", str(missing))
    monkeypatch.setenv("USER_DATABASE_PATH", str(tmp_path / "users.db"))

    with TestClient(app) as client:
        response = client.get("/api/recipes")

    assert response.status_code == 503
    assert str(missing) not in response.text
    assert any(record.levelno == logging.WARNING and record.getMessage() == f"No recipe database at {missing}."
               for record in caplog.records)
