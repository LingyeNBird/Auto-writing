from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import TypeGuard

from auto_writing.prompt_assets import (
    PromptAssetLoader,
    PromptAssetNotFoundError,
    PromptAssetRenderError,
)

DEFAULT_CHAPTER_PROMPT_TEMPLATE = "chapter/draft.md"
DEFAULT_SYSTEM_PROMPT_TEMPLATE = "system/global.md"
DEFAULT_CANON_CONTEXT_PATH = Path("canon/context.json")
DEFAULT_RETRIEVAL_MEMORY_PATH = Path("retrieval/memory.json")


class ChapterPacketBuildError(RuntimeError):
    pass


class ChapterPacketTemplateMissingError(ChapterPacketBuildError):
    pass


class ChapterPacketMissingContextError(ChapterPacketBuildError):
    pass


@dataclass(frozen=True)
class ChapterPacketRequest:
    project_dir: Path
    project_config: Mapping[str, object]
    chapter_index: int
    outline: str
    goal: str
    recent_summaries: Sequence[str]
    constraints: Sequence[str]
    prompt_template_path: str = DEFAULT_CHAPTER_PROMPT_TEMPLATE
    canon_relative_path: Path = DEFAULT_CANON_CONTEXT_PATH
    retrieval_relative_path: Path = DEFAULT_RETRIEVAL_MEMORY_PATH


