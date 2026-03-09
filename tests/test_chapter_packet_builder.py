# pyright: reportMissingImports=false
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import cast

import pytest

from auto_writing.chapter_packet import (
    ChapterPacketBuilder,
    ChapterPacketMissingContextError,
    ChapterPacketRequest,
    ChapterPacketTemplateMissingError,
)


def _fixture_project_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "chapter_packet"


def _base_request(project_dir: Path) -> ChapterPacketRequest:
    return ChapterPacketRequest(
        project_dir=project_dir,
        project_config={
            "name": "Court of Ash",
            "project_id": "proj-001",
            "theme_notes": "Conspiracy and memory",
        },
        chapter_index=3,
        outline="Lina enters the archive and discovers forged seals.",
        goal="Escalate political tension while preserving timeline logic.",
        recent_summaries=[
            "The council fractured after the public trial.",
            "A hidden tunnel connected the archive to the harbor.",
        ],
        constraints=[
            "Do not resolve the central conspiracy in this chapter.",
            "Keep viewpoint limited to Lina.",
        ],
    )


def test_chapter_packet_builder_happy_path_returns_stable_packet_structure() -> None:
    builder = ChapterPacketBuilder()

    packet = builder.build(_base_request(_fixture_project_dir()))
    canon = cast(dict[str, object], packet["canon"])
    placeholders = cast(dict[str, str], canon["placeholders"])
    role_info = cast(list[dict[str, object]], canon["role_info"])
    retrieval_memory = cast(list[str], packet["retrieval_memory"])
    prompt = cast(str, packet["prompt"])

    assert list(packet.keys()) == [
        "outline",
        "goal",
        "canon",
        "recent_summaries",
        "retrieval_memory",
        "constraints",
        "prompt",
    ]
    assert placeholders == {
        "a_rule": "Magic requires a visible source.",
        "z_rule": "No time travel without cost.",
    }
    assert [entry["name"] for entry in role_info] == ["Lina", "Marro"]
    assert retrieval_memory == [
        "Chapter 1 ended with a broken oath and a blood seal.",
        "Chapter 2 revealed the hidden tunnel under the archive.",
    ]
    assert "[System]" in prompt
    assert "Write chapter 3" in prompt


def test_chapter_packet_builder_reports_missing_prompt_template_with_controlled_error() -> None:
    builder = ChapterPacketBuilder()
    request = _base_request(_fixture_project_dir())
    request = replace(request, prompt_template_path="chapter/missing-template.md")

    with pytest.raises(ChapterPacketTemplateMissingError):
        _ = builder.build(request)


def test_chapter_packet_builder_reports_missing_required_context_data_with_controlled_error(
    tmp_path: Path,
) -> None:
    builder = ChapterPacketBuilder()

    missing_summary_request = _base_request(_fixture_project_dir())
    missing_summary_request = replace(missing_summary_request, recent_summaries=[])
    with pytest.raises(ChapterPacketMissingContextError):
        _ = builder.build(missing_summary_request)

    project_dir = tmp_path / "project"
    canon_dir = project_dir / "canon"
    retrieval_dir = project_dir / "retrieval"
    canon_dir.mkdir(parents=True)
    retrieval_dir.mkdir(parents=True)
    _ = (canon_dir / "context.json").write_text(
        json.dumps({"placeholders": {"rule": "Only one moon."}}),
        encoding="utf-8",
    )
    _ = (retrieval_dir / "memory.json").write_text(
        json.dumps({"memory": ["A patrol crossed the bridge at dawn."]}),
        encoding="utf-8",
    )

    missing_role_info_request = _base_request(project_dir)
    with pytest.raises(ChapterPacketMissingContextError):
        _ = builder.build(missing_role_info_request)


def test_chapter_packet_builder_enforces_canon_and_retrieval_path_separation() -> None:
    builder = ChapterPacketBuilder()
    request = _base_request(_fixture_project_dir())
    request = replace(request, retrieval_relative_path=Path("canon/context.json"))

    with pytest.raises(ChapterPacketMissingContextError):
        _ = builder.build(request)
