# pyright: reportMissingImports=false
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class NovelRun(Base):
    __tablename__ = "novel_runs"
    __table_args__ = (
        Index(
            "uq_novel_runs_project_active",
            "project_id",
            unique=True,
            sqlite_where=text("project_id IS NOT NULL AND status NOT IN ('FINALIZED', 'FAILED')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    checkpoint_state: Mapped[str] = mapped_column(String(32), nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    lease_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    chapter_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    theme_notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)


class ChapterRun(Base):
    __tablename__ = "chapter_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    novel_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("novel_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_index: Mapped[int] = mapped_column(Integer(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)


class NovelRunTransition(Base):
    __tablename__ = "novel_run_transitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    novel_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("novel_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False)
    input_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)


class ChapterRunTransition(Base):
    __tablename__ = "chapter_run_transitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chapter_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chapter_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False)
    input_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
