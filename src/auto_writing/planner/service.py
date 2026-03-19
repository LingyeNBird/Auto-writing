from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re

from auto_writing.config import RuntimeConfig
from auto_writing.db.models import Project
from auto_writing.llm import LLMClient, LLMRequest
from auto_writing.prompt_assets import PromptAssetLoader
from auto_writing.workflow.states import NovelRunState


_DEFAULT_THEME_NOTES = "Keep continuity stable."
_VERSION_PATTERN = re.compile(r"^(?P<name>[a-z0-9_]+)_v(?P<version>\d+)\.(?P<ext>[a-z0-9]+)$")


@dataclass(frozen=True)
class PlannerStepResult:
    output_summary: str | None = None


class PlannerService:
    def __init__(
        self,
        *,
        runtime_config: RuntimeConfig,
        llm_client: LLMClient,
        prompt_loader: PromptAssetLoader | None = None,
    ) -> None:
        self._runtime_config = runtime_config
        self._llm_client = llm_client
        self._prompt_loader = prompt_loader or PromptAssetLoader()

    def prepare_for_transition(
        self,
        *,
        project: Project,
        target_state: NovelRunState,
    ) -> PlannerStepResult:
        if target_state == NovelRunState.INPUT_NORMALIZED:
            theme_notes = self._theme_notes(project.theme_notes)
            return PlannerStepResult(output_summary=f"normalized project brief for {theme_notes}")

        if target_state == NovelRunState.BIBLE_READY:
            return self._generate_story_bible_and_world(project)

        if target_state == NovelRunState.CHARACTERS_READY:
            return self._generate_characters(project)

        if target_state == NovelRunState.MASTER_OUTLINE_READY:
            return self._generate_outlines(project)

        return PlannerStepResult()

    def _generate_story_bible_and_world(self, project: Project) -> PlannerStepResult:
        project_dir = self._project_dir(project.id)
        metadata = self._project_metadata(project)
        premise_prompt = self._prompt_loader.render(
            "planner/premise.md",
            {
                "project_name": project.name,
                "theme_notes": metadata["theme_notes"],
                "chapter_count": metadata["chapter_count"],
            },
        )
        premise_payload = self._generate_json(
            stage="planner-premise",
            prompt=premise_prompt,
            metadata=metadata,
        )
        story_bible_markdown = self._require_text(
            premise_payload.get("story_bible_markdown"),
            field_name="story_bible_markdown",
        )
        story_bible_path = self._write_text_artifact(
            directory=project_dir,
            name="story_bible",
            extension="md",
            content=story_bible_markdown,
        )

        world_prompt = self._prompt_loader.render(
            "planner/world.md",
            {
                "project_name": project.name,
                "theme_notes": metadata["theme_notes"],
                "story_bible": story_bible_markdown,
            },
        )
        world_payload = self._generate_json(
            stage="planner-world",
            prompt=world_prompt,
            metadata=metadata,
        )
        rules = self._require_object_list(world_payload.get("rules"), field_name="rules")
        locations = self._require_object_list(world_payload.get("locations"), field_name="locations")
        rules_path = self._write_yaml_artifact(
            directory=project_dir / "world",
            name="rules",
            root_key="rules",
            entries=rules,
        )
        locations_path = self._write_yaml_artifact(
            directory=project_dir / "world",
            name="locations",
            root_key="locations",
            entries=locations,
        )

        canon_payload = self._load_canon_context(project_dir)
        placeholders: dict[str, str] = self._require_string_mapping(
            canon_payload.get("placeholders"),
            "canon.placeholders",
        )
        placeholders.update(self._require_string_mapping(premise_payload.get("canon_placeholders"), "canon_placeholders"))
        placeholders.update(self._require_string_mapping(world_payload.get("canon_placeholders"), "canon_placeholders"))
        role_info: list[dict[str, object]] = self._normalize_role_info(canon_payload.get("role_info"))
        self._write_canon_context(project_dir, placeholders=placeholders, role_info=role_info)

        return PlannerStepResult(
            output_summary=", ".join(
                [
                    story_bible_path.name,
                    rules_path.name,
                    locations_path.name,
                ]
            )
        )

    def _generate_characters(self, project: Project) -> PlannerStepResult:
        project_dir = self._project_dir(project.id)
        metadata = self._project_metadata(project)
        characters_prompt = self._prompt_loader.render(
            "planner/characters.md",
            {
                "project_name": project.name,
                "theme_notes": metadata["theme_notes"],
                "story_bible": self._latest_text_artifact(
                    directory=project_dir,
                    name="story_bible",
                    extension="md",
                ),
                "world_rules": self._latest_text_artifact(
                    directory=project_dir / "world",
                    name="rules",
                    extension="yaml",
                ),
                "world_locations": self._latest_text_artifact(
                    directory=project_dir / "world",
                    name="locations",
                    extension="yaml",
                ),
            },
        )
        characters_payload = self._generate_json(
            stage="planner-characters",
            prompt=characters_prompt,
            metadata=metadata,
        )
        raw_characters = self._require_object_list(
            characters_payload.get("characters"),
            field_name="characters",
        )

        character_file_names: list[str] = []
        for entry in raw_characters:
            file_stem = self._require_file_stem(entry.get("file_stem"))
            card_markdown = self._require_text(entry.get("card_markdown"), field_name="card_markdown")
            path = self._write_text_artifact(
                directory=project_dir / "characters",
                name=file_stem,
                extension="md",
                content=card_markdown,
            )
            character_file_names.append(path.name)

        canon_payload = self._load_canon_context(project_dir)
        role_info: list[dict[str, object]] = self._normalize_role_info(characters_payload.get("role_info"))
        placeholders: dict[str, str] = self._require_string_mapping(
            canon_payload.get("placeholders"),
            "canon.placeholders",
        )
        self._write_canon_context(
            project_dir,
            placeholders=placeholders,
            role_info=role_info,
        )
        return PlannerStepResult(output_summary=", ".join(character_file_names))

    def _generate_outlines(self, project: Project) -> PlannerStepResult:
        project_dir = self._project_dir(project.id)
        metadata = self._project_metadata(project)
        master_outline_prompt = self._prompt_loader.render(
            "planner/master_outline.md",
            {
                "project_name": project.name,
                "theme_notes": metadata["theme_notes"],
                "chapter_count": metadata["chapter_count"],
                "story_bible": self._latest_text_artifact(
                    directory=project_dir,
                    name="story_bible",
                    extension="md",
                ),
                "characters": self._joined_character_cards(project_dir),
            },
        )
        master_outline_payload = self._generate_json(
            stage="planner-master-outline",
            prompt=master_outline_prompt,
            metadata=metadata,
        )
        master_outline_markdown = self._require_text(
            master_outline_payload.get("master_outline_markdown"),
            field_name="master_outline_markdown",
        )
        master_outline_path = self._write_text_artifact(
            directory=project_dir / "outlines",
            name="master_outline",
            extension="md",
            content=master_outline_markdown,
        )

        chapter_outline_prompt = self._prompt_loader.render(
            "planner/chapter_outline.md",
            {
                "project_name": project.name,
                "chapter_index": 1,
                "master_outline": master_outline_markdown,
            },
        )
        chapter_outline_markdown = self._generate_text(
            stage="planner-chapter-outline",
            prompt=chapter_outline_prompt,
            metadata={**metadata, "chapter_index": "1"},
        )
        chapter_outline_path = self._write_text_artifact(
            directory=project_dir / "outlines",
            name="chapter_001",
            extension="md",
            content=chapter_outline_markdown,
        )
        return PlannerStepResult(
            output_summary=", ".join([master_outline_path.name, chapter_outline_path.name])
        )

    def _generate_json(
        self,
        *,
        stage: str,
        prompt: str,
        metadata: Mapping[str, str],
    ) -> dict[str, object]:
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
            raise RuntimeError(f"Planner stage '{stage}' returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Planner stage '{stage}' must return a JSON object")
        return {str(key): value for key, value in payload.items()}

    def _generate_text(
        self,
        *,
        stage: str,
        prompt: str,
        metadata: Mapping[str, str],
    ) -> str:
        response = self._llm_client.generate(
            LLMRequest(
                stage=stage,
                prompt=prompt,
                response_format="text",
                metadata=metadata,
            )
        )
        return self._require_text(response.output_text, field_name=stage)

    def _project_dir(self, project_id: str) -> Path:
        return self._runtime_config.data_dir / "projects" / project_id

    def _project_metadata(self, project: Project) -> dict[str, str]:
        return {
            "project_id": project.id,
            "project_name": project.name,
            "chapter_count": str(project.chapter_count),
            "theme_notes": self._theme_notes(project.theme_notes),
        }

    @staticmethod
    def _theme_notes(value: str | None) -> str:
        normalized = (value or "").strip()
        return normalized or _DEFAULT_THEME_NOTES

    def _load_canon_context(self, project_dir: Path) -> dict[str, object]:
        canon_path = project_dir / "canon" / "context.json"
        try:
            raw_content = canon_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise RuntimeError("Missing required context data: canon/context.json") from exc
        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Invalid context data format for canon/context.json") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("canon/context.json must contain a JSON object")
        return {str(key): value for key, value in payload.items()}

    def _write_canon_context(
        self,
        project_dir: Path,
        *,
        placeholders: Mapping[str, str],
        role_info: Sequence[Mapping[str, object]],
    ) -> None:
        canon_path = project_dir / "canon" / "context.json"
        payload = {
            "placeholders": {key: placeholders[key] for key in sorted(placeholders)},
            "role_info": [dict(item) for item in role_info],
        }
        canon_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    def _joined_character_cards(self, project_dir: Path) -> str:
        latest_by_stem: dict[str, tuple[int, Path]] = {}
        for path in sorted((project_dir / "characters").glob("*_v*.md")):
            match = _VERSION_PATTERN.match(path.name)
            if match is None or match.group("ext") != "md":
                continue
            stem = match.group("name")
            version = int(match.group("version"))
            current = latest_by_stem.get(stem)
            if current is None or version > current[0]:
                latest_by_stem[stem] = (version, path)

        cards: list[str] = []
        for stem in sorted(latest_by_stem):
            cards.append(latest_by_stem[stem][1].read_text(encoding="utf-8").strip())
        if len(cards) == 0:
            raise RuntimeError("Character cards are required before outline generation")
        return "\n\n".join(cards)

    def _latest_text_artifact(self, *, directory: Path, name: str, extension: str) -> str:
        latest = self._latest_artifact_path(directory=directory, name=name, extension=extension)
        if latest is None:
            raise RuntimeError(f"Missing planner artifact: {name}")
        return latest.read_text(encoding="utf-8").strip()

    def _latest_artifact_path(self, *, directory: Path, name: str, extension: str) -> Path | None:
        highest_version = 0
        selected: Path | None = None
        for path in directory.glob(f"{name}_v*.{extension}"):
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

    def _next_artifact_path(self, *, directory: Path, name: str, extension: str) -> Path:
        latest = self._latest_artifact_path(directory=directory, name=name, extension=extension)
        next_version = 1
        if latest is not None:
            match = _VERSION_PATTERN.match(latest.name)
            if match is not None:
                next_version = int(match.group("version")) + 1
        return directory / f"{name}_v{next_version}.{extension}"

    def _write_text_artifact(self, *, directory: Path, name: str, extension: str, content: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = self._next_artifact_path(directory=directory, name=name, extension=extension)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return path

    def _write_yaml_artifact(
        self,
        *,
        directory: Path,
        name: str,
        root_key: str,
        entries: Sequence[Mapping[str, str]],
    ) -> Path:
        lines = [f"{root_key}:"]
        for entry in entries:
            lines.append("  -")
            for key in sorted(entry):
                lines.append(f"      {key}: {self._yaml_scalar(entry[key])}")
        content = "\n".join(lines)
        return self._write_text_artifact(directory=directory, name=name, extension="yaml", content=content)

    @staticmethod
    def _yaml_scalar(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    def _require_text(value: object, *, field_name: str) -> str:
        if not isinstance(value, str):
            raise RuntimeError(f"Planner field '{field_name}' must be text")
        normalized = value.strip()
        if normalized == "":
            raise RuntimeError(f"Planner field '{field_name}' must not be empty")
        return normalized

    @staticmethod
    def _require_object_list(value: object, field_name: str) -> list[dict[str, str]]:
        if not isinstance(value, Sequence) or isinstance(value, str) or len(value) == 0:
            raise RuntimeError(f"Planner field '{field_name}' must be a non-empty list")
        normalized: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise RuntimeError(f"Planner field '{field_name}' entries must be objects")
            normalized.append(
                {
                    str(key): PlannerService._require_text(item[key], field_name=f"{field_name}.{key}")
                    for key in sorted(item)
                }
            )
        return normalized

    @staticmethod
    def _require_string_mapping(value: object, field_name: str) -> dict[str, str]:
        if not isinstance(value, Mapping) or len(value) == 0:
            raise RuntimeError(f"Planner field '{field_name}' must be a non-empty object")
        return {
            str(key): PlannerService._require_text(item, field_name=f"{field_name}.{key}")
            for key, item in sorted(value.items())
        }

    @staticmethod
    def _normalize_role_info(value: object) -> list[dict[str, object]]:
        if not isinstance(value, Sequence) or isinstance(value, str) or len(value) == 0:
            raise RuntimeError("Planner field 'role_info' must be a non-empty list")
        normalized: list[dict[str, object]] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise RuntimeError("Planner field 'role_info' entries must be objects")
            name = PlannerService._require_text(item.get("name"), field_name="role_info.name")
            role = PlannerService._require_text(item.get("role"), field_name="role_info.role")
            goal = PlannerService._require_text(item.get("goal"), field_name="role_info.goal")
            normalized.append({"goal": goal, "name": name, "role": role})
        return normalized

    @staticmethod
    def _require_file_stem(value: object) -> str:
        if not isinstance(value, str):
            raise RuntimeError("Planner character file_stem must be text")
        normalized = value.strip()
        if re.fullmatch(r"[a-z0-9_]+", normalized) is None:
            raise RuntimeError("Planner character file_stem must use lowercase letters, digits, and underscores")
        return normalized
