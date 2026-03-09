# pyright: reportMissingImports=false
from __future__ import annotations

from pathlib import Path
import re

import pytest

from auto_writing.config import RuntimeConfig
from auto_writing.project.service import ProjectService
from auto_writing.workflow.service import WorkflowStateService
from auto_writing.workflow.states import (
    ChapterRunState,
    InvalidStateTransitionError,
    NovelRunState,
    can_transition_chapter_run,
    can_transition_novel_run,
)


def _build_runtime_config(tmp_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        storage_dir=tmp_path / "storage",
        log_level="INFO",
        app_port=8000,
    )


def _create_project(runtime_config: RuntimeConfig) -> str:
    project = ProjectService(runtime_config).create_project(
        name="workflow-test",
        chapter_count=1,
        theme_notes=None,
    )
    return project.id


def test_novel_run_transition_rules_cover_legal_and_illegal_paths() -> None:
    legal_paths = [
        (NovelRunState.INIT, NovelRunState.INPUT_NORMALIZED),
        (NovelRunState.INPUT_NORMALIZED, NovelRunState.BIBLE_READY),
        (NovelRunState.BIBLE_READY, NovelRunState.CHARACTERS_READY),
        (NovelRunState.CHARACTERS_READY, NovelRunState.MASTER_OUTLINE_READY),
        (NovelRunState.MASTER_OUTLINE_READY, NovelRunState.CHAPTERS_RUNNING),
        (NovelRunState.CHAPTERS_RUNNING, NovelRunState.GLOBAL_REVIEW),
        (NovelRunState.GLOBAL_REVIEW, NovelRunState.FINALIZED),
    ]

    for source, target in legal_paths:
        assert can_transition_novel_run(source, target)
        assert can_transition_novel_run(source, NovelRunState.FAILED)

    assert not can_transition_novel_run(NovelRunState.INIT, NovelRunState.BIBLE_READY)
    assert not can_transition_novel_run(NovelRunState.FINALIZED, NovelRunState.FAILED)
    assert not can_transition_novel_run(NovelRunState.FAILED, NovelRunState.INPUT_NORMALIZED)


def test_chapter_run_transition_rules_cover_legal_and_illegal_paths() -> None:
    legal_paths = [
        (ChapterRunState.PLANNED, ChapterRunState.CONTEXT_PACKED),
        (ChapterRunState.CONTEXT_PACKED, ChapterRunState.DRAFTED),
        (ChapterRunState.DRAFTED, ChapterRunState.SUMMARIZED),
        (ChapterRunState.SUMMARIZED, ChapterRunState.FACTS_EXTRACTED),
        (ChapterRunState.FACTS_EXTRACTED, ChapterRunState.CONTINUITY_CHECKED),
        (ChapterRunState.CONTINUITY_CHECKED, ChapterRunState.REVISED),
        (ChapterRunState.REVISED, ChapterRunState.LOCKED),
    ]

    for source, target in legal_paths:
        assert can_transition_chapter_run(source, target)
        assert can_transition_chapter_run(source, ChapterRunState.FAILED)

    assert not can_transition_chapter_run(ChapterRunState.PLANNED, ChapterRunState.SUMMARIZED)
    assert not can_transition_chapter_run(ChapterRunState.LOCKED, ChapterRunState.REVISED)
    assert not can_transition_chapter_run(ChapterRunState.FAILED, ChapterRunState.PLANNED)


