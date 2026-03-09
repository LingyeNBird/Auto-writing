from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
class PromptAssetError(RuntimeError):
    pass


class PromptAssetNotFoundError(PromptAssetError):
    pass


class PromptAssetRenderError(PromptAssetError):
    pass


def default_prompt_assets_dir() -> Path:
    module_path = Path(__file__).resolve()
    candidates: list[Path] = []

    project_root = os.getenv("AUTO_WRITING_PROJECT_ROOT")
    if project_root:
        candidates.append(Path(project_root) / "prompts")

    candidates.extend(
        [
            Path.cwd() / "prompts",
            Path("/app/prompts"),
            module_path.parents[2] / "prompts",
            module_path.parents[1] / "prompts",
        ]
    )

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "system" / "global.md").is_file():
            return resolved

    return (module_path.parents[2] / "prompts").resolve()


class PromptAssetLoader:
    def __init__(self, root_dir: Path | None = None) -> None:
        self._root_dir: Path = (root_dir or default_prompt_assets_dir()).resolve()

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def load(self, asset_path: str) -> str:
        file_path = self._resolve_asset_path(asset_path)
        try:
            return file_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise PromptAssetNotFoundError(f"Prompt asset not found: {asset_path}") from exc

    def render(self, asset_path: str, variables: Mapping[str, object] | None = None) -> str:
        template = self.load(asset_path)
        values = dict(variables or {})
        try:
            return template.format_map(values)
        except KeyError as exc:
            key = str(exc).strip("\"'")
            raise PromptAssetRenderError(f"Missing template variable: {key}") from exc

    def _resolve_asset_path(self, asset_path: str) -> Path:
        relative_path = Path(asset_path)
        if relative_path.is_absolute():
            raise ValueError("Prompt asset path must be relative")

        resolved = (self._root_dir / relative_path).resolve()
        if resolved != self._root_dir and self._root_dir not in resolved.parents:
            raise ValueError("Prompt asset path escapes the configured root directory")
        return resolved
