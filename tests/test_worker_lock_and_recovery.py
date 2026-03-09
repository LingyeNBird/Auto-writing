# pyright: reportMissingImports=false
from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from auto_writing.config import RuntimeConfig
from auto_writing.llm import FakeLLMClient
from auto_writing.project.service import ProjectService
from auto_writing.worker import WorkflowWorker
from auto_writing.workflow.service import WorkflowStateService
from auto_writing.workflow.states import ChapterRunState, NovelRunState
import pytest


def _build_runtime_config(tmp_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        storage_dir=tmp_path / "storage",
        log_level="INFO",
        app_port=8000,
    )


def _create_project_id(runtime_config: RuntimeConfig) -> str:
    project = ProjectService(runtime_config).create_project(
        name="worker-test",
        chapter_count=1,
        theme_notes=None,
    )
    return project.id


def _non_failed_states() -> list[str]:
    return [
        NovelRunState.INIT.value,
        NovelRunState.INPUT_NORMALIZED.value,
        NovelRunState.BIBLE_READY.value,
        NovelRunState.CHARACTERS_READY.value,
        NovelRunState.MASTER_OUTLINE_READY.value,
        NovelRunState.CHAPTERS_RUNNING.value,
        NovelRunState.GLOBAL_REVIEW.value,
        NovelRunState.FINALIZED.value,
    ]


