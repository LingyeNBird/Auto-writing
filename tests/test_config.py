from __future__ import annotations

import importlib
from pathlib import Path


def test_runtime_config_defaults_match_container_mount_contract(monkeypatch) -> None:
    for key in (
        "AUTO_WRITING_DATA_DIR",
        "AUTO_WRITING_LOGS_DIR",
        "AUTO_WRITING_STORAGE_DIR",
        "AUTO_WRITING_LOG_LEVEL",
        "AUTO_WRITING_APP_PORT",
    ):
        monkeypatch.delenv(key, raising=False)

    config_module = importlib.import_module("auto_writing.config")
    config = config_module.get_runtime_config()

    assert config.data_dir == Path("/app/data")
    assert config.logs_dir == Path("/app/logs")
    assert config.storage_dir == Path("/app/storage")
    assert config.log_level == "INFO"
    assert config.app_port == 8000


def test_runtime_config_supports_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AUTO_WRITING_DATA_DIR", "/tmp/aw-data")
    monkeypatch.setenv("AUTO_WRITING_LOGS_DIR", "/tmp/aw-logs")
    monkeypatch.setenv("AUTO_WRITING_STORAGE_DIR", "/tmp/aw-storage")
    monkeypatch.setenv("AUTO_WRITING_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("AUTO_WRITING_APP_PORT", "9000")

    config_module = importlib.import_module("auto_writing.config")
    config = config_module.get_runtime_config()

    assert config.data_dir == Path("/tmp/aw-data")
    assert config.logs_dir == Path("/tmp/aw-logs")
    assert config.storage_dir == Path("/tmp/aw-storage")
    assert config.log_level == "DEBUG"
    assert config.app_port == 9000


def test_app_and_worker_use_the_shared_runtime_config_entrypoint(monkeypatch) -> None:
    app_module = importlib.import_module("auto_writing.app")
    worker_module = importlib.import_module("auto_writing.worker")
    config_module = importlib.import_module("auto_writing.config")
    runtime_config = config_module.get_runtime_config()
    worker_called = False

    def fake_app_config():
        return runtime_config

    def fake_worker_config():
        nonlocal worker_called
        worker_called = True
        return runtime_config

    monkeypatch.setattr(app_module, "get_runtime_config", fake_app_config)
    monkeypatch.setattr(worker_module, "get_runtime_config", fake_worker_config)

    created_app = app_module.create_app()
    worker_module.run_worker()

    assert created_app.state.runtime_config == runtime_config
    assert worker_called


def test_worker_entrypoint_does_not_invoke_runtime_migrations(monkeypatch) -> None:
    worker_module = importlib.import_module("auto_writing.worker")
    config_module = importlib.import_module("auto_writing.config")
    runtime_config = config_module.get_runtime_config()
    migration_called = False

    def fake_worker_config():
        return runtime_config

    def fake_upgrade_runtime_database(_runtime_config):
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr(worker_module, "get_runtime_config", fake_worker_config)
    monkeypatch.setattr(
        worker_module,
        "upgrade_runtime_database",
        fake_upgrade_runtime_database,
        raising=False,
    )

    worker_module.run_worker()

    assert migration_called is False
