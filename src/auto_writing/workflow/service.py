# pyright: reportMissingImports=false
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auto_writing.config import RuntimeConfig
from auto_writing.db.models import (
    ChapterRun,
    ChapterRunTransition,
    NovelRun,
    NovelRunTransition,
    Project,
)

from .states import (
    ChapterRunState,
    InvalidStateTransitionError,
    NovelRunState,
    is_novel_run_active,
    is_novel_run_terminal,
    validate_chapter_run_transition,
    validate_novel_run_transition,
)


class WorkflowStateService:
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
        Project.metadata.create_all(self._engine)
        self._initialized = True

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=UTC)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _active_novel_run_query(project_id: str):
        return (
            select(NovelRun)
            .where(NovelRun.project_id == project_id)
            .where(NovelRun.status.notin_([NovelRunState.FINALIZED.value, NovelRunState.FAILED.value]))
            .order_by(NovelRun.created_at.desc())
        )

    @staticmethod
    def _latest_novel_run_query(project_id: str):
        return (
            select(NovelRun)
            .where(NovelRun.project_id == project_id)
            .order_by(NovelRun.updated_at.desc(), NovelRun.created_at.desc())
        )

    @staticmethod
    def _create_initial_novel_run(
        session: Session,
        *,
        project_id: str,
        triggered_by: str,
        now: datetime,
    ) -> NovelRun:
        novel_run = NovelRun(
            id=str(uuid4()),
            project_id=project_id,
            status=NovelRunState.INIT.value,
            checkpoint_state=NovelRunState.INIT.value,
            claimed_by=None,
            lease_expires_at=None,
            lease_heartbeat_at=None,
            created_at=now,
            updated_at=now,
        )
        audit_entry = NovelRunTransition(
            id=str(uuid4()),
            novel_run_id=novel_run.id,
            from_status=NovelRunState.INIT.value,
            to_status=NovelRunState.INIT.value,
            triggered_by=triggered_by,
            input_summary=None,
            output_summary=None,
            success=True,
            error_message=None,
            created_at=now,
        )
        session.add(novel_run)
        session.add(audit_entry)
        return novel_run

    def create_novel_run(self, *, project_id: str, triggered_by: str) -> NovelRun:
        self._ensure_initialized()
        now = self._now()

        with self._sessions.begin() as session:
            if session.get(Project, project_id) is None:
                raise ValueError(f"Project not found: {project_id}")
            return self._create_initial_novel_run(
                session,
                project_id=project_id,
                triggered_by=triggered_by,
                now=now,
            )

    def trigger_novel_run(self, *, project_id: str, triggered_by: str) -> NovelRun:
        self._ensure_initialized()
        now = self._now()

        try:
            with self._sessions.begin() as session:
                if session.get(Project, project_id) is None:
                    raise ValueError(f"Project not found: {project_id}")

                active_run = session.scalars(self._active_novel_run_query(project_id)).first()
                if active_run is not None:
                    return active_run

                latest_run = session.scalars(self._latest_novel_run_query(project_id)).first()
                if latest_run is not None and latest_run.status == NovelRunState.FINALIZED.value:
                    return latest_run

                return self._create_initial_novel_run(
                    session,
                    project_id=project_id,
                    triggered_by=triggered_by,
                    now=now,
                )
        except IntegrityError:
            with self._sessions() as session:
                active_run = session.scalars(self._active_novel_run_query(project_id)).first()
                if active_run is not None:
                    return active_run
            raise

    def get_active_novel_run(self, project_id: str) -> NovelRun | None:
        self._ensure_initialized()
        with self._sessions() as session:
            return session.scalars(self._active_novel_run_query(project_id)).first()

    def get_latest_novel_run(self, project_id: str) -> NovelRun | None:
        self._ensure_initialized()
        with self._sessions() as session:
            return session.scalars(
                select(NovelRun)
                .where(NovelRun.project_id == project_id)
                .order_by(NovelRun.updated_at.desc(), NovelRun.created_at.desc())
            ).first()

    def create_chapter_run(self, *, novel_run_id: str, chapter_index: int, triggered_by: str) -> ChapterRun:
        self._ensure_initialized()
        now = self._now()

        with self._sessions.begin() as session:
            chapter_run = self._create_initial_chapter_run(
                session,
                novel_run_id=novel_run_id,
                chapter_index=chapter_index,
                triggered_by=triggered_by,
                now=now,
            )

        return chapter_run

    @staticmethod
    def _create_initial_chapter_run(
        session: Session,
        *,
        novel_run_id: str,
        chapter_index: int,
        triggered_by: str,
        now: datetime,
    ) -> ChapterRun:
        chapter_run = ChapterRun(
            id=str(uuid4()),
            novel_run_id=novel_run_id,
            chapter_index=chapter_index,
            status=ChapterRunState.PLANNED.value,
            created_at=now,
            updated_at=now,
        )
        audit_entry = ChapterRunTransition(
            id=str(uuid4()),
            chapter_run_id=chapter_run.id,
            from_status=ChapterRunState.PLANNED.value,
            to_status=ChapterRunState.PLANNED.value,
            triggered_by=triggered_by,
            input_summary=None,
            output_summary=None,
            success=True,
            error_message=None,
            created_at=now,
        )
        session.add(chapter_run)
        session.add(audit_entry)
        return chapter_run

    def claim_next_novel_run(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> NovelRun | None:
        self._ensure_initialized()
        claim_time = self._as_utc(now or self._now())
        lease_expires_at = claim_time + timedelta(seconds=lease_seconds)

        with self._sessions.begin() as session:
            rows = session.scalars(
                select(NovelRun)
                .where(NovelRun.status.notin_([NovelRunState.FINALIZED.value, NovelRunState.FAILED.value]))
                .order_by(NovelRun.updated_at.asc(), NovelRun.created_at.asc())
            )
            for run in rows:
                lease_expiry = run.lease_expires_at
                lease_expired = lease_expiry is None or self._as_utc(lease_expiry) <= claim_time
                if run.claimed_by is None or run.claimed_by == worker_id or lease_expired:
                    run.claimed_by = worker_id
                    run.lease_heartbeat_at = claim_time
                    run.lease_expires_at = lease_expires_at
                    run.updated_at = claim_time
                    session.flush()
                    return run

        return None

    def claim_novel_run_by_id(
        self,
        *,
        novel_run_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> NovelRun | None:
        self._ensure_initialized()
        claim_time = self._as_utc(now or self._now())
        lease_expires_at = claim_time + timedelta(seconds=lease_seconds)

        with self._sessions.begin() as session:
            run = session.get(NovelRun, novel_run_id)
            if run is None:
                return None
            if not is_novel_run_active(run.status):
                return None

            lease_expiry = run.lease_expires_at
            lease_expired = lease_expiry is None or self._as_utc(lease_expiry) <= claim_time
            if run.claimed_by is None or run.claimed_by == worker_id or lease_expired:
                run.claimed_by = worker_id
                run.lease_heartbeat_at = claim_time
                run.lease_expires_at = lease_expires_at
                run.updated_at = claim_time
                session.flush()
                return run

            return None

    def heartbeat_novel_run_claim(
        self,
        *,
        novel_run_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        self._ensure_initialized()
        heartbeat_time = now or self._now()

        with self._sessions.begin() as session:
            run = session.get(NovelRun, novel_run_id)
            if run is None:
                return False
            if run.claimed_by != worker_id:
                return False
            if not is_novel_run_active(run.status):
                return False

            run.lease_heartbeat_at = heartbeat_time
            run.lease_expires_at = heartbeat_time + timedelta(seconds=lease_seconds)
            run.updated_at = heartbeat_time
            return True

    def release_novel_run_claim(
        self,
        *,
        novel_run_id: str,
        worker_id: str,
        now: datetime | None = None,
    ) -> bool:
        self._ensure_initialized()
        release_time = now or self._now()

        with self._sessions.begin() as session:
            run = session.get(NovelRun, novel_run_id)
            if run is None:
                return False
            if run.claimed_by != worker_id:
                return False

            run.claimed_by = None
            run.lease_expires_at = None
            run.lease_heartbeat_at = None
            run.updated_at = release_time
            return True

    def get_novel_run(self, novel_run_id: str) -> NovelRun | None:
        self._ensure_initialized()
        with self._sessions() as session:
            return session.get(NovelRun, novel_run_id)

    def get_chapter_run(self, chapter_run_id: str) -> ChapterRun | None:
        self._ensure_initialized()
        with self._sessions() as session:
            return session.get(ChapterRun, chapter_run_id)

    def get_chapter_run_for_index(self, *, novel_run_id: str, chapter_index: int) -> ChapterRun | None:
        self._ensure_initialized()
        with self._sessions() as session:
            return session.scalars(
                select(ChapterRun)
                .where(ChapterRun.novel_run_id == novel_run_id)
                .where(ChapterRun.chapter_index == chapter_index)
                .order_by(ChapterRun.created_at.asc())
            ).first()

    def get_or_create_chapter_run(
        self,
        *,
        novel_run_id: str,
        chapter_index: int,
        triggered_by: str,
    ) -> ChapterRun:
        self._ensure_initialized()
        now = self._now()
        with self._sessions.begin() as session:
            existing = session.scalars(
                select(ChapterRun)
                .where(ChapterRun.novel_run_id == novel_run_id)
                .where(ChapterRun.chapter_index == chapter_index)
                .order_by(ChapterRun.created_at.asc())
            ).first()
            if existing is not None:
                return existing

            return self._create_initial_chapter_run(
                session,
                novel_run_id=novel_run_id,
                chapter_index=chapter_index,
                triggered_by=triggered_by,
                now=now,
            )

    def list_chapter_runs(self, novel_run_id: str) -> list[ChapterRun]:
        self._ensure_initialized()
        with self._sessions() as session:
            rows = session.scalars(
                select(ChapterRun)
                .where(ChapterRun.novel_run_id == novel_run_id)
                .order_by(ChapterRun.chapter_index.asc(), ChapterRun.created_at.asc())
            )
            return list(rows)

    def list_novel_run_transitions(self, novel_run_id: str) -> list[NovelRunTransition]:
        self._ensure_initialized()
        with self._sessions() as session:
            rows = session.scalars(
                select(NovelRunTransition)
                .where(NovelRunTransition.novel_run_id == novel_run_id)
                .order_by(NovelRunTransition.created_at.asc())
            )
            return list(rows)

    def list_chapter_run_transitions(self, chapter_run_id: str) -> list[ChapterRunTransition]:
        self._ensure_initialized()
        with self._sessions() as session:
            rows = session.scalars(
                select(ChapterRunTransition)
                .where(ChapterRunTransition.chapter_run_id == chapter_run_id)
                .order_by(ChapterRunTransition.created_at.asc())
            )
            return list(rows)

    def transition_novel_run(
        self,
        *,
        novel_run_id: str,
        to_state: NovelRunState | str,
        triggered_by: str,
        input_summary: str | None = None,
        output_summary: str | None = None,
    ) -> NovelRunTransition:
        self._ensure_initialized()
        target_state = NovelRunState(to_state)
        transition: NovelRunTransition | None = None
        transition_error: InvalidStateTransitionError | None = None
        now = self._now()

        with self._sessions.begin() as session:
            novel_run = session.get(NovelRun, novel_run_id)
            if novel_run is None:
                raise ValueError(f"NovelRun not found: {novel_run_id}")

            current_state = NovelRunState(novel_run.status)
            try:
                validate_novel_run_transition(current_state, target_state)
            except InvalidStateTransitionError as exc:
                transition_error = exc
                transition = NovelRunTransition(
                    id=str(uuid4()),
                    novel_run_id=novel_run.id,
                    from_status=current_state.value,
                    to_status=target_state.value,
                    triggered_by=triggered_by,
                    input_summary=input_summary,
                    output_summary=output_summary,
                    success=False,
                    error_message=str(exc),
                    created_at=now,
                )
            else:
                novel_run.status = target_state.value
                novel_run.checkpoint_state = target_state.value
                novel_run.updated_at = now
                if is_novel_run_terminal(target_state):
                    novel_run.claimed_by = None
                    novel_run.lease_expires_at = None
                    novel_run.lease_heartbeat_at = None

                transition = NovelRunTransition(
                    id=str(uuid4()),
                    novel_run_id=novel_run.id,
                    from_status=current_state.value,
                    to_status=target_state.value,
                    triggered_by=triggered_by,
                    input_summary=input_summary,
                    output_summary=output_summary,
                    success=True,
                    error_message=None,
                    created_at=now,
                )

            session.add(transition)

        if transition_error is not None:
            raise transition_error
        return transition

    def transition_chapter_run(
        self,
        *,
        chapter_run_id: str,
        to_state: ChapterRunState | str,
        triggered_by: str,
        input_summary: str | None = None,
        output_summary: str | None = None,
    ) -> ChapterRunTransition:
        self._ensure_initialized()
        target_state = ChapterRunState(to_state)
        transition: ChapterRunTransition | None = None
        transition_error: InvalidStateTransitionError | None = None
        now = self._now()

        with self._sessions.begin() as session:
            chapter_run = session.get(ChapterRun, chapter_run_id)
            if chapter_run is None:
                raise ValueError(f"ChapterRun not found: {chapter_run_id}")

            current_state = ChapterRunState(chapter_run.status)
            try:
                validate_chapter_run_transition(current_state, target_state)
            except InvalidStateTransitionError as exc:
                transition_error = exc
                transition = ChapterRunTransition(
                    id=str(uuid4()),
                    chapter_run_id=chapter_run.id,
                    from_status=current_state.value,
                    to_status=target_state.value,
                    triggered_by=triggered_by,
                    input_summary=input_summary,
                    output_summary=output_summary,
                    success=False,
                    error_message=str(exc),
                    created_at=now,
                )
            else:
                chapter_run.status = target_state.value
                chapter_run.updated_at = now
                transition = ChapterRunTransition(
                    id=str(uuid4()),
                    chapter_run_id=chapter_run.id,
                    from_status=current_state.value,
                    to_status=target_state.value,
                    triggered_by=triggered_by,
                    input_summary=input_summary,
                    output_summary=output_summary,
                    success=True,
                    error_message=None,
                    created_at=now,
                )

            session.add(transition)

        if transition_error is not None:
            raise transition_error
        return transition
