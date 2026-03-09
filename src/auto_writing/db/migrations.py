# pyright: reportMissingImports=false
from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path

from alembic.config import Config

from auto_writing.config import RuntimeConfig


alembic_module = __import__("alembic", fromlist=["command"])
command = getattr(alembic_module, "command")
MIGRATION_LOCK_FILE_NAME = ".alembic-upgrade.lock"


def _default_search_roots() -> list[Path]:
    module_path = Path(__file__).resolve()
    candidates = [
        Path.cwd(),
        Path("/app"),
        module_path.parents[3],
        module_path.parents[2],
        module_path.parents[1],
    ]

    root_from_env = os.getenv("AUTO_WRITING_PROJECT_ROOT")
    if root_from_env:
        candidates.insert(0, Path(root_from_env))

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _resolve_alembic_paths(search_roots: Sequence[Path] | None = None) -> tuple[Path, Path]:
    roots = list(search_roots or _default_search_roots())
    checked: list[str] = []
    for root in roots:
        resolved_root = root.resolve()
        ini_path = resolved_root / "alembic.ini"
        script_path = resolved_root / "alembic"
        checked.append(str(resolved_root))
        if ini_path.is_file() and script_path.is_dir():
            return ini_path, script_path

    raise FileNotFoundError(
        "Alembic assets not found. Expected both 'alembic.ini' and 'alembic/' under one of: "
        + ", ".join(checked)
    )


def create_alembic_config(
    database_url: str | None = None,
    *,
    search_roots: Sequence[Path] | None = None,
) -> Config:
    ini_path, script_path = _resolve_alembic_paths(search_roots)
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(script_path))

    if database_url is not None:
        config.set_main_option("sqlalchemy.url", database_url)

    return config


def runtime_database_url(runtime_config: RuntimeConfig) -> str:
    db_path = runtime_config.storage_dir / "auto_writing.db"
    return f"sqlite+pysqlite:///{db_path}"


def upgrade_database_to_head(database_url: str) -> None:
    alembic_config = create_alembic_config(database_url)
    command.upgrade(alembic_config, "head")


@contextmanager
def _migration_lock(lock_file_path: Path):
    lock_file_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_file_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def upgrade_runtime_database(runtime_config: RuntimeConfig) -> None:
    runtime_config.storage_dir.mkdir(parents=True, exist_ok=True)
    lock_file = runtime_config.storage_dir / MIGRATION_LOCK_FILE_NAME
    with _migration_lock(lock_file):
        upgrade_database_to_head(runtime_database_url(runtime_config))
