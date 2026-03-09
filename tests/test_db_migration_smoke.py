# pyright: reportMissingImports=false
from __future__ import annotations

import importlib
import multiprocessing
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from auto_writing.config import RuntimeConfig
from auto_writing.db.migrations import create_alembic_config
from auto_writing.db.migrations import upgrade_runtime_database


alembic_module = __import__("alembic", fromlist=["command"])
command = getattr(alembic_module, "command")


def _run_upgrade_runtime_database_in_subprocess(
    data_dir: str,
    logs_dir: str,
    storage_dir: str,
    ready_event,
) -> None:
    runtime_config = RuntimeConfig(
        data_dir=Path(data_dir),
        logs_dir=Path(logs_dir),
        storage_dir=Path(storage_dir),
        log_level="INFO",
        app_port=8000,
    )
    _ = ready_event.wait(timeout=10)
    upgrade_runtime_database(runtime_config)


def _load_table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

    return {row[0] for row in rows}


def _load_column_names(db_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()

    return {row[1] for row in rows}


def test_create_alembic_config_uses_discovered_assets_from_search_root(tmp_path: Path) -> None:
    custom_root = tmp_path / "runtime"
    (custom_root / "alembic").mkdir(parents=True)
    _ = (custom_root / "alembic.ini").write_text(
        "[alembic]\nscript_location = alembic\npath_separator = os\n",
        encoding="utf-8",
    )

    config = create_alembic_config("sqlite+pysqlite:///tmp/test.db", search_roots=[custom_root])

    config_file_name = config.config_file_name
    assert config_file_name is not None
    assert Path(config_file_name) == (custom_root / "alembic.ini")
    script_location = config.get_main_option("script_location")
    assert script_location is not None
    assert Path(script_location) == (custom_root / "alembic")
    assert config.get_main_option("sqlalchemy.url") == "sqlite+pysqlite:///tmp/test.db"


def test_create_alembic_config_raises_when_assets_missing(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"
    missing_root.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="Alembic assets not found"):
        _ = create_alembic_config(search_roots=[missing_root])


def test_upgrade_head_bootstraps_fresh_sqlite_db(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.sqlite3"
    database_url = f"sqlite+pysqlite:///{db_path}"
    alembic_config = create_alembic_config(database_url)

    command.upgrade(alembic_config, "head")

    assert _load_table_names(db_path) == {
        "alembic_version",
        "chapter_run_transitions",
        "chapter_runs",
        "novel_runs",
        "novel_run_transitions",
        "projects",
    }
    assert _load_column_names(db_path, "novel_runs") >= {
        "id",
        "project_id",
        "status",
        "checkpoint_state",
        "claimed_by",
        "lease_expires_at",
        "lease_heartbeat_at",
        "created_at",
        "updated_at",
    }


def test_startup_upgrades_existing_legacy_db_before_run_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    storage_dir = tmp_path / "storage"
    db_path = storage_dir / "auto_writing.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    alembic_config = create_alembic_config(database_url)

    command.upgrade(alembic_config, "20260308_0003")
    assert "project_id" not in _load_column_names(db_path, "novel_runs")

    monkeypatch.setenv("AUTO_WRITING_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AUTO_WRITING_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("AUTO_WRITING_STORAGE_DIR", str(storage_dir))

    app_module = importlib.import_module("auto_writing.app")
    app_module = importlib.reload(app_module)
    app = app_module.create_app()

    with TestClient(app) as test_client:
        project_response = test_client.post(
            "/projects",
            json={"name": "legacy-db", "chapter_count": 1},
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["project_id"]

        run_response = test_client.post(f"/projects/{project_id}/runs")
        assert run_response.status_code == 202

    assert "project_id" in _load_column_names(db_path, "novel_runs")


def test_concurrent_startup_upgrade_handles_legacy_projects_table(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    storage_dir = tmp_path / "storage"
    db_path = storage_dir / "auto_writing.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    alembic_config = create_alembic_config(database_url)

    command.upgrade(alembic_config, "20260308_0001")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE projects (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                chapter_count INTEGER NOT NULL,
                theme_notes TEXT,
                status VARCHAR(32) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )

    context = multiprocessing.get_context("spawn")
    ready_event = context.Event()
    processes = [
        context.Process(
            target=_run_upgrade_runtime_database_in_subprocess,
            args=(str(data_dir), str(logs_dir), str(storage_dir), ready_event),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()

    ready_event.set()

    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    with sqlite3.connect(db_path) as connection:
        revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert revision_row is not None
    assert revision_row[0] == "20260308_0004"
    assert "project_id" in _load_column_names(db_path, "novel_runs")


def test_upgrade_head_tolerates_precreated_transition_tables_and_novel_run_columns(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    database_url = f"sqlite+pysqlite:///{db_path}"
    alembic_config = create_alembic_config(database_url)

    command.upgrade(alembic_config, "20260308_0002")

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE novel_run_transitions (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                novel_run_id VARCHAR(36) NOT NULL,
                from_status VARCHAR(32) NOT NULL,
                to_status VARCHAR(32) NOT NULL,
                triggered_by VARCHAR(64) NOT NULL,
                input_summary TEXT,
                output_summary TEXT,
                success BOOLEAN NOT NULL,
                error_message TEXT,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(novel_run_id) REFERENCES novel_runs(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE chapter_run_transitions (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                chapter_run_id VARCHAR(36) NOT NULL,
                from_status VARCHAR(32) NOT NULL,
                to_status VARCHAR(32) NOT NULL,
                triggered_by VARCHAR(64) NOT NULL,
                input_summary TEXT,
                output_summary TEXT,
                success BOOLEAN NOT NULL,
                error_message TEXT,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(chapter_run_id) REFERENCES chapter_runs(id) ON DELETE CASCADE
            )
            """
        )

        connection.execute("ALTER TABLE novel_runs ADD COLUMN project_id VARCHAR(36)")
        connection.execute("ALTER TABLE novel_runs ADD COLUMN checkpoint_state VARCHAR(32)")
        connection.execute("ALTER TABLE novel_runs ADD COLUMN claimed_by VARCHAR(128)")
        connection.execute("ALTER TABLE novel_runs ADD COLUMN lease_expires_at DATETIME")
        connection.execute("ALTER TABLE novel_runs ADD COLUMN lease_heartbeat_at DATETIME")

        connection.execute("CREATE INDEX ix_novel_runs_project_id ON novel_runs (project_id)")
        connection.execute(
            """
            CREATE UNIQUE INDEX uq_novel_runs_project_active
            ON novel_runs (project_id)
            WHERE project_id IS NOT NULL AND status NOT IN ('FINALIZED', 'FAILED')
            """
        )

    command.upgrade(alembic_config, "head")

    with sqlite3.connect(db_path) as connection:
        revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert revision_row is not None
    assert revision_row[0] == "20260308_0004"
    assert _load_table_names(db_path) >= {"novel_run_transitions", "chapter_run_transitions"}
    assert _load_column_names(db_path, "novel_runs") >= {
        "project_id",
        "checkpoint_state",
        "claimed_by",
        "lease_expires_at",
        "lease_heartbeat_at",
    }
