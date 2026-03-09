# pyright: reportMissingImports=false
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa


alembic_module = __import__("alembic", fromlist=["op"])
op = getattr(alembic_module, "op")


revision: str = "20260308_0003"
down_revision: str | None = "20260308_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "novel_run_transitions" not in inspector.get_table_names():
        op.create_table(
            "novel_run_transitions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("novel_run_id", sa.String(length=36), nullable=False),
            sa.Column("from_status", sa.String(length=32), nullable=False),
            sa.Column("to_status", sa.String(length=32), nullable=False),
            sa.Column("triggered_by", sa.String(length=64), nullable=False),
            sa.Column("input_summary", sa.Text(), nullable=True),
            sa.Column("output_summary", sa.Text(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["novel_run_id"], ["novel_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "chapter_run_transitions" not in inspector.get_table_names():
        op.create_table(
            "chapter_run_transitions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("chapter_run_id", sa.String(length=36), nullable=False),
            sa.Column("from_status", sa.String(length=32), nullable=False),
            sa.Column("to_status", sa.String(length=32), nullable=False),
            sa.Column("triggered_by", sa.String(length=64), nullable=False),
            sa.Column("input_summary", sa.Text(), nullable=True),
            sa.Column("output_summary", sa.Text(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["chapter_run_id"], ["chapter_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "chapter_run_transitions" in inspector.get_table_names():
        op.drop_table("chapter_run_transitions")
    if "novel_run_transitions" in inspector.get_table_names():
        op.drop_table("novel_run_transitions")
