from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib import error, request
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_compose(project_name: str, compose_files: list[Path], *args: str) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "-p", project_name]
    for compose_file in compose_files:
        command.extend(["-f", str(compose_file)])
    command.extend(args)
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def _compose_or_fail(project_name: str, compose_files: list[Path], *args: str) -> None:
    result = _run_compose(project_name, compose_files, *args)
    if result.returncode != 0:
        pytest.fail(
            "docker compose command failed: "
            f"{' '.join(args)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _request_json(method: str, url: str, payload: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        url=url,
        method=method,
        data=body,
        headers={
            "accept": "application/json",
            "content-type": "application/json",
        },
    )
    try:
        with request.urlopen(http_request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            return int(response.status), data
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        pytest.fail(f"HTTP {exc.code} from {url}: {details}")


def _wait_for_web(base_url: str, *, timeout_seconds: int = 180) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            status, _ = _request_json("GET", f"{base_url}/projects")
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(2)
    pytest.fail(f"Web service did not become ready within {timeout_seconds} seconds")


def test_docker_compose_restart_keeps_projects_runs_and_artifacts(tmp_path: Path) -> None:
    if os.getenv("AUTO_WRITING_RUN_DOCKER_E2E") != "1":
        pytest.skip("Set AUTO_WRITING_RUN_DOCKER_E2E=1 to run docker lifecycle persistence test")
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for docker lifecycle persistence test")

    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    storage_dir = tmp_path / "storage"
    data_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    storage_dir.mkdir(parents=True)

    host_port = _find_free_port()
    compose_file = tmp_path / "docker-compose.e2e.yml"
    compose_file.write_text(
        "\n".join(
            [
                "services:",
                "  web:",
                "    build:",
                f"      context: {ROOT}",
                "      dockerfile: Dockerfile",
                "    image: auto-writing:local",
                "    command:",
                "      - python",
                "      - -m",
                "      - uvicorn",
                "      - auto_writing.app:app",
                "      - --host",
                "      - 0.0.0.0",
                "      - --port",
                "      - \"8000\"",
                "    ports:",
                f"      - \"{host_port}:8000\"",
                "    volumes:",
                f"      - \"{data_dir}:/app/data\"",
                f"      - \"{logs_dir}:/app/logs\"",
                f"      - \"{storage_dir}:/app/storage\"",
                "  worker:",
                "    image: auto-writing:local",
                "    build:",
                f"      context: {ROOT}",
                "      dockerfile: Dockerfile",
                "    command:",
                "      - python",
                "      - -c",
                "      - |",
                "        import time",
                "        from auto_writing.worker import run_worker",
                "",
                "        while True:",
                "            run_worker()",
                "            time.sleep(5)",
                "    volumes:",
                f"      - \"{data_dir}:/app/data\"",
                f"      - \"{logs_dir}:/app/logs\"",
                f"      - \"{storage_dir}:/app/storage\"",
                "",
            ]
        ),
        encoding="utf-8",
    )

    project_name = f"auto-writing-restart-{uuid4().hex[:8]}"
    compose_files = [compose_file]
    base_url = f"http://127.0.0.1:{host_port}"

    try:
        _compose_or_fail(project_name, compose_files, "config")
        _compose_or_fail(project_name, compose_files, "up", "-d", "--build")
        _wait_for_web(base_url)

        _, project_payload = _request_json(
            "POST",
            f"{base_url}/projects",
            {
                "name": "docker-restart-persistence",
                "chapter_count": 1,
            },
        )
        project_id = str(project_payload["project_id"])

        _, run_payload = _request_json("POST", f"{base_url}/projects/{project_id}/runs")
        run_id = str(run_payload["novel_run_id"])
        assert run_payload["status"] == "FINALIZED"
        assert run_payload["checkpoint_state"] == "FINALIZED"
        chapter_runs = run_payload["chapter_runs"]
        assert isinstance(chapter_runs, list)
        assert len(chapter_runs) == 1
        first_chapter = chapter_runs[0]
        assert isinstance(first_chapter, dict)
        assert first_chapter["status"] == "LOCKED"

        chapter_dir = data_dir / "projects" / project_id / "chapters" / "chapter_001"
        tracked_files = [
            chapter_dir / "draft_v1.md",
            chapter_dir / "summary_v1.md",
            chapter_dir / "facts_v1.json",
            chapter_dir / "review_v1.json",
            chapter_dir / "draft_v2.md",
        ]
        for artifact in tracked_files:
            assert artifact.is_file()
        artifact_snapshot = {artifact.name: artifact.read_text(encoding="utf-8") for artifact in tracked_files}

        _compose_or_fail(project_name, compose_files, "down")
        _compose_or_fail(project_name, compose_files, "up", "-d")
        _wait_for_web(base_url)

        _, project_after_restart = _request_json("GET", f"{base_url}/projects/{project_id}")
        assert project_after_restart["id"] == project_id

        _, run_after_restart = _request_json("GET", f"{base_url}/runs/{run_id}")
        assert run_after_restart["novel_run_id"] == run_id
        assert run_after_restart["status"] == "FINALIZED"
        assert run_after_restart["checkpoint_state"] == "FINALIZED"
        chapter_runs_after = run_after_restart["chapter_runs"]
        assert isinstance(chapter_runs_after, list)
        assert len(chapter_runs_after) == 1
        first_chapter_after = chapter_runs_after[0]
        assert isinstance(first_chapter_after, dict)
        assert first_chapter_after["status"] == "LOCKED"

        for artifact in tracked_files:
            assert artifact.read_text(encoding="utf-8") == artifact_snapshot[artifact.name]
    finally:
        _ = _run_compose(project_name, compose_files, "down")
