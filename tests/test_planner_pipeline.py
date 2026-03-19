# pyright: reportMissingImports=false
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from auto_writing.config import RuntimeConfig
from auto_writing.llm import FakeLLMClient, LLMRequest, LLMResponse
from auto_writing.planner import PlannerService
from auto_writing.project.service import ProjectService
from auto_writing.worker import WorkflowWorker
from auto_writing.workflow.chapter_pipeline import SingleChapterWorkflowPipeline
from auto_writing.workflow.service import WorkflowStateService
from auto_writing.workflow.states import ChapterRunState, NovelRunState


def _build_runtime_config(tmp_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        storage_dir=tmp_path / "storage",
        log_level="INFO",
        app_port=8000,
    )


def test_worker_generates_versioned_planning_assets_and_updates_canon(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_service = ProjectService(runtime_config)
    workflow_service = WorkflowStateService(runtime_config)
    llm_client = FakeLLMClient()

    project = project_service.create_project(
        name="planner-test",
        chapter_count=1,
        theme_notes="Clockwork intrigue",
    )
    novel_run = workflow_service.trigger_novel_run(project_id=project.id, triggered_by="tests")
    worker = WorkflowWorker(
        workflow_service,
        runtime_config=runtime_config,
        project_service=project_service,
        llm_client=llm_client,
        worker_id="planner-worker",
    )

    assert worker.run_for_novel_run(novel_run.id) is True

    project_dir = runtime_config.data_dir / "projects" / project.id
    assert (project_dir / "story_bible_v1.md").is_file()
    assert (project_dir / "world" / "rules_v1.yaml").is_file()
    assert (project_dir / "world" / "locations_v1.yaml").is_file()
    assert (project_dir / "characters" / "lead_character_v1.md").is_file()
    assert (project_dir / "outlines" / "master_outline_v1.md").is_file()
    assert (project_dir / "outlines" / "chapter_001_v1.md").is_file()

    assert (project_dir / "story_bible_v1.md").read_text(encoding="utf-8") != "# Story Bible v1\n\n"
    assert (project_dir / "world" / "rules_v1.yaml").read_text(encoding="utf-8") != "rules: []\n"
    assert (project_dir / "world" / "locations_v1.yaml").read_text(encoding="utf-8") != "locations: []\n"
    assert (project_dir / "outlines" / "master_outline_v1.md").read_text(encoding="utf-8") != "# Master Outline v1\n\n"

    canon_payload = json.loads((project_dir / "canon" / "context.json").read_text(encoding="utf-8"))
    assert canon_payload["placeholders"]
    assert canon_payload["role_info"]
    assert canon_payload["placeholders"]["core_rule"] != "Maintain internal causality."
    assert canon_payload["role_info"][0]["name"] != "Narrator"


class RecordingChapterLLMClient:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if request.stage == "chapter-draft":
            return LLMResponse(
                provider="test",
                model="recording",
                output_text="Draft built from generated chapter outline.",
                structured_output={"draft": "Draft built from generated chapter outline."},
            )
        if request.stage == "chapter-summary":
            payload = {"summary": "Summary from generated chapter outline."}
            return LLMResponse(
                provider="test",
                model="recording",
                output_text=json.dumps(payload, ensure_ascii=True),
                structured_output=payload,
            )
        if request.stage == "chapter-facts":
            payload = {"facts": ["generated-outline-fact"]}
            return LLMResponse(
                provider="test",
                model="recording",
                output_text=json.dumps(payload, ensure_ascii=True),
                structured_output=payload,
            )
        if request.stage == "chapter-review":
            payload = {"requires_revision": True, "issues": []}
            return LLMResponse(
                provider="test",
                model="recording",
                output_text=json.dumps(payload, ensure_ascii=True),
                structured_output=payload,
            )
        if request.stage == "chapter-revise":
            return LLMResponse(
                provider="test",
                model="recording",
                output_text="Revised draft from generated chapter outline.",
                structured_output={"revised_draft": "Revised draft from generated chapter outline."},
            )
        raise AssertionError(f"Unexpected stage: {request.stage}")


def test_chapter_pipeline_uses_generated_outline_file_instead_of_fallback(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_service = ProjectService(runtime_config)
    workflow_service = WorkflowStateService(runtime_config)
    llm_client = RecordingChapterLLMClient()

    project = project_service.create_project(
        name="outline-source-test",
        chapter_count=1,
        theme_notes="Solar court intrigue",
    )
    project_dir = runtime_config.data_dir / "projects" / project.id
    custom_outline = "# Chapter 1 Outline\n\n- Open with the eclipse tribunal."
    (project_dir / "canon" / "context.json").write_text(
        json.dumps(
            {
                "placeholders": {"core_rule": "The eclipse bell cannot ring twice."},
                "role_info": [
                    {
                        "name": "Archivist",
                        "role": "witness",
                        "goal": "Track the tribunal's official record",
                    }
                ],
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (project_dir / "retrieval" / "memory.json").write_text(
        json.dumps({"memory": ["No prior chapter summary yet."]}, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (project_dir / "outlines" / "chapter_001_v1.md").write_text(custom_outline + "\n", encoding="utf-8")

    novel_run = workflow_service.create_novel_run(project_id=project.id, triggered_by="tests")
    pipeline = SingleChapterWorkflowPipeline(
        runtime_config=runtime_config,
        workflow_service=workflow_service,
        project_service=project_service,
        llm_client=llm_client,
    )

    result = pipeline.run(novel_run_id=novel_run.id, triggered_by="tests")

    assert result.success is True
    draft_request = next(request for request in llm_client.requests if request.stage == "chapter-draft")
    assert custom_outline in draft_request.prompt
    assert "Chapter 1 outline for project 'outline-source-test'." not in draft_request.prompt


def test_worker_recovers_mid_chapters_running_and_resumes_chapter_pipeline(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_service = ProjectService(runtime_config)
    workflow_service = WorkflowStateService(runtime_config)
    planner_service = PlannerService(runtime_config=runtime_config, llm_client=FakeLLMClient())

    project = project_service.create_project(
        name="resume-chapters-running",
        chapter_count=1,
        theme_notes="Flooded palace intrigue",
    )
    novel_run = workflow_service.trigger_novel_run(project_id=project.id, triggered_by="tests")

    for target_state in (
        NovelRunState.INPUT_NORMALIZED,
        NovelRunState.BIBLE_READY,
        NovelRunState.CHARACTERS_READY,
        NovelRunState.MASTER_OUTLINE_READY,
    ):
        result = planner_service.prepare_for_transition(project=project, target_state=target_state)
        _ = workflow_service.transition_novel_run(
            novel_run_id=novel_run.id,
            to_state=target_state,
            triggered_by="tests",
            input_summary="seed planner state",
            output_summary=result.output_summary,
        )

    _ = workflow_service.transition_novel_run(
        novel_run_id=novel_run.id,
        to_state=NovelRunState.CHAPTERS_RUNNING,
        triggered_by="tests",
        input_summary="seed chapter orchestration",
        output_summary="checkpoint=CHAPTERS_RUNNING",
    )

    chapter_run = workflow_service.get_or_create_chapter_run(
        novel_run_id=novel_run.id,
        chapter_index=1,
        triggered_by="tests",
    )
    _ = workflow_service.transition_chapter_run(
        chapter_run_id=chapter_run.id,
        to_state=ChapterRunState.CONTEXT_PACKED,
        triggered_by="tests",
        input_summary="seed packet",
        output_summary="packet prepared",
    )
    chapter_dir = runtime_config.data_dir / "projects" / project.id / "chapters" / "chapter_001"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    _ = (chapter_dir / "draft_v1.md").write_text("Partial draft before interruption.\n", encoding="utf-8")
    _ = workflow_service.transition_chapter_run(
        chapter_run_id=chapter_run.id,
        to_state=ChapterRunState.DRAFTED,
        triggered_by="tests",
        input_summary="seed draft",
        output_summary="draft_v1.md",
    )

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

    claimed = workflow_service.claim_novel_run_by_id(
        novel_run_id=novel_run.id,
        worker_id=worker_a.worker_id,
        lease_seconds=30,
        now=first_tick,
    )
    assert claimed is not None

    blocked = worker_b.run_for_novel_run(novel_run.id, now=first_tick)
    assert blocked is False

    recovered = worker_b.run_for_novel_run(novel_run.id, now=first_tick + timedelta(seconds=31))
    assert recovered is True

    refreshed_run = workflow_service.get_novel_run(novel_run.id)
    assert refreshed_run is not None
    assert refreshed_run.status == NovelRunState.FINALIZED.value
    assert refreshed_run.checkpoint_state == NovelRunState.FINALIZED.value

    refreshed_chapter = workflow_service.get_chapter_run(chapter_run.id)
    assert refreshed_chapter is not None
    assert refreshed_chapter.status == ChapterRunState.LOCKED.value
    assert (chapter_dir / "summary_v1.md").is_file()
    assert (chapter_dir / "facts_v1.json").is_file()
    assert (chapter_dir / "review_v1.json").is_file()
    assert (chapter_dir / "draft_v2.md").is_file()


class RecordingPlannerLLMClient:
    def __init__(self) -> None:
        self._delegate = FakeLLMClient()
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return self._delegate.generate(request)


def test_planner_outline_generation_uses_latest_character_card_versions(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_service = ProjectService(runtime_config)
    llm_client = RecordingPlannerLLMClient()
    planner_service = PlannerService(runtime_config=runtime_config, llm_client=llm_client)

    project = project_service.create_project(
        name="planner-version-selection",
        chapter_count=1,
        theme_notes="Ember court intrigue",
    )
    project_dir = runtime_config.data_dir / "projects" / project.id

    _ = planner_service.prepare_for_transition(project=project, target_state=NovelRunState.BIBLE_READY)
    _ = planner_service.prepare_for_transition(project=project, target_state=NovelRunState.CHARACTERS_READY)

    latest_card = "# Ember Vale\n\n- Role: lead investigator\n- Goal: Protect the second archive.\n"
    _ = (project_dir / "characters" / "lead_character_v2.md").write_text(latest_card, encoding="utf-8")

    _ = planner_service.prepare_for_transition(project=project, target_state=NovelRunState.MASTER_OUTLINE_READY)

    master_outline_request = next(
        request for request in llm_client.requests if request.stage == "planner-master-outline"
    )
    assert latest_card.strip() in master_outline_request.prompt
    assert "Anchor: keeps track of marker" not in master_outline_request.prompt


class ExplodingChapterLLMClient:
    def generate(self, request: LLMRequest) -> LLMResponse:
        raise RuntimeError(f"planner handoff exploded at {request.stage}")


def test_worker_marks_active_chapter_failed_when_chapter_pipeline_raises(tmp_path: Path) -> None:
    runtime_config = _build_runtime_config(tmp_path)
    project_service = ProjectService(runtime_config)
    workflow_service = WorkflowStateService(runtime_config)
    planner_service = PlannerService(runtime_config=runtime_config, llm_client=FakeLLMClient())

    project = project_service.create_project(
        name="chapter-exception",
        chapter_count=1,
        theme_notes="Harbor conspiracy",
    )
    novel_run = workflow_service.create_novel_run(project_id=project.id, triggered_by="tests")

    for target_state in (
        NovelRunState.INPUT_NORMALIZED,
        NovelRunState.BIBLE_READY,
        NovelRunState.CHARACTERS_READY,
        NovelRunState.MASTER_OUTLINE_READY,
    ):
        result = planner_service.prepare_for_transition(project=project, target_state=target_state)
        _ = workflow_service.transition_novel_run(
            novel_run_id=novel_run.id,
            to_state=target_state,
            triggered_by="tests",
            input_summary="seed state",
            output_summary=result.output_summary,
        )

    _ = workflow_service.transition_novel_run(
        novel_run_id=novel_run.id,
        to_state=NovelRunState.CHAPTERS_RUNNING,
        triggered_by="tests",
        input_summary="seed state",
        output_summary="checkpoint=CHAPTERS_RUNNING",
    )

    chapter_pipeline = SingleChapterWorkflowPipeline(
        runtime_config=runtime_config,
        workflow_service=workflow_service,
        project_service=project_service,
        llm_client=ExplodingChapterLLMClient(),
    )

    worker = WorkflowWorker(
        workflow_service,
        chapter_pipeline=chapter_pipeline,
        worker_id="exploding-worker",
    )

    assert worker.run_for_novel_run(novel_run.id) is True

    refreshed_run = workflow_service.get_novel_run(novel_run.id)
    assert refreshed_run is not None
    assert refreshed_run.status == NovelRunState.FAILED.value

    chapter_run = workflow_service.get_chapter_run_for_index(novel_run_id=novel_run.id, chapter_index=1)
    assert chapter_run is not None
    assert chapter_run.status == ChapterRunState.FAILED.value
