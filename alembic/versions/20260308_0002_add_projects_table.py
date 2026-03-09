# pyright: reportMissingImports=false
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa


alembic_module = __import__("alembic", fromlist=["op"])
op = getattr(alembic_module, "op")


revision: str = "20260308_0002"
down_revision: str | None = "20260308_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "projects" not in inspector.get_table_names():
        op.create_table(
            "projects",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("chapter_count", sa.Integer(), nullable=False),
            sa.Column("theme_notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "projects" in inspector.get_table_names():
        op.drop_table("projects")
