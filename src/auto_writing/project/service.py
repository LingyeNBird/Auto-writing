# pyright: reportMissingImports=false
from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from auto_writing.config import RuntimeConfig
from auto_writing.db.models import Project


class ProjectService:
    def __init__(self, runtime_config: RuntimeConfig) -> None:
        self._runtime_config = runtime_config
        self._engine = self._build_engine(runtime_config.storage_dir)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    @staticmethod
    def _build_engine(storage_dir: Path) -> Engine:
        db_path = storage_dir / "auto_writing.db"
        return create_engine(f"sqlite+pysqlite:///{db_path}", future=True)

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        self._runtime_config.storage_dir.mkdir(parents=True, exist_ok=True)
        (self._runtime_config.data_dir / "projects").mkdir(parents=True, exist_ok=True)
        Project.metadata.create_all(self._engine)
        self._initialized = True

    def create_project(
        self,
        *,
        name: str,
        chapter_count: int,
        theme_notes: str | None,
    ) -> Project:
        self._ensure_initialized()

        now = datetime.now(tz=UTC)
        project = Project(
            id=str(uuid4()),
            name=name,
            chapter_count=chapter_count,
            theme_notes=theme_notes,
            status="created",
            created_at=now,
            updated_at=now,
        )

        with self._sessions.begin() as session:
            session.add(project)

        try:
            self._initialize_assets(project)
        except Exception:
            with self._sessions.begin() as session:
                existing = session.get(Project, project.id)
                if existing is not None:
                    session.delete(existing)

            rmtree(self._project_dir(project.id), ignore_errors=True)
            raise

        return project

    def list_projects(self) -> list[Project]:
        self._ensure_initialized()
        with self._sessions() as session:
            rows = session.scalars(select(Project).order_by(Project.created_at.desc()))
            return list(rows)

    def get_project(self, project_id: str) -> Project | None:
        self._ensure_initialized()
        with self._sessions() as session:
            return session.get(Project, project_id)

    def _project_dir(self, project_id: str) -> Path:
        return self._runtime_config.data_dir / "projects" / project_id

    def _initialize_assets(self, project: Project) -> None:
        project_dir = self._project_dir(project.id)
        project_dir.mkdir(parents=True, exist_ok=False)

        for relative_dir in ("world", "characters", "outlines", "chapters", "reports"):
            (project_dir / relative_dir).mkdir(parents=True, exist_ok=True)
        for relative_dir in ("canon", "retrieval"):
            (project_dir / relative_dir).mkdir(parents=True, exist_ok=True)

        project_payload = {
            "id": project.id,
            "name": project.name,
            "chapter_count": project.chapter_count,
            "theme_notes": project.theme_notes,
            "status": project.status,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }

        for relative_path, content in {
            "project.json": json.dumps(project_payload, indent=2) + "\n",
            "reports/continuity_report_v1.json": "{}\n",
            "canon/context.json": json.dumps(
                {
                    "placeholders": {
                        "core_rule": "Maintain internal causality.",
                    },
                    "role_info": [
                        {
                            "name": "Narrator",
                            "role": "observer",
                            "goal": "Preserve continuity anchors",
                        }
                    ],
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
            "retrieval/memory.json": json.dumps(
                {
                    "memory": [
                        "No prior chapter summary yet.",
                    ]
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
        }.items():
            _ = (project_dir / relative_path).write_text(content, encoding="utf-8")