class ChapterPacketBuilder:
    def __init__(
        self,
        prompt_loader: PromptAssetLoader | None = None,
        *,
        system_prompt_template_path: str = DEFAULT_SYSTEM_PROMPT_TEMPLATE,
    ) -> None:
        self._prompt_loader = prompt_loader or PromptAssetLoader()
        self._system_prompt_template_path = system_prompt_template_path

    def build(self, request: ChapterPacketRequest) -> dict[str, object]:
        outline = _require_text("outline", request.outline)
        goal = _require_text("goal", request.goal)
        project_config = _require_project_config(request.project_config)
        recent_summaries = _require_text_list("recent_summaries", request.recent_summaries)
        constraints = _require_text_list("constraints", request.constraints)

        canon_path = self._resolve_context_path(request.project_dir, request.canon_relative_path)
        retrieval_path = self._resolve_context_path(request.project_dir, request.retrieval_relative_path)
        if canon_path == retrieval_path:
            raise ChapterPacketMissingContextError(
                "canon context path and retrieval memory path must be different"
            )

        canon = self._load_canon_context(canon_path)
        retrieval_memory = self._load_retrieval_memory(retrieval_path)
        prompt = self._render_prompt(
            chapter_index=request.chapter_index,
            prompt_template_path=request.prompt_template_path,
            project_config=project_config,
            outline=outline,
            goal=goal,
            canon=canon,
            recent_summaries=recent_summaries,
            retrieval_memory=retrieval_memory,
            constraints=constraints,
        )

        return {
            "outline": outline,
            "goal": goal,
            "canon": canon,
            "recent_summaries": recent_summaries,
            "retrieval_memory": retrieval_memory,
            "constraints": constraints,
            "prompt": prompt,
        }

    @staticmethod
    def _resolve_context_path(project_dir: Path, relative_path: Path) -> Path:
        if relative_path.is_absolute():
            raise ChapterPacketMissingContextError("context asset path must be relative to project_dir")
        resolved = (project_dir / relative_path).resolve()
        try:
            resolved.relative_to(project_dir.resolve())
        except ValueError as exc:
            raise ChapterPacketMissingContextError("context asset path escapes project_dir") from exc
        return resolved

    def _load_canon_context(self, path: Path) -> dict[str, object]:
        payload = self._load_json_file(path, "canon")
        raw_placeholders = payload.get("placeholders")
        if not isinstance(raw_placeholders, Mapping) or len(raw_placeholders) == 0:
            raise ChapterPacketMissingContextError("Missing required context data: canon.placeholders")

        placeholders = {
            key: _require_text(f"canon.placeholders.{key}", str(value))
            for key, value in sorted(raw_placeholders.items())
        }

        raw_role_info = payload.get("role_info")
        if not _is_non_string_sequence(raw_role_info):
            raise ChapterPacketMissingContextError("Missing required context data: canon.role_info")

        normalized_role_info: list[dict[str, object]] = []
        for item in raw_role_info:
            if not isinstance(item, Mapping):
                raise ChapterPacketMissingContextError("canon.role_info entries must be objects")
            name = _require_text("canon.role_info.name", _as_text(item.get("name")))
            role = _require_text("canon.role_info.role", _as_text(item.get("role")))
            normalized = {str(key): item[key] for key in sorted(item)}
            normalized["name"] = name
            normalized["role"] = role
            normalized_role_info.append(normalized)

        normalized_role_info.sort(key=lambda item: (str(item.get("name", "")), str(item.get("role", ""))))
        return {
            "placeholders": placeholders,
            "role_info": normalized_role_info,
        }

    def _load_retrieval_memory(self, path: Path) -> list[str]:
        payload = self._load_json_file(path, "retrieval")
        raw_memory = payload.get("memory")
        if not _is_non_string_sequence(raw_memory):
            raise ChapterPacketMissingContextError("Missing required context data: retrieval.memory")

        return [_require_text("retrieval.memory", _as_text(item)) for item in raw_memory]

    def _load_json_file(self, path: Path, label: str) -> dict[str, object]:
        try:
            raw_content = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ChapterPacketMissingContextError(
                f"Missing required context data: {label} file not found at {path}"
            ) from exc

        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise ChapterPacketMissingContextError(
                f"Invalid context data format for {label}: expected JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise ChapterPacketMissingContextError(f"Invalid context data for {label}: expected JSON object")
        return payload

    def _render_prompt(
        self,
        *,
        chapter_index: int,
        prompt_template_path: str,
        project_config: Mapping[str, object],
        outline: str,
        goal: str,
        canon: Mapping[str, object],
        recent_summaries: Sequence[str],
        retrieval_memory: Sequence[str],
        constraints: Sequence[str],
    ) -> str:
        try:
            system_prompt = self._prompt_loader.load(self._system_prompt_template_path).strip()
            chapter_prompt = self._prompt_loader.render(
                prompt_template_path,
                {
                    "chapter_index": chapter_index,
                    "project_id": _as_text(project_config.get("project_id")),
                    "project_name": _as_text(project_config.get("name")),
                },
            ).strip()
        except PromptAssetNotFoundError as exc:
            raise ChapterPacketTemplateMissingError(str(exc)) from exc
        except PromptAssetRenderError as exc:
            raise ChapterPacketMissingContextError(
                f"Missing required context data for prompt rendering: {exc}"
            ) from exc

        normalized_project_config = {
            key: project_config[key]
            for key in sorted(project_config)
        }
        sections = [
            ("System", system_prompt),
            ("ChapterTask", chapter_prompt),
            ("ProjectConfig", json.dumps(normalized_project_config, sort_keys=True, ensure_ascii=True)),
            ("Outline", outline),
            ("Goal", goal),
            ("Canon", json.dumps(canon, sort_keys=True, ensure_ascii=True)),
            ("RecentSummaries", json.dumps(list(recent_summaries), ensure_ascii=True)),
            ("RetrievalMemory", json.dumps(list(retrieval_memory), ensure_ascii=True)),
            ("Constraints", json.dumps(list(constraints), ensure_ascii=True)),
        ]
        return "\n\n".join(f"[{name}]\n{value}" for name, value in sections)


def _require_project_config(project_config: Mapping[str, object]) -> dict[str, object]:
    if len(project_config) == 0:
        raise ChapterPacketMissingContextError("Missing required context data: project_config")
    return {str(key): value for key, value in project_config.items()}


def _is_non_string_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, str) and len(value) > 0


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _require_text(field_name: str, value: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ChapterPacketMissingContextError(f"Missing required context data: {field_name}")
    return normalized


def _require_text_list(field_name: str, values: Sequence[str]) -> list[str]:
    if len(values) == 0:
        raise ChapterPacketMissingContextError(f"Missing required context data: {field_name}")
    return [_require_text(field_name, _as_text(item)) for item in values]
