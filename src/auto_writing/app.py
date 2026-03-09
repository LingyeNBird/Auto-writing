from __future__ import annotations

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from auto_writing.db.migrations import upgrade_runtime_database
from auto_writing.llm import build_llm_client
from auto_writing.project.routes import router as project_router
from auto_writing.project.service import ProjectService
from auto_writing.workflow.routes import router as workflow_router
from auto_writing.workflow.service import WorkflowStateService

config_module = __import__("auto_writing.config", fromlist=["get_runtime_config"])
get_runtime_config = getattr(config_module, "get_runtime_config")


def _default_template_search_paths() -> tuple[Path, ...]:
    module_path = Path(__file__).resolve()
    candidates: list[Path] = []

    project_root = os.getenv("AUTO_WRITING_PROJECT_ROOT")
    if project_root:
        candidates.append(Path(project_root) / "templates")

    candidates.extend(
        [
            Path.cwd() / "templates",
            Path("/app/templates"),
            module_path.parent / "templates",
            module_path.parents[2] / "templates",
            module_path.parents[1] / "templates",
        ]
    )

    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(resolved)

    return tuple(unique_paths)


def _create_template_environment(search_paths: tuple[Path, ...]) -> Environment:
    return Environment(
        loader=FileSystemLoader([str(path) for path in search_paths]),
        autoescape=select_autoescape(("html", "xml")),
        variable_start_string="[%=",
        variable_end_string="=%]",
    )

def create_app() -> object:
    runtime_config = get_runtime_config()
    fastapi_module = __import__("fastapi", fromlist=["FastAPI"])
    fastapi_class = getattr(fastapi_module, "FastAPI")
    created_app = fastapi_class(title="Auto Writing")

    @created_app.get("/health", tags=["runtime"])
    def read_health() -> dict[str, str]:
        return {"status": "ok"}

    def _upgrade_db_on_startup() -> None:
        upgrade_runtime_database(runtime_config)

    created_app.state.project_service = ProjectService(runtime_config)
    created_app.state.workflow_state_service = WorkflowStateService(runtime_config)
    created_app.state.runtime_config = runtime_config
    created_app.state.llm_client = build_llm_client()
    template_search_paths = _default_template_search_paths()
    created_app.state.template_search_paths = template_search_paths
    created_app.state.template_environment = _create_template_environment(template_search_paths)
    created_app.add_event_handler("startup", _upgrade_db_on_startup)
    created_app.include_router(project_router)
    created_app.include_router(workflow_router)

    return created_app


app = create_app()


def main() -> object:
    return app
