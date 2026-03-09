from __future__ import annotations

import importlib
from pathlib import Path

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


def test_get_projects_new_returns_html_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        response = test_client.get("/projects/new")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Auto Writing Console / New Project" in response.text
    assert 'form method="post" action="/projects/new"' in response.text


def test_get_projects_new_uses_jinja_template_from_project_root_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime-root"
    template_file = runtime_root / "templates" / "projects" / "new.html"
    template_file.parent.mkdir(parents=True)
    _ = template_file.write_text(
        """<!doctype html>
<html lang=\"en\">
<body>
{% if error %}
<p>fallback-error</p>
{% else %}
<p>fallback-jinja-ok</p>
{% endif %}
<form method=\"post\" action=\"/projects/new\">
  <input type=\"text\" name=\"name\" value=\"[%= name =%]\">
</form>
</body>
</html>
""",
        encoding="utf-8",
    )

    isolated_cwd = tmp_path / "isolated-cwd"
    isolated_cwd.mkdir()
    monkeypatch.chdir(isolated_cwd)
    monkeypatch.setenv("AUTO_WRITING_PROJECT_ROOT", str(runtime_root))

    client, _data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        response = test_client.get("/projects/new")

    assert response.status_code == 200
    assert "fallback-jinja-ok" in response.text
    assert "{% if error %}" not in response.text
    assert 'value=""' in response.text


def test_project_create_flow_redirects_to_detail_console_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        create_response = test_client.post(
            "/projects/new",
            data={
                "name": "console-demo",
                "chapter_count": "2",
                "theme_notes": "minimal console visibility",
            },
            follow_redirects=False,
        )

        assert create_response.status_code == 303
        location = create_response.headers["location"]
        assert location.startswith("http://testserver/projects/")
        project_id = location.rsplit("/", 1)[-1]

        detail_response = test_client.get(
            f"/projects/{project_id}",
            headers={"accept": "text/html"},
        )
        assert detail_response.status_code == 200
        assert "Project Console / console-demo" in detail_response.text
        assert f'hx-get="/projects/{project_id}/console/status"' in detail_response.text
        assert f'hx-get="/projects/{project_id}/console/logs"' in detail_response.text

        status_fragment = test_client.get(f"/projects/{project_id}/console/status")
        assert status_fragment.status_code == 200
        assert "Run Status" in status_fragment.text
        assert "No run yet" in status_fragment.text

        logs_fragment = test_client.get(f"/projects/{project_id}/console/logs")
        assert logs_fragment.status_code == 200
        assert "Recent Logs" in logs_fragment.text
        assert "No logs yet" in logs_fragment.text


def test_detail_console_shows_stage_chapter_and_logs_for_happy_path_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        project_response = test_client.post(
            "/projects",
            json={
                "name": "status-visibility",
                "chapter_count": 1,
            },
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["project_id"]

        run_response = test_client.post(f"/projects/{project_id}/runs")
        assert run_response.status_code == 202
        run_id = run_response.json()["novel_run_id"]

        detail_response = test_client.get(
            f"/projects/{project_id}",
            headers={"accept": "text/html"},
        )
        assert detail_response.status_code == 200
        assert "Project Console / status-visibility" in detail_response.text

        status_fragment = test_client.get(f"/projects/{project_id}/console/status?run_id={run_id}")
        assert status_fragment.status_code == 200
        assert "status:</strong> FINALIZED" in status_fragment.text
        assert "checkpoint_state:</strong> FINALIZED" in status_fragment.text
        assert "<td>LOCKED</td>" in status_fragment.text

        logs_fragment = test_client.get(f"/projects/{project_id}/console/logs?run_id={run_id}")
        assert logs_fragment.status_code == 200
        assert "Recent Logs" in logs_fragment.text
        assert "FINALIZED" in logs_fragment.text


def test_detail_console_shows_failure_reason_and_failed_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, data_dir = _build_client(tmp_path, monkeypatch)

    with client as test_client:
        project_response = test_client.post(
            "/projects",
            json={
                "name": "failed-visibility",
                "chapter_count": 1,
            },
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["project_id"]

        (data_dir / "projects" / project_id / "canon" / "context.json").unlink()

        run_response = test_client.post(f"/projects/{project_id}/runs")
        assert run_response.status_code == 202
        run_id = run_response.json()["novel_run_id"]

        status_fragment = test_client.get(f"/projects/{project_id}/console/status?run_id={run_id}")
        assert status_fragment.status_code == 200
        assert "status:</strong> FAILED" in status_fragment.text
        assert "failure_reason:" in status_fragment.text
        assert "Missing required context data" in status_fragment.text

        logs_fragment = test_client.get(f"/projects/{project_id}/console/logs?run_id={run_id}")
        assert logs_fragment.status_code == 200
        assert "FAILED" in logs_fragment.text
