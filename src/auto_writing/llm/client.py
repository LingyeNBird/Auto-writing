from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
import os
import re
from typing import Protocol


def _empty_metadata() -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class LLMRequest:
    stage: str
    prompt: str
    response_format: str = "text"
    metadata: Mapping[str, str] = field(default_factory=_empty_metadata)


@dataclass(frozen=True)
class LLMResponse:
    provider: str
    model: str
    output_text: str
    structured_output: Mapping[str, object]


class LLMClient(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse:
        ...


class LLMProviderNotConfiguredError(RuntimeError):
    pass


class LLMProviderTransportNotConfiguredError(RuntimeError):
    pass


class FakeLLMClient:
    def __init__(
        self,
        *,
        model: str = "fake-deterministic-v1",
        malformed_stages: Mapping[str, int] | None = None,
    ) -> None:
        self._model: str = model
        self._malformed_remaining: dict[str, int] = {
            stage: max(0, count)
            for stage, count in dict(malformed_stages or {}).items()
        }

    def _malformed_response(self, request: LLMRequest, fingerprint: str) -> LLMResponse:
        structured_output = {
            "stage": request.stage,
            "fingerprint": fingerprint[:16],
            "response_format": request.response_format,
            "metadata": dict(sorted(request.metadata.items())),
            "malformed": True,
        }
        return LLMResponse(
            provider="fake",
            model=self._model,
            output_text="{malformed-json",
            structured_output=structured_output,
        )

    @staticmethod
    def _metadata_value(metadata: Mapping[str, str], key: str, default: str) -> str:
        value = metadata.get(key, "").strip()
        return value or default

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
        return normalized.strip("-") or "project"

    @staticmethod
    def _project_title(value: str) -> str:
        parts = [segment for segment in re.split(r"[^a-zA-Z0-9]+", value.strip()) if segment]
        if len(parts) == 0:
            return "Project"
        return " ".join(part.capitalize() for part in parts)

    def _stage_payload(self, request: LLMRequest, fingerprint: str) -> tuple[str, Mapping[str, object]]:
        metadata = dict(sorted(request.metadata.items()))
        chapter_index = self._metadata_value(metadata, "chapter_index", "1")
        project_name = self._metadata_value(metadata, "project_name", "project")
        project_title = self._project_title(project_name)
        project_slug = self._slugify(project_name)
        theme_notes = self._metadata_value(metadata, "theme_notes", "Keep continuity stable.")
        chapter_count = self._metadata_value(metadata, "chapter_count", "1")

        if request.stage == "planner-premise":
            story_bible_markdown = "\n\n".join(
                [
                    f"# Story Bible for {project_title}",
                    f"## Premise\n{project_title} turns {theme_notes.lower()} into the central pressure point of the run.",
                    f"## Promise\nTrack the clue chain stamped with deterministic marker {fingerprint[:10]}.",
                ]
            )
            payload = {
                "story_bible_markdown": story_bible_markdown,
                "canon_placeholders": {
                    "core_rule": f"All major reveals in {project_title} must remain causally grounded.",
                    "story_driver": f"Push the conflict around {theme_notes.lower()} until chapter {chapter_count} resolves it.",
                },
            }
            return json.dumps(payload, ensure_ascii=True, sort_keys=True), payload

        if request.stage == "planner-world":
            payload = {
                "rules": [
                    {
                        "name": "causality_anchor",
                        "description": f"Evidence in {project_slug} must validate the public story before it can overturn it.",
                    },
                    {
                        "name": "pressure_clock",
                        "description": f"Every setback advances the hidden clock keyed to {fingerprint[:8]}.",
                    },
                ],
                "locations": [
                    {
                        "name": "Archive Tower",
                        "description": f"A records vault where {project_title} keeps its most dangerous secrets in plain sight.",
                    },
                    {
                        "name": "Transit Ward",
                        "description": f"A crowded district where {theme_notes.lower()} collides with daily survival.",
                    },
                ],
                "canon_placeholders": {
                    "signature_location": "Archive Tower",
                    "pressure_point": f"The public order depends on the hidden clock {fingerprint[:8]} staying secret.",
                },
            }
            return json.dumps(payload, ensure_ascii=True, sort_keys=True), payload

        if request.stage == "planner-characters":
            lead_name = f"{project_title.split()[0]} Vale"
            lead_goal = f"Expose the truth behind {theme_notes.lower()} before the next official deadline closes the trail."
            card_markdown = "\n".join(
                [
                    f"# {lead_name}",
                    "",
                    "- Role: lead investigator",
                    f"- Goal: {lead_goal}",
                    f"- Anchor: keeps track of marker {fingerprint[:8]}",
                ]
            )
            payload = {
                "characters": [
                    {
                        "file_stem": "lead_character",
                        "name": lead_name,
                        "role": "lead investigator",
                        "goal": lead_goal,
                        "card_markdown": card_markdown,
                    }
                ],
                "role_info": [
                    {
                        "name": lead_name,
                        "role": "lead investigator",
                        "goal": lead_goal,
                    }
                ],
            }
            return json.dumps(payload, ensure_ascii=True, sort_keys=True), payload

        if request.stage == "planner-master-outline":
            payload = {
                "master_outline_markdown": "\n".join(
                    [
                        f"# Master Outline for {project_title}",
                        "",
                        f"1. Chapter 1 - {project_title} opens with a destabilizing clue tied to {theme_notes.lower()}.",
                    ]
                )
            }
            return json.dumps(payload, ensure_ascii=True, sort_keys=True), payload

        if request.stage == "planner-chapter-outline":
            outline_text = "\n".join(
                [
                    f"# Chapter {chapter_index} Outline",
                    "",
                    f"- Project: {project_name}",
                    f"- Opening move: reveal the clue chain marked {fingerprint[:8]}.",
                    f"- Chapter goal: escalate {theme_notes.lower()} without breaking canon.",
                ]
            )
            return outline_text, {"chapter_outline": outline_text}

        if request.stage == "chapter-draft":
            draft_text = (
                f"Chapter {chapter_index} draft built from deterministic fingerprint {fingerprint[:12]}."
            )
            return draft_text, {"draft": draft_text}

        if request.stage == "chapter-summary":
            summary_text = f"Summary for chapter {chapter_index} fingerprint {fingerprint[:12]}."
            payload: dict[str, object] = {"summary": summary_text}
            return json.dumps(payload, ensure_ascii=True, sort_keys=True), payload

        if request.stage == "chapter-facts":
            payload = {
                "facts": [
                    f"chapter_{chapter_index}_fact_{fingerprint[:8]}",
                    f"chapter_{chapter_index}_fact_{fingerprint[8:16]}",
                ]
            }
            return json.dumps(payload, ensure_ascii=True, sort_keys=True), payload

        if request.stage == "chapter-review":
            payload = {
                "requires_revision": True,
                "issues": [
                    {
                        "type": "continuity",
                        "severity": "medium",
                        "evidence": f"review evidence {fingerprint[:10]}",
                        "fix": "Align wording with established chapter timeline.",
                    }
                ],
            }
            return json.dumps(payload, ensure_ascii=True, sort_keys=True), payload

        if request.stage == "chapter-revise":
            revised_text = (
                f"Chapter {chapter_index} revised draft with targeted continuity fix {fingerprint[:12]}."
            )
            return revised_text, {"revised_draft": revised_text}

        payload = {
            "stage": request.stage,
            "fingerprint": fingerprint[:16],
            "response_format": request.response_format,
            "metadata": metadata,
        }
        return f"fake:{request.stage}:{fingerprint[:24]}", payload

    def generate(self, request: LLMRequest) -> LLMResponse:
        metadata_items = sorted(request.metadata.items())
        payload = {
            "stage": request.stage,
            "prompt": request.prompt,
            "response_format": request.response_format,
            "metadata": metadata_items,
        }
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

        malformed_remaining = self._malformed_remaining.get(request.stage, 0)
        if malformed_remaining > 0:
            self._malformed_remaining[request.stage] = malformed_remaining - 1
            return self._malformed_response(request, fingerprint)

        output_text, stage_payload = self._stage_payload(request, fingerprint)
        structured_output: dict[str, object] = {
            "stage": request.stage,
            "fingerprint": fingerprint[:16],
            "response_format": request.response_format,
            "metadata": dict(metadata_items),
        }
        structured_output.update(dict(stage_payload))
        return LLMResponse(
            provider="fake",
            model=self._model,
            output_text=output_text,
            structured_output=structured_output,
        )


LLMTransport = Callable[[LLMRequest, str, str], LLMResponse]


class RealProviderLLMClient:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str | None,
        transport: LLMTransport | None = None,
    ) -> None:
        self._provider: str = provider
        self._model: str = model
        self._api_key: str | None = api_key
        self._transport: LLMTransport | None = transport

    def generate(self, request: LLMRequest) -> LLMResponse:
        if not self._api_key:
            raise LLMProviderNotConfiguredError(
                f"Provider '{self._provider}' is not configured: missing API key"
            )
        if self._transport is None:
            raise LLMProviderTransportNotConfiguredError(
                f"Provider '{self._provider}' transport is not configured in this environment"
            )
        return self._transport(request, self._model, self._api_key)


