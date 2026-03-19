from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
import pytest


def _build_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    storage_dir = tmp_path / "storage"

    monkeypatch.setenv("AUTO_WRITING_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AUTO_WRITING_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("AUTO_WRITING_STORAGE_DIR", str(storage_dir))

    app_module = importlib.import_module("auto_writing.app")
    app = app_module.create_app()
    return TestClient(app), data_dir


def test_single_chapter_run_reaches_locked_with_versioned_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        project_response = test_client.post(
            "/projects",
            json={
                "name": "chapter-slice",
                "chapter_count": 3,
                "theme_notes": "Political conspiracy escalation",
            },
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["project_id"]

        run_response = test_client.post(f"/projects/{project_id}/runs")
        assert run_response.status_code == 202
        run_payload = run_response.json()

        run_id = run_payload["novel_run_id"]
        status_response = test_client.get(f"/runs/{run_id}")
        assert status_response.status_code == 200
        status_payload = status_response.json()

        assert status_payload["status"] == "FINALIZED"
        assert status_payload["checkpoint_state"] == "FINALIZED"
        assert len(status_payload["chapter_runs"]) == 1
        assert status_payload["chapter_runs"][0]["chapter_index"] == 1
        assert status_payload["chapter_runs"][0]["status"] == "LOCKED"

    chapter_dir = data_dir / "projects" / project_id / "chapters" / "chapter_001"
    project_dir = data_dir / "projects" / project_id
    assert (chapter_dir / "draft_v1.md").is_file()
    assert (chapter_dir / "summary_v1.md").is_file()
    assert (chapter_dir / "facts_v1.json").is_file()
    assert (chapter_dir / "review_v1.json").is_file()
    assert (chapter_dir / "draft_v2.md").is_file()
    assert (project_dir / "story_bible_v1.md").is_file()
    assert (project_dir / "world" / "rules_v1.yaml").is_file()
    assert (project_dir / "world" / "locations_v1.yaml").is_file()
    assert (project_dir / "characters" / "lead_character_v1.md").is_file()
    assert (project_dir / "outlines" / "master_outline_v1.md").is_file()
    assert (project_dir / "outlines" / "chapter_001_v1.md").is_file()
    assert (project_dir / "story_bible_v1.md").read_text(encoding="utf-8") != "# Story Bible v1\n\n"
    assert (project_dir / "world" / "rules_v1.yaml").read_text(encoding="utf-8") != "rules: []\n"
    assert (project_dir / "world" / "locations_v1.yaml").read_text(encoding="utf-8") != "locations: []\n"
    assert (project_dir / "outlines" / "master_outline_v1.md").read_text(encoding="utf-8") != "# Master Outline v1\n\n"

    canon_payload = json.loads((project_dir / "canon" / "context.json").read_text(encoding="utf-8"))
    assert canon_payload["placeholders"]["core_rule"] != "Maintain internal causality."
    assert canon_payload["role_info"][0]["name"] != "Narrator"


def test_malformed_fake_output_causes_controlled_failure_without_silent_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_WRITING_FAKE_LLM_MALFORMED_STAGES", "chapter-summary:2")
    client, data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        project_response = test_client.post(
            "/projects",
            json={
                "name": "malformed-slice",
                "chapter_count": 1,
            },
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["project_id"]

        run_response = test_client.post(f"/projects/{project_id}/runs")
        assert run_response.status_code == 202
        run_id = run_response.json()["novel_run_id"]

        status_response = test_client.get(f"/runs/{run_id}")
        assert status_response.status_code == 200
        payload = status_response.json()

        assert payload["status"] == "FAILED"
        assert payload["checkpoint_state"] == "FAILED"
        assert len(payload["chapter_runs"]) == 1
        assert payload["chapter_runs"][0]["status"] == "FAILED"

        chapter_run_id = payload["chapter_runs"][0]["chapter_run_id"]
        app_state = cast(Any, test_client.app).state
        transitions = app_state.workflow_state_service.list_chapter_run_transitions(chapter_run_id)
        assert [entry.to_status for entry in transitions if entry.success] == [
            "PLANNED",
            "CONTEXT_PACKED",
            "DRAFTED",
            "FAILED",
        ]

    chapter_dir = data_dir / "projects" / project_id / "chapters" / "chapter_001"
    assert (chapter_dir / "draft_v1.md").is_file()
    assert not (chapter_dir / "summary_v1.md").exists()
    assert not (chapter_dir / "facts_v1.json").exists()
    assert not (chapter_dir / "review_v1.json").exists()


def test_single_malformed_output_retries_and_finishes_successfully(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_WRITING_FAKE_LLM_MALFORMED_STAGES", "chapter-summary:1")
    client, _data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        project_response = test_client.post(
            "/projects",
            json={
                "name": "retry-slice",
                "chapter_count": 1,
            },
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["project_id"]

        run_response = test_client.post(f"/projects/{project_id}/runs")
        assert run_response.status_code == 202
        run_id = run_response.json()["novel_run_id"]

        status_response = test_client.get(f"/runs/{run_id}")
        assert status_response.status_code == 200
        payload = status_response.json()
        assert payload["status"] == "FINALIZED"
        assert payload["chapter_runs"][0]["status"] == "LOCKED"


def test_trigger_run_handles_pipeline_exception_as_controlled_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        project_response = test_client.post(
            "/projects",
            json={
                "name": "broken-pipeline",
                "chapter_count": 1,
            },
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["project_id"]

        broken_context = data_dir / "projects" / project_id / "canon" / "context.json"
        broken_context.unlink()

        run_response = test_client.post(f"/projects/{project_id}/runs")
        assert run_response.status_code == 202
        run_payload = run_response.json()

        assert run_payload["status"] == "FAILED"
        assert run_payload["checkpoint_state"] == "FAILED"


def test_trigger_run_processes_triggered_run_not_oldest_global_active_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        stale_project_response = test_client.post(
            "/projects",
            json={
                "name": "stale-project",
                "chapter_count": 1,
            },
        )
        assert stale_project_response.status_code == 201
        stale_project_id = stale_project_response.json()["project_id"]

        app_state = cast(Any, test_client.app).state
        stale_run = app_state.workflow_state_service.trigger_novel_run(
            project_id=stale_project_id,
            triggered_by="tests",
        )
        stale_context = data_dir / "projects" / stale_project_id / "canon" / "context.json"
        stale_context.unlink()

        fresh_project_response = test_client.post(
            "/projects",
            json={
                "name": "fresh-project",
                "chapter_count": 1,
            },
        )
        assert fresh_project_response.status_code == 201
        fresh_project_id = fresh_project_response.json()["project_id"]

        run_response = test_client.post(f"/projects/{fresh_project_id}/runs")
        assert run_response.status_code == 202
        payload = run_response.json()

        assert payload["status"] == "FINALIZED"
        assert payload["checkpoint_state"] == "FINALIZED"
        assert payload["chapter_runs"][0]["status"] == "LOCKED"

        stale_after = app_state.workflow_state_service.get_novel_run(stale_run.id)
        assert stale_after is not None
        assert stale_after.status == "INIT"


def test_duplicate_run_trigger_keeps_existing_locked_artifacts_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        project_response = test_client.post(
            "/projects",
            json={
                "name": "duplicate-trigger-idempotency",
                "chapter_count": 1,
            },
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["project_id"]

        first_run_response = test_client.post(f"/projects/{project_id}/runs")
        assert first_run_response.status_code == 202
        first_payload = first_run_response.json()
        first_run_id = first_payload["novel_run_id"]
        assert first_payload["status"] == "FINALIZED"
        assert first_payload["chapter_runs"][0]["status"] == "LOCKED"

        chapter_dir = data_dir / "projects" / project_id / "chapters" / "chapter_001"
        first_files = sorted(path for path in chapter_dir.iterdir() if path.is_file())
        first_snapshot = {path.name: path.read_text(encoding="utf-8") for path in first_files}

        app_state = cast(Any, test_client.app).state
        first_transitions = app_state.workflow_state_service.list_novel_run_transitions(first_run_id)

        second_run_response = test_client.post(f"/projects/{project_id}/runs")
        assert second_run_response.status_code == 202
        second_payload = second_run_response.json()
        second_run_id = second_payload["novel_run_id"]
        assert second_run_id == first_run_id
        assert second_payload["status"] == "FINALIZED"
        assert second_payload["chapter_runs"][0]["status"] == "LOCKED"

        second_files = sorted(path for path in chapter_dir.iterdir() if path.is_file())
        assert [path.name for path in second_files] == [path.name for path in first_files]

        for path in first_files:
            assert path.read_text(encoding="utf-8") == first_snapshot[path.name]

        second_transitions = app_state.workflow_state_service.list_novel_run_transitions(second_run_id)
        assert len(second_transitions) == len(first_transitions)
