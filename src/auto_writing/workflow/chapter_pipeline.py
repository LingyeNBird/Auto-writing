from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re

from auto_writing.chapter_packet import ChapterPacketBuilder, ChapterPacketRequest
from auto_writing.config import RuntimeConfig
from auto_writing.llm import LLMClient, LLMRequest
from auto_writing.project.service import ProjectService
from auto_writing.prompt_assets import PromptAssetLoader

from .service import WorkflowStateService
from .states import ChapterRunState


DEFAULT_FACTS_PROMPT_TEMPLATE = "extract/facts.md"
DEFAULT_REVIEW_PROMPT_TEMPLATE = "review/continuity.md"
DEFAULT_SUMMARY_PROMPT_TEMPLATE = "chapter/summary.md"
DEFAULT_REVISE_PROMPT_TEMPLATE = "chapter/revise.md"
_VERSION_PATTERN = re.compile(r"^(?P<name>[a-z_]+)_v(?P<version>\d+)\.(?P<ext>[a-z0-9]+)$")


class MalformedLLMOutputError(RuntimeError):
    def __init__(self, stage: str, attempts: int, message: str) -> None:
        super().__init__(f"Malformed output at stage '{stage}' after {attempts} attempts: {message}")
        self.stage = stage
        self.attempts = attempts
        self.message = message


@dataclass(frozen=True)
class ChapterWorkflowResult:
    chapter_run_id: str
    success: bool
    failed_stage: str | None = None
    error_message: str | None = None


