from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from auto_writing.config import RuntimeConfig
from auto_writing.llm import LLMClient, build_llm_client
from auto_writing.project.service import ProjectService
from auto_writing.workflow.chapter_pipeline import SingleChapterWorkflowPipeline
from auto_writing.workflow.service import WorkflowStateService
from auto_writing.workflow.states import NovelRunState, is_novel_run_terminal, next_novel_run_state


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
        worker_id: str | None = None,
        lease_seconds: int = 30,
    ) -> None:
        self._workflow_service = workflow_service
        self._worker_id = worker_id or f"worker-{uuid4()}"
        self._lease_seconds = lease_seconds
        self._chapter_pipeline: SingleChapterWorkflowPipeline | None = chapter_pipeline
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
                now=claim_time,
            )
            return claimed.id
        finally:
            if release_claim:
                self._workflow_service.release_novel_run_claim(
                    novel_run_id=claimed.id,
                    worker_id=self._worker_id,
                    now=claim_time,
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
                now=claim_time,
            )
            return True
        finally:
            if release_claim:
                self._workflow_service.release_novel_run_claim(
                    novel_run_id=claimed.id,
                    worker_id=self._worker_id,
                    now=claim_time,
                )

    def _process_claimed_run_safely(
        self,
        novel_run_id: str,
        *,
        max_steps: int | None,
        now: datetime,
    ) -> None:
        try:
            self._process_claimed_run(
                novel_run_id,
                max_steps=max_steps,
                now=now,
            )
        except Exception as exc:
            self._mark_run_failed_after_exception(novel_run_id=novel_run_id, error=exc)

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
        now: datetime,
    ) -> None:
        processed_steps = 0
        while max_steps is None or processed_steps < max_steps:
            current = self._workflow_service.get_novel_run(novel_run_id)
            if current is None:
                return

            checkpoint_state = NovelRunState(current.checkpoint_state)
            target_state = next_novel_run_state(checkpoint_state)
            if target_state is None:
                return

            lease_ok = self._workflow_service.heartbeat_novel_run_claim(
                novel_run_id=novel_run_id,
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                now=now,
            )
            if not lease_ok:
                return

            self._workflow_service.transition_novel_run(
                novel_run_id=novel_run_id,
                to_state=target_state,
                triggered_by=f"worker:{self._worker_id}",
                input_summary=f"checkpoint={checkpoint_state.value}",
                output_summary=f"checkpoint={target_state.value}",
            )
            processed_steps += 1

            if target_state == NovelRunState.CHAPTERS_RUNNING and self._chapter_pipeline is not None:
                chapter_result = self._chapter_pipeline.run(
                    novel_run_id=novel_run_id,
                    triggered_by=f"worker:{self._worker_id}",
                )
                if not chapter_result.success:
                    self._workflow_service.transition_novel_run(
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
