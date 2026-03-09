from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    data_dir: Path
    logs_dir: Path
    storage_dir: Path
    log_level: str
    app_port: int


def _read_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_runtime_config(env: Mapping[str, str] | None = None) -> RuntimeConfig:
    source = os.environ if env is None else env
    return RuntimeConfig(
        data_dir=Path(source.get("AUTO_WRITING_DATA_DIR", "/app/data")),
        logs_dir=Path(source.get("AUTO_WRITING_LOGS_DIR", "/app/logs")),
        storage_dir=Path(source.get("AUTO_WRITING_STORAGE_DIR", "/app/storage")),
        log_level=source.get("AUTO_WRITING_LOG_LEVEL", "INFO"),
        app_port=_read_int(source.get("AUTO_WRITING_APP_PORT"), default=8000),
    )