def test_trigger_novel_run_reuses_existing_active_run_for_same_project(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_id = _create_project_id(runtime_config)
    workflow_service = WorkflowStateService(runtime_config)

    first_run = workflow_service.trigger_novel_run(project_id=project_id, triggered_by="api")
    second_run = workflow_service.trigger_novel_run(project_id=project_id, triggered_by="api")

    assert first_run.id == second_run.id

    active = workflow_service.get_active_novel_run(project_id)
    assert active is not None
    assert active.id == first_run.id

    history = workflow_service.list_novel_run_transitions(first_run.id)
    assert len(history) == 1
    assert history[0].to_status == NovelRunState.INIT.value


def test_worker_restart_recovers_from_latest_checkpoint_boundary(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_id = _create_project_id(runtime_config)
    workflow_service = WorkflowStateService(runtime_config)
    novel_run = workflow_service.trigger_novel_run(project_id=project_id, triggered_by="api")

    first_tick = datetime(2026, 3, 8, 12, 0, 0, tzinfo=UTC)
    worker_a = WorkflowWorker(workflow_service, worker_id="worker-a", lease_seconds=30)
    worker_b = WorkflowWorker(workflow_service, worker_id="worker-b", lease_seconds=30)

    run_id = worker_a.run_once(max_steps_per_run=2, now=first_tick, release_claim=False)
    assert run_id == novel_run.id

    stalled = worker_b.run_once(now=first_tick)
    assert stalled is None

    recovered = worker_b.run_once(now=first_tick + timedelta(seconds=31))
    assert recovered == novel_run.id

    refreshed = workflow_service.get_novel_run(novel_run.id)
    assert refreshed is not None
    assert refreshed.status == NovelRunState.FINALIZED.value
    assert refreshed.checkpoint_state == NovelRunState.FINALIZED.value
    assert refreshed.claimed_by is None

    history = workflow_service.list_novel_run_transitions(novel_run.id)
    successful_states = [entry.to_status for entry in history if entry.success]
    assert successful_states == _non_failed_states()


def test_running_worker_again_after_completion_has_no_duplicate_step_effects(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_id = _create_project_id(runtime_config)
    workflow_service = WorkflowStateService(runtime_config)
    novel_run = workflow_service.trigger_novel_run(project_id=project_id, triggered_by="api")

    worker = WorkflowWorker(workflow_service, worker_id="worker-a", lease_seconds=30)
    first_tick = datetime(2026, 3, 8, 12, 0, 0, tzinfo=UTC)
    second_tick = datetime(2026, 3, 8, 12, 5, 0, tzinfo=UTC)

    first_processed = worker.run_once(now=first_tick)
    assert first_processed == novel_run.id
    first_history = workflow_service.list_novel_run_transitions(novel_run.id)

    second_processed = worker.run_once(now=second_tick)
    assert second_processed is None
    second_history = workflow_service.list_novel_run_transitions(novel_run.id)

    assert len(first_history) == len(second_history)
    assert [entry.to_status for entry in second_history if entry.success] == _non_failed_states()


def test_run_worker_entrypoint_processes_pending_run_with_real_runtime_wiring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_id = _create_project_id(runtime_config)
    workflow_service = WorkflowStateService(runtime_config)
    novel_run = workflow_service.trigger_novel_run(project_id=project_id, triggered_by="worker-entrypoint-test")

    worker_module = importlib.import_module("auto_writing.worker")
    monkeypatch.setattr(worker_module, "get_runtime_config", lambda: runtime_config)
    monkeypatch.setattr(worker_module, "build_llm_client", lambda: FakeLLMClient())

    worker_module.run_worker()

    refreshed = workflow_service.get_novel_run(novel_run.id)
    assert refreshed is not None
    assert refreshed.status == NovelRunState.FINALIZED.value
    assert refreshed.checkpoint_state == NovelRunState.FINALIZED.value
    assert refreshed.claimed_by is None

    chapter_run = workflow_service.get_chapter_run_for_index(novel_run_id=novel_run.id, chapter_index=1)
    assert chapter_run is not None
    assert chapter_run.status == ChapterRunState.LOCKED.value

    chapter_dir = runtime_config.data_dir / "projects" / project_id / "chapters" / "chapter_001"
    assert (chapter_dir / "draft_v1.md").is_file()
    assert (chapter_dir / "summary_v1.md").is_file()
    assert (chapter_dir / "facts_v1.json").is_file()
    assert (chapter_dir / "review_v1.json").is_file()
    assert (chapter_dir / "draft_v2.md").is_file()


def test_worker_recovery_after_checkpoint_boundary_keeps_locked_artifacts_immutable(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_service = ProjectService(runtime_config)
    project_id = _create_project_id(runtime_config)
    workflow_service = WorkflowStateService(runtime_config)
    novel_run = workflow_service.trigger_novel_run(project_id=project_id, triggered_by="api")

    worker_a = WorkflowWorker(
        workflow_service,
        runtime_config=runtime_config,
        project_service=project_service,
        llm_client=FakeLLMClient(),
        worker_id="worker-a",
        lease_seconds=30,
    )
    worker_b = WorkflowWorker(
        workflow_service,
        runtime_config=runtime_config,
        project_service=project_service,
        llm_client=FakeLLMClient(),
        worker_id="worker-b",
        lease_seconds=30,
    )

    first_tick = datetime(2026, 3, 8, 12, 0, 0, tzinfo=UTC)
    processed = worker_a.run_for_novel_run(
        novel_run.id,
        max_steps_per_run=5,
        now=first_tick,
        release_claim=False,
    )
    assert processed is True

    chapter_dir = runtime_config.data_dir / "projects" / project_id / "chapters" / "chapter_001"
    locked_files = [
        chapter_dir / "draft_v1.md",
        chapter_dir / "summary_v1.md",
        chapter_dir / "facts_v1.json",
        chapter_dir / "review_v1.json",
        chapter_dir / "draft_v2.md",
    ]
    for path in locked_files:
        assert path.is_file()
    locked_snapshot = {path.name: path.read_text(encoding="utf-8") for path in locked_files}

    chapter_run = workflow_service.get_chapter_run_for_index(novel_run_id=novel_run.id, chapter_index=1)
    assert chapter_run is not None
    assert chapter_run.status == ChapterRunState.LOCKED.value

    blocked = worker_b.run_for_novel_run(novel_run.id, now=first_tick)
    assert blocked is False

    recovered = worker_b.run_for_novel_run(novel_run.id, now=first_tick + timedelta(seconds=31))
    assert recovered is True

    refreshed = workflow_service.get_novel_run(novel_run.id)
    assert refreshed is not None
    assert refreshed.status == NovelRunState.FINALIZED.value

    for path in locked_files:
        assert path.read_text(encoding="utf-8") == locked_snapshot[path.name]

    chapter_transitions = workflow_service.list_chapter_run_transitions(chapter_run.id)
    successful_states = [entry.to_status for entry in chapter_transitions if entry.success]
    assert successful_states == [
        ChapterRunState.PLANNED.value,
        ChapterRunState.CONTEXT_PACKED.value,
        ChapterRunState.DRAFTED.value,
        ChapterRunState.SUMMARIZED.value,
        ChapterRunState.FACTS_EXTRACTED.value,
        ChapterRunState.CONTINUITY_CHECKED.value,
        ChapterRunState.REVISED.value,
        ChapterRunState.LOCKED.value,
    ]
