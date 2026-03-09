from __future__ import annotations

import importlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


def _build_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path, Path]:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    storage_dir = tmp_path / "storage"

    monkeypatch.setenv("AUTO_WRITING_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AUTO_WRITING_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("AUTO_WRITING_STORAGE_DIR", str(storage_dir))

    app_module = importlib.import_module("auto_writing.app")
    app = app_module.create_app()

    return TestClient(app), data_dir, storage_dir


def test_post_projects_creates_persistence_record_and_project_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, data_dir, storage_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        response = test_client.post(
            "/projects",
            json={
                "name": "demo",
                "chapter_count": 3,
                "theme_notes": "city mystery",
            },
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["status"] == "created"
        assert payload["project_id"]

        project_id = payload["project_id"]
        project_dir = data_dir / "projects" / project_id

        assert project_dir.is_dir()
        assert (project_dir / "project.json").is_file()
        assert (project_dir / "story_bible_v1.md").is_file()
        assert (project_dir / "world" / "rules_v1.yaml").is_file()
        assert (project_dir / "outlines" / "master_outline_v1.md").is_file()

        project_json = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        assert project_json["id"] == project_id
        assert project_json["chapter_count"] == 3

        list_response = test_client.get("/projects")
        assert list_response.status_code == 200
        list_payload = list_response.json()
        assert len(list_payload) == 1
        assert list_payload[0]["id"] == project_id
        assert list_payload[0]["name"] == "demo"

        detail_response = test_client.get(f"/projects/{project_id}")
        assert detail_response.status_code == 200
        assert detail_response.json()["id"] == project_id

    assert (storage_dir / "auto_writing.db").is_file()


def test_post_projects_rejects_non_positive_chapter_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, data_dir, storage_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        for invalid_count in (0, -1):
            response = test_client.post(
                "/projects",
                json={
                    "name": "invalid",
                    "chapter_count": invalid_count,
                },
            )
            assert response.status_code == 422

    assert not (data_dir / "projects").exists()
    assert (storage_dir / "auto_writing.db").is_file()