def test_transition_engine_persists_legal_transition_and_audit_entry(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_id = _create_project(runtime_config)
    service = WorkflowStateService(runtime_config)
    novel_run = service.create_novel_run(project_id=project_id, triggered_by="tests")

    transition = service.transition_novel_run(
        novel_run_id=novel_run.id,
        to_state=NovelRunState.INPUT_NORMALIZED,
        triggered_by="worker",
        input_summary="raw input prepared",
        output_summary="normalized inputs",
    )

    assert transition.success is True
    assert transition.from_status == NovelRunState.INIT.value
    assert transition.to_status == NovelRunState.INPUT_NORMALIZED.value
    assert transition.triggered_by == "worker"

    refreshed = service.get_novel_run(novel_run.id)
    assert refreshed is not None
    assert refreshed.status == NovelRunState.INPUT_NORMALIZED.value

    history = service.list_novel_run_transitions(novel_run.id)
    assert len(history) == 2
    assert history[0].success is True
    assert history[0].from_status == NovelRunState.INIT.value
    assert history[0].to_status == NovelRunState.INIT.value
    assert history[1].success is True
    assert history[1].input_summary == "raw input prepared"
    assert history[1].output_summary == "normalized inputs"


def test_transition_engine_rejects_illegal_transition_and_records_failure(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_id = _create_project(runtime_config)
    service = WorkflowStateService(runtime_config)
    novel_run = service.create_novel_run(project_id=project_id, triggered_by="tests")

    with pytest.raises(InvalidStateTransitionError):
        service.transition_novel_run(
            novel_run_id=novel_run.id,
            to_state=NovelRunState.BIBLE_READY,
            triggered_by="worker",
            input_summary="skip state",
        )

    refreshed = service.get_novel_run(novel_run.id)
    assert refreshed is not None
    assert refreshed.status == NovelRunState.INIT.value

    history = service.list_novel_run_transitions(novel_run.id)
    assert len(history) == 2
    assert history[0].success is True
    assert history[1].success is False
    assert "Illegal NovelRun transition" in (history[1].error_message or "")


def test_chapter_transition_engine_records_success_and_failure_audit(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_id = _create_project(runtime_config)
    service = WorkflowStateService(runtime_config)
    novel_run = service.create_novel_run(project_id=project_id, triggered_by="tests")
    chapter_run = service.create_chapter_run(
        novel_run_id=novel_run.id,
        chapter_index=1,
        triggered_by="tests",
    )

    transition = service.transition_chapter_run(
        chapter_run_id=chapter_run.id,
        to_state=ChapterRunState.CONTEXT_PACKED,
        triggered_by="worker",
    )
    assert transition.success is True
    assert transition.to_status == ChapterRunState.CONTEXT_PACKED.value

    with pytest.raises(InvalidStateTransitionError):
        service.transition_chapter_run(
            chapter_run_id=chapter_run.id,
            to_state=ChapterRunState.FACTS_EXTRACTED,
            triggered_by="worker",
        )

    refreshed = service.get_chapter_run(chapter_run.id)
    assert refreshed is not None
    assert refreshed.status == ChapterRunState.CONTEXT_PACKED.value

    history = service.list_chapter_run_transitions(chapter_run.id)
    assert len(history) == 3
    assert history[0].success is True
    assert history[1].success is True
    assert history[2].success is False
    assert "Illegal ChapterRun transition" in (history[2].error_message or "")


def test_non_workflow_modules_do_not_directly_assign_run_status_fields() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "auto_writing"
    workflow_dir = root / "workflow"
    creation_patterns = [
        re.compile(r"NovelRun\([^\)]*status\s*=", re.DOTALL),
        re.compile(r"ChapterRun\([^\)]*status\s*=", re.DOTALL),
    ]
    assignment_pattern = re.compile(r"\.status\s*=")

    violations: list[str] = []
    for path in root.rglob("*.py"):
        if path.is_relative_to(workflow_dir):
            continue

        source = path.read_text(encoding="utf-8")
        if any(pattern.search(source) for pattern in creation_patterns):
            violations.append(f"creation write in {path.relative_to(root)}")
            continue

        if "NovelRun" in source or "ChapterRun" in source:
            if assignment_pattern.search(source):
                violations.append(f"attribute write in {path.relative_to(root)}")

    assert not violations, f"core status writes outside workflow path: {violations}"
