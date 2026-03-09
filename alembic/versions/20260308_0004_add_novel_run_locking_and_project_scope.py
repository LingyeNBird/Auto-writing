# pyright: reportMissingImports=false
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa


alembic_module = __import__("alembic", fromlist=["op"])
op = getattr(alembic_module, "op")


revision: str = "20260308_0004"
down_revision: str | None = "20260308_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {column["name"] for column in inspector.get_columns("novel_runs")}
    columns_to_add = [
        ("project_id", sa.Column("project_id", sa.String(length=36), nullable=True)),
        ("checkpoint_state", sa.Column("checkpoint_state", sa.String(length=32), nullable=True)),
        ("claimed_by", sa.Column("claimed_by", sa.String(length=128), nullable=True)),
        ("lease_expires_at", sa.Column("lease_expires_at", sa.DateTime(), nullable=True)),
        ("lease_heartbeat_at", sa.Column("lease_heartbeat_at", sa.DateTime(), nullable=True)),
    ]

    missing_columns = [column for name, column in columns_to_add if name not in columns]
    if missing_columns:
        with op.batch_alter_table("novel_runs") as batch:
            for column in missing_columns:
                batch.add_column(column)

    refreshed_columns = {column["name"] for column in inspector.get_columns("novel_runs")}
    if "checkpoint_state" in refreshed_columns:
        op.execute("UPDATE novel_runs SET checkpoint_state = status WHERE checkpoint_state IS NULL")

    index_names = {index["name"] for index in inspector.get_indexes("novel_runs") if index.get("name")}
    if "ix_novel_runs_project_id" not in index_names and "project_id" in refreshed_columns:
        op.create_index("ix_novel_runs_project_id", "novel_runs", ["project_id"], unique=False)

    if "uq_novel_runs_project_active" not in index_names and "project_id" in refreshed_columns:
        op.create_index(
            "uq_novel_runs_project_active",
            "novel_runs",
            ["project_id"],
            unique=True,
            sqlite_where=sa.text("project_id IS NOT NULL AND status NOT IN ('FINALIZED', 'FAILED')"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    index_names = {index["name"] for index in inspector.get_indexes("novel_runs") if index.get("name")}
    if "uq_novel_runs_project_active" in index_names:
        op.drop_index("uq_novel_runs_project_active", table_name="novel_runs")
    if "ix_novel_runs_project_id" in index_names:
        op.drop_index("ix_novel_runs_project_id", table_name="novel_runs")

    columns = {column["name"] for column in inspector.get_columns("novel_runs")}
    columns_to_drop = [
        "lease_heartbeat_at",
        "lease_expires_at",
        "claimed_by",
        "checkpoint_state",
        "project_id",
    ]
    existing_drop_columns = [column_name for column_name in columns_to_drop if column_name in columns]
    if existing_drop_columns:
        with op.batch_alter_table("novel_runs") as batch:
            for column_name in existing_drop_columns:
                batch.drop_column(column_name)
