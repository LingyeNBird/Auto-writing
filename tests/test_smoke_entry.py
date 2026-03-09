from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


def test_app_entry_is_importable() -> None:
    module = importlib.import_module("auto_writing.app")
    app = module.app

    assert app is not None


def test_create_app_returns_named_application() -> None:
    module = importlib.import_module("auto_writing.app")
    create_app = module.create_app

    created = create_app()

    assert created.title == "Auto Writing"


def test_create_app_exposes_health_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    storage_dir = tmp_path / "storage"

    monkeypatch.setenv("AUTO_WRITING_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AUTO_WRITING_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("AUTO_WRITING_STORAGE_DIR", str(storage_dir))

    module = importlib.import_module("auto_writing.app")
    create_app = module.create_app

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
