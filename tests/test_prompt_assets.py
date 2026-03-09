# pyright: reportMissingImports=false
from __future__ import annotations

from pathlib import Path

import pytest

from auto_writing.prompt_assets import (
    PromptAssetLoader,
    PromptAssetNotFoundError,
    PromptAssetRenderError,
)


def test_prompt_asset_loader_loads_repository_prompt_files() -> None:
    loader = PromptAssetLoader()

    template = loader.load("system/global.md")

    assert "Auto Writing system assistant" in template


def test_prompt_asset_loader_renders_template_from_filesystem(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    draft_file = prompt_root / "chapter" / "draft.md"
    draft_file.parent.mkdir(parents=True)
    _ = draft_file.write_text("Draft chapter {chapter_index}", encoding="utf-8")
    loader = PromptAssetLoader(prompt_root)

    rendered = loader.render("chapter/draft.md", {"chapter_index": 3})

    assert rendered == "Draft chapter 3"


def test_prompt_asset_loader_reports_missing_file_with_controlled_error(tmp_path: Path) -> None:
    loader = PromptAssetLoader(tmp_path / "prompts")

    with pytest.raises(PromptAssetNotFoundError):
        _ = loader.load("missing/file.md")


def test_prompt_asset_loader_reports_missing_variable_with_controlled_error(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    draft_file = prompt_root / "chapter" / "draft.md"
    draft_file.parent.mkdir(parents=True)
    _ = draft_file.write_text("Draft chapter {chapter_index}", encoding="utf-8")
    loader = PromptAssetLoader(prompt_root)

    with pytest.raises(PromptAssetRenderError):
        _ = loader.render("chapter/draft.md")


def test_prompt_asset_loader_default_root_prefers_cwd_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_root = tmp_path / "prompts"
    global_prompt = prompt_root / "system" / "global.md"
    global_prompt.parent.mkdir(parents=True)
    _ = global_prompt.write_text("cwd prompt root", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    loader = PromptAssetLoader()

    assert loader.root_dir == prompt_root.resolve()
    assert loader.load("system/global.md") == "cwd prompt root"