class SingleChapterWorkflowPipeline:
    def __init__(
        self,
        *,
        runtime_config: RuntimeConfig,
        workflow_service: WorkflowStateService,
        project_service: ProjectService,
        llm_client: LLMClient,
        chapter_packet_builder: ChapterPacketBuilder | None = None,
        prompt_loader: PromptAssetLoader | None = None,
        max_retries: int = 1,
    ) -> None:
        self._runtime_config = runtime_config
        self._workflow_service = workflow_service
        self._project_service = project_service
        self._llm_client = llm_client
        self._chapter_packet_builder = chapter_packet_builder or ChapterPacketBuilder()
        self._prompt_loader = prompt_loader or PromptAssetLoader()
        self._max_retries = max(0, max_retries)

    def run(self, *, novel_run_id: str, triggered_by: str) -> ChapterWorkflowResult:
        novel_run = self._workflow_service.get_novel_run(novel_run_id)
        if novel_run is None:
            raise ValueError(f"NovelRun not found: {novel_run_id}")

        project = self._project_service.get_project(novel_run.project_id)
        if project is None:
            raise ValueError(f"Project not found for run: {novel_run.project_id}")

        chapter_index = 1
        chapter_run = self._workflow_service.get_or_create_chapter_run(
            novel_run_id=novel_run_id,
            chapter_index=chapter_index,
            triggered_by=triggered_by,
        )

        chapter_dir = self._chapter_dir(project_id=project.id, chapter_index=chapter_index)
        chapter_dir.mkdir(parents=True, exist_ok=True)
        packet: dict[str, object] | None = None

        while True:
            current = self._workflow_service.get_chapter_run(chapter_run.id)
            if current is None:
                return ChapterWorkflowResult(chapter_run_id=chapter_run.id, success=False)

            state = ChapterRunState(current.status)
            if state == ChapterRunState.LOCKED:
                return ChapterWorkflowResult(chapter_run_id=chapter_run.id, success=True)
            if state == ChapterRunState.FAILED:
                return ChapterWorkflowResult(
                    chapter_run_id=chapter_run.id,
                    success=False,
                    failed_stage="chapter-failed",
                    error_message="chapter run already in FAILED state",
                )

            try:
                if state == ChapterRunState.PLANNED:
                    packet = self._build_packet(project_id=project.id, project_name=project.name, theme_notes=project.theme_notes)
                    self._workflow_service.transition_chapter_run(
                        chapter_run_id=chapter_run.id,
                        to_state=ChapterRunState.CONTEXT_PACKED,
                        triggered_by=triggered_by,
                        input_summary="prepared planning input",
                        output_summary="chapter packet prepared",
                    )
                    continue

                if state == ChapterRunState.CONTEXT_PACKED:
                    packet = packet or self._build_packet(
                        project_id=project.id,
                        project_name=project.name,
                        theme_notes=project.theme_notes,
                    )
                    prompt = self._as_text(packet.get("prompt"), "chapter packet prompt")
                    draft_text = self._generate_text_with_retry(
                        stage="chapter-draft",
                        prompt=prompt,
                        metadata={
                            "project_id": project.id,
                            "chapter_index": str(chapter_index),
                        },
                    )
                    draft_path = self._write_text_artifact(
                        chapter_dir=chapter_dir,
                        name="draft",
                        extension="md",
                        content=draft_text,
                    )
                    self._workflow_service.transition_chapter_run(
                        chapter_run_id=chapter_run.id,
                        to_state=ChapterRunState.DRAFTED,
                        triggered_by=triggered_by,
                        input_summary="packet prompt",
                        output_summary=draft_path.name,
                    )
                    continue

                if state == ChapterRunState.DRAFTED:
                    draft_text = self._latest_text_artifact(chapter_dir, name="draft", extension="md")
                    summary_prompt = self._prompt_loader.render(
                        DEFAULT_SUMMARY_PROMPT_TEMPLATE,
                        {"chapter_index": chapter_index},
                    )
                    summary_payload = self._generate_json_with_retry(
                        stage="chapter-summary",
                        prompt=f"{summary_prompt}\n\n[Draft]\n{draft_text}",
                        metadata={
                            "project_id": project.id,
                            "chapter_index": str(chapter_index),
                        },
                    )
                    summary_text = self._as_text(summary_payload.get("summary"), "summary")
                    summary_path = self._write_text_artifact(
                        chapter_dir=chapter_dir,
                        name="summary",
                        extension="md",
                        content=summary_text,
                    )
                    self._workflow_service.transition_chapter_run(
                        chapter_run_id=chapter_run.id,
                        to_state=ChapterRunState.SUMMARIZED,
                        triggered_by=triggered_by,
                        input_summary="draft to summary",
                        output_summary=summary_path.name,
                    )
                    continue

                if state == ChapterRunState.SUMMARIZED:
                    draft_text = self._latest_text_artifact(chapter_dir, name="draft", extension="md")
                    facts_template = self._prompt_loader.load(DEFAULT_FACTS_PROMPT_TEMPLATE)
                    facts_payload = self._generate_json_with_retry(
                        stage="chapter-facts",
                        prompt=f"{facts_template}\n\n[Draft]\n{draft_text}",
                        metadata={
                            "project_id": project.id,
                            "chapter_index": str(chapter_index),
                        },
                    )
                    facts = facts_payload.get("facts")
                    if not isinstance(facts, list) or len(facts) == 0:
                        raise MalformedLLMOutputError(
                            stage="chapter-facts",
                            attempts=self._max_retries + 1,
                            message="facts must be a non-empty list",
                        )
                    facts_path = self._write_json_artifact(
                        chapter_dir=chapter_dir,
                        name="facts",
                        payload=facts_payload,
                    )
                    self._workflow_service.transition_chapter_run(
                        chapter_run_id=chapter_run.id,
                        to_state=ChapterRunState.FACTS_EXTRACTED,
                        triggered_by=triggered_by,
                        input_summary="draft to facts",
                        output_summary=facts_path.name,
                    )
                    continue

                if state == ChapterRunState.FACTS_EXTRACTED:
                    draft_text = self._latest_text_artifact(chapter_dir, name="draft", extension="md")
                    facts_text = self._latest_text_artifact(chapter_dir, name="facts", extension="json")
                    review_template = self._prompt_loader.load(DEFAULT_REVIEW_PROMPT_TEMPLATE)
                    review_payload = self._generate_json_with_retry(
                        stage="chapter-review",
                        prompt=f"{review_template}\n\n[Draft]\n{draft_text}\n\n[Facts]\n{facts_text}",
                        metadata={
                            "project_id": project.id,
                            "chapter_index": str(chapter_index),
                        },
                    )
                    requires_revision = review_payload.get("requires_revision")
                    issues = review_payload.get("issues")
                    if not isinstance(requires_revision, bool):
                        raise MalformedLLMOutputError(
                            stage="chapter-review",
                            attempts=self._max_retries + 1,
                            message="requires_revision must be bool",
                        )
                    if not isinstance(issues, list):
                        raise MalformedLLMOutputError(
                            stage="chapter-review",
                            attempts=self._max_retries + 1,
                            message="issues must be a list",
                        )
                    review_path = self._write_json_artifact(
                        chapter_dir=chapter_dir,
                        name="review",
                        payload=review_payload,
                    )
                    self._workflow_service.transition_chapter_run(
                        chapter_run_id=chapter_run.id,
                        to_state=ChapterRunState.CONTINUITY_CHECKED,
                        triggered_by=triggered_by,
                        input_summary="facts and draft reviewed",
                        output_summary=review_path.name,
                    )
                    continue

                if state == ChapterRunState.CONTINUITY_CHECKED:
                    draft_text = self._latest_text_artifact(chapter_dir, name="draft", extension="md")
                    review_text = self._latest_text_artifact(chapter_dir, name="review", extension="json")
                    revise_template = self._prompt_loader.render(
                        DEFAULT_REVISE_PROMPT_TEMPLATE,
                        {"chapter_index": chapter_index},
                    )
                    revised_draft = self._generate_text_with_retry(
                        stage="chapter-revise",
                        prompt=(
                            f"{revise_template}\n\n[Draft]\n{draft_text}\n\n[Review]\n{review_text}"
                        ),
                        metadata={
                            "project_id": project.id,
                            "chapter_index": str(chapter_index),
                        },
                    )
                    revised_path = self._write_text_artifact(
                        chapter_dir=chapter_dir,
                        name="draft",
                        extension="md",
                        content=revised_draft,
                    )
                    self._workflow_service.transition_chapter_run(
                        chapter_run_id=chapter_run.id,
                        to_state=ChapterRunState.REVISED,
                        triggered_by=triggered_by,
                        input_summary="targeted revise",
                        output_summary=revised_path.name,
                    )
                    continue

                if state == ChapterRunState.REVISED:
                    self._workflow_service.transition_chapter_run(
                        chapter_run_id=chapter_run.id,
                        to_state=ChapterRunState.LOCKED,
                        triggered_by=triggered_by,
                        input_summary="revised chapter accepted",
                        output_summary="locked",
                    )
                    continue

                raise RuntimeError(f"Unsupported chapter state: {state.value}")
            except MalformedLLMOutputError as exc:
                self._workflow_service.transition_chapter_run(
                    chapter_run_id=chapter_run.id,
                    to_state=ChapterRunState.FAILED,
                    triggered_by=triggered_by,
                    input_summary=f"stage={exc.stage}; attempts={exc.attempts}",
                    output_summary=exc.message,
                )
                return ChapterWorkflowResult(
                    chapter_run_id=chapter_run.id,
                    success=False,
                    failed_stage=exc.stage,
                    error_message=exc.message,
                )

    def _build_packet(
        self,
        *,
        project_id: str,
        project_name: str,
        theme_notes: str | None,
    ) -> dict[str, object]:
        project_dir = self._runtime_config.data_dir / "projects" / project_id
        theme = (theme_notes or "Keep continuity stable.").strip() or "Keep continuity stable."
        request = ChapterPacketRequest(
            project_dir=project_dir,
            project_config={
                "project_id": project_id,
                "name": project_name,
                "theme_notes": theme,
            },
            chapter_index=1,
            outline=f"Chapter 1 outline for project '{project_name}'.",
            goal=f"Chapter goal: {theme}",
            recent_summaries=["No prior chapter summary yet; establish baseline continuity."],
            constraints=[
                "Maintain canon consistency.",
                f"Honor project theme: {theme}",
            ],
        )
        return self._chapter_packet_builder.build(request)

    def _generate_text_with_retry(
        self,
        *,
        stage: str,
        prompt: str,
        metadata: Mapping[str, str],
    ) -> str:
        attempts = self._max_retries + 1
        last_error = ""
        for _attempt in range(1, attempts + 1):
            response = self._llm_client.generate(
                LLMRequest(
                    stage=stage,
                    prompt=prompt,
                    response_format="text",
                    metadata=metadata,
                )
            )
            text = response.output_text.strip()
            if text != "":
                return text
            last_error = "output text is empty"
        raise MalformedLLMOutputError(stage=stage, attempts=attempts, message=last_error)

    def _generate_json_with_retry(
        self,
        *,
        stage: str,
        prompt: str,
        metadata: Mapping[str, str],
    ) -> dict[str, object]:
        attempts = self._max_retries + 1
        last_error = ""
        for _attempt in range(1, attempts + 1):
            response = self._llm_client.generate(
                LLMRequest(
                    stage=stage,
                    prompt=prompt,
                    response_format="json",
                    metadata=metadata,
                )
            )
            try:
                payload = json.loads(response.output_text)
            except json.JSONDecodeError as exc:
                last_error = str(exc)
                continue
            if isinstance(payload, dict):
                return {str(key): value for key, value in payload.items()}
            last_error = "response payload must be a JSON object"
        raise MalformedLLMOutputError(stage=stage, attempts=attempts, message=last_error)

    def _chapter_dir(self, *, project_id: str, chapter_index: int) -> Path:
        return self._runtime_config.data_dir / "projects" / project_id / "chapters" / f"chapter_{chapter_index:03d}"

    def _latest_text_artifact(self, chapter_dir: Path, *, name: str, extension: str) -> str:
        latest = self._latest_artifact_path(chapter_dir, name=name, extension=extension)
        if latest is None:
            raise RuntimeError(f"Missing artifact for stage continuation: {name}")
        return latest.read_text(encoding="utf-8")

    def _latest_artifact_path(self, chapter_dir: Path, *, name: str, extension: str) -> Path | None:
        highest_version = 0
        selected: Path | None = None
        for path in chapter_dir.glob(f"{name}_v*.{extension}"):
            match = _VERSION_PATTERN.match(path.name)
            if match is None:
                continue
            if match.group("name") != name or match.group("ext") != extension:
                continue
            version = int(match.group("version"))
            if version > highest_version:
                highest_version = version
                selected = path
        return selected

    def _next_artifact_path(self, chapter_dir: Path, *, name: str, extension: str) -> Path:
        latest = self._latest_artifact_path(chapter_dir, name=name, extension=extension)
        next_version = 1
        if latest is not None:
            match = _VERSION_PATTERN.match(latest.name)
            if match is not None:
                next_version = int(match.group("version")) + 1
        return chapter_dir / f"{name}_v{next_version}.{extension}"

    def _write_text_artifact(self, *, chapter_dir: Path, name: str, extension: str, content: str) -> Path:
        target = self._next_artifact_path(chapter_dir, name=name, extension=extension)
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        return target

    def _write_json_artifact(self, *, chapter_dir: Path, name: str, payload: Mapping[str, object]) -> Path:
        target = self._next_artifact_path(chapter_dir, name=name, extension="json")
        target.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return target

    @staticmethod
    def _as_text(value: object, label: str) -> str:
        if not isinstance(value, str):
            raise RuntimeError(f"Expected text value for {label}")
        normalized = value.strip()
        if normalized == "":
            raise RuntimeError(f"Expected non-empty text value for {label}")
        return normalized
