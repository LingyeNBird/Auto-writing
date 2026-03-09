# pyright: reportMissingImports=false
from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

from auto_writing.db.base import Base
from auto_writing.db import models  # noqa: F401


alembic_module = __import__("alembic", fromlist=["context"])
context = getattr(alembic_module, "context")


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _ensure_sqlite_parent_exists(database_url: str) -> None:
    url = make_url(database_url)
    if url.drivername != "sqlite" and not url.drivername.startswith("sqlite+"):
        return
    if url.database is None or url.database == ":memory:":
        return

    Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section)
    if configuration is None:
        raise RuntimeError("Alembic configuration section is missing")

    database_url = configuration.get("sqlalchemy.url")
    if database_url is None:
        raise RuntimeError("sqlalchemy.url is not configured")
    _ensure_sqlite_parent_exists(database_url)

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
