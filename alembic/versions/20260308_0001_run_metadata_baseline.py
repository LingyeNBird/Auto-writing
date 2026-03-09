# pyright: reportMissingImports=false
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa


alembic_module = __import__("alembic", fromlist=["op"])
op = getattr(alembic_module, "op")


revision: str = "20260308_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "novel_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chapter_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("novel_run_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["novel_run_id"], ["novel_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("chapter_runs")
    op.drop_table("novel_runs")