def _parse_fake_malformed_stages(raw: str | None) -> dict[str, int]:
    if raw is None or raw.strip() == "":
        return {}

    parsed: dict[str, int] = {}
    for segment in raw.split(","):
        token = segment.strip()
        if token == "":
            continue

        if ":" not in token:
            parsed[token] = 1
            continue

        stage, count_text = token.split(":", 1)
        normalized_stage = stage.strip()
        if normalized_stage == "":
            continue

        try:
            count = int(count_text.strip())
        except ValueError:
            count = 1

        parsed[normalized_stage] = max(0, count)

    return parsed


def build_llm_client(env: Mapping[str, str] | None = None) -> LLMClient:
    source = os.environ if env is None else env
    provider = source.get("AUTO_WRITING_LLM_PROVIDER", "fake").strip().lower()

    if provider == "fake":
        return FakeLLMClient(
            model=source.get("AUTO_WRITING_LLM_MODEL", "fake-deterministic-v1"),
            malformed_stages=_parse_fake_malformed_stages(
                source.get("AUTO_WRITING_FAKE_LLM_MALFORMED_STAGES")
            ),
        )

    if provider in {"openai", "real"}:
        return RealProviderLLMClient(
            provider=provider,
            model=source.get("AUTO_WRITING_LLM_MODEL", "gpt-4o-mini"),
            api_key=source.get("AUTO_WRITING_LLM_API_KEY"),
            transport=None,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")
