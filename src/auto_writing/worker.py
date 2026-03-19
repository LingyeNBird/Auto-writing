from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from auto_writing.config import RuntimeConfig
from auto_writing.llm import LLMClient, build_llm_client
from auto_writing.planner import PlannerService
from auto_writing.project.service import ProjectService
from auto_writing.workflow.chapter_pipeline import ChapterWorkflowResult, SingleChapterWorkflowPipeline
from auto_writing.workflow.service import WorkflowStateService
from auto_writing.workflow.states import (
    ChapterRunState,
    NovelRunState,
    is_novel_run_terminal,
    next_novel_run_state,
)


config_module = __import__("auto_writing.config", fromlist=["get_runtime_config"])
get_runtime_config = getattr(config_module, "get_runtime_config")


class WorkflowWorker:
    def __init__(
        self,
        workflow_service: WorkflowStateService,
        *,
        runtime_config: RuntimeConfig | None = None,
        project_service: ProjectService | None = None,
        llm_client: LLMClient | None = None,
        chapter_pipeline: SingleChapterWorkflowPipeline | None = None,
        planner_service: PlannerService | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 30,
    ) -> None:
        self._workflow_service = workflow_service
        self._project_service = project_service
        self._worker_id = worker_id or f"worker-{uuid4()}"
        self._lease_seconds = lease_seconds
        self._planner_service: PlannerService | None = planner_service
        self._chapter_pipeline: SingleChapterWorkflowPipeline | None = chapter_pipeline
        if self._planner_service is None and runtime_config and llm_client:
            self._planner_service = PlannerService(
                runtime_config=runtime_config,
                llm_client=llm_client,
            )
        if self._chapter_pipeline is None and runtime_config and project_service and llm_client:
            self._chapter_pipeline = SingleChapterWorkflowPipeline(
                runtime_config=runtime_config,
                workflow_service=workflow_service,
                project_service=project_service,
                llm_client=llm_client,
            )

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def run_once(
        self,
        *,
        max_steps_per_run: int | None = None,
        now: datetime | None = None,
        release_claim: bool = True,
    ) -> str | None:
        claim_time = now or datetime.now(tz=UTC)
        claimed = self._workflow_service.claim_next_novel_run(
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            now=claim_time,
        )
        if claimed is None:
            return None

        try:
            self._process_claimed_run_safely(
                claimed.id,
                max_steps=max_steps_per_run,
                now=now,
            )
            return claimed.id
        finally:
            if release_claim:
                _ = self._workflow_service.release_novel_run_claim(
                    novel_run_id=claimed.id,
                    worker_id=self._worker_id,
                    now=self._current_time(now),
                )

    def run_for_novel_run(
        self,
        novel_run_id: str,
        *,
        max_steps_per_run: int | None = None,
        now: datetime | None = None,
        release_claim: bool = True,
    ) -> bool:
        claim_time = now or datetime.now(tz=UTC)
        claimed = self._workflow_service.claim_novel_run_by_id(
            novel_run_id=novel_run_id,
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            now=claim_time,
        )
        if claimed is None:
            return False

        try:
            self._process_claimed_run_safely(
                claimed.id,
                max_steps=max_steps_per_run,
                now=now,
            )
            return True
        finally:
            if release_claim:
                _ = self._workflow_service.release_novel_run_claim(
                    novel_run_id=claimed.id,
                    worker_id=self._worker_id,
                    now=self._current_time(now),
                )

    def _process_claimed_run_safely(
        self,
        novel_run_id: str,
        *,
        max_steps: int | None,
        now: datetime | None,
    ) -> None:
        try:
            self._process_claimed_run(
                novel_run_id,
                max_steps=max_steps,
                now=now,
            )
        except Exception as exc:
            self._mark_run_failed_after_exception(novel_run_id=novel_run_id, error=exc)

    @staticmethod
    def _current_time(reference_time: datetime | None) -> datetime:
        return reference_time or datetime.now(tz=UTC)

    def _heartbeat_claim(self, *, novel_run_id: str, reference_time: datetime | None) -> bool:
        return self._workflow_service.heartbeat_novel_run_claim(
            novel_run_id=novel_run_id,
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            now=self._current_time(reference_time),
        )

    def _mark_active_chapter_run_failed_after_exception(
        self,
        *,
        novel_run_id: str,
        error: Exception,
    ) -> None:
        chapter_run = self._workflow_service.get_chapter_run_for_index(
            novel_run_id=novel_run_id,
            chapter_index=1,
        )
        if chapter_run is None:
            return
        if chapter_run.status in {ChapterRunState.LOCKED.value, ChapterRunState.FAILED.value}:
            return

        error_message = f"{error.__class__.__name__}: {error}".strip()
        try:
            _ = self._workflow_service.transition_chapter_run(
                chapter_run_id=chapter_run.id,
                to_state=ChapterRunState.FAILED,
                triggered_by=f"worker:{self._worker_id}",
                input_summary="unhandled chapter pipeline exception",
                output_summary=error_message,
            )
        except Exception:
            return

    def _run_chapter_pipeline(self, *, novel_run_id: str) -> ChapterWorkflowResult:
        if self._chapter_pipeline is None:
            raise RuntimeError("Chapter pipeline is not configured for CHAPTERS_RUNNING")
        try:
            return self._chapter_pipeline.run(
                novel_run_id=novel_run_id,
                triggered_by=f"worker:{self._worker_id}",
            )
        except Exception as exc:
            self._mark_active_chapter_run_failed_after_exception(
                novel_run_id=novel_run_id,
                error=exc,
            )
            raise

    def _mark_run_failed_after_exception(self, *, novel_run_id: str, error: Exception) -> None:
        current = self._workflow_service.get_novel_run(novel_run_id)
        if current is None:
            return
        if is_novel_run_terminal(current.status):
            return

        error_message = f"{error.__class__.__name__}: {error}".strip()
        try:
            self._workflow_service.transition_novel_run(
                novel_run_id=novel_run_id,
                to_state=NovelRunState.FAILED,
                triggered_by=f"worker:{self._worker_id}",
                input_summary="unhandled pipeline exception",
                output_summary=error_message,
            )
        except Exception:
            return

    def _process_claimed_run(
        self,
        novel_run_id: str,
        *,
        max_steps: int | None,
        now: datetime | None,
    ) -> None:
        processed_steps = 0
        while max_steps is None or processed_steps < max_steps:
            current = self._workflow_service.get_novel_run(novel_run_id)
            if current is None:
                return

            checkpoint_state = NovelRunState(current.checkpoint_state)
            if checkpoint_state == NovelRunState.CHAPTERS_RUNNING:
                if not self._heartbeat_claim(novel_run_id=novel_run_id, reference_time=now):
                    return

                if self._chapter_pipeline is not None:
                    chapter_result: ChapterWorkflowResult = self._run_chapter_pipeline(
                        novel_run_id=novel_run_id
                    )
                    if not chapter_result.success:
                        _ = self._workflow_service.transition_novel_run(
                            novel_run_id=novel_run_id,
                            to_state=NovelRunState.FAILED,
                            triggered_by=f"worker:{self._worker_id}",
                            input_summary=f"chapter_run_id={chapter_result.chapter_run_id}",
                            output_summary=(
                                chapter_result.error_message
                                or f"chapter pipeline failed at {chapter_result.failed_stage or 'unknown'}"
                            ),
                        )
                        return
                    output_summary = f"chapter_run_id={chapter_result.chapter_run_id}"
                else:
                    output_summary = f"checkpoint={checkpoint_state.value}"

                if not self._heartbeat_claim(novel_run_id=novel_run_id, reference_time=now):
                    return

                target_state = next_novel_run_state(checkpoint_state)
                if target_state is None:
                    return

                _ = self._workflow_service.transition_novel_run(
                    novel_run_id=novel_run_id,
                    to_state=target_state,
                    triggered_by=f"worker:{self._worker_id}",
                    input_summary=f"checkpoint={checkpoint_state.value}",
                    output_summary=output_summary,
                )
                processed_steps += 1
                continue

            target_state = next_novel_run_state(checkpoint_state)
            if target_state is None:
                return

            if not self._heartbeat_claim(novel_run_id=novel_run_id, reference_time=now):
                return

            output_summary = f"checkpoint={target_state.value}"
            if self._planner_service is not None and self._project_service is not None:
                project = self._project_service.get_project(current.project_id)
                if project is None:
                    raise ValueError(f"Project not found for run: {current.project_id}")
                planning_result = self._planner_service.prepare_for_transition(
                    project=project,
                    target_state=target_state,
                )
                if planning_result.output_summary:
                    output_summary = planning_result.output_summary

                if not self._heartbeat_claim(novel_run_id=novel_run_id, reference_time=now):
                    return

            _ = self._workflow_service.transition_novel_run(
                novel_run_id=novel_run_id,
                to_state=target_state,
                triggered_by=f"worker:{self._worker_id}",
                input_summary=f"checkpoint={checkpoint_state.value}",
                output_summary=output_summary,
            )
            processed_steps += 1

            if target_state == NovelRunState.CHAPTERS_RUNNING and self._chapter_pipeline is not None:
                if not self._heartbeat_claim(novel_run_id=novel_run_id, reference_time=now):
                    return

                chapter_result: ChapterWorkflowResult = self._run_chapter_pipeline(
                    novel_run_id=novel_run_id
                )
                if not chapter_result.success:
                    _ = self._workflow_service.transition_novel_run(
                        novel_run_id=novel_run_id,
                        to_state=NovelRunState.FAILED,
                        triggered_by=f"worker:{self._worker_id}",
                        input_summary=f"chapter_run_id={chapter_result.chapter_run_id}",
                        output_summary=(
                            chapter_result.error_message
                            or f"chapter pipeline failed at {chapter_result.failed_stage or 'unknown'}"
                        ),
                    )
                    return

                if not self._heartbeat_claim(novel_run_id=novel_run_id, reference_time=now):
                    return


def run_worker() -> None:
    runtime_config = get_runtime_config()
    db_path = Path(runtime_config.storage_dir) / "auto_writing.db"
    if not db_path.is_file():
        return None

    workflow_service = WorkflowStateService(runtime_config)
    project_service = ProjectService(runtime_config)
    worker = WorkflowWorker(
        workflow_service,
        runtime_config=runtime_config,
        project_service=project_service,
        llm_client=build_llm_client(),
    )
    _ = worker.run_once()
    return None
