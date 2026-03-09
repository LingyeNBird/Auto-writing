from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from auto_writing.db.models import ChapterRun, ChapterRunTransition, NovelRun, NovelRunTransition, Project
from auto_writing.workflow.service import WorkflowStateService

from .schemas import ProjectCreateRequest, ProjectCreateResponse, ProjectReadResponse
from .service import ProjectService


router = APIRouter(prefix="/projects", tags=["projects"])


@dataclass(frozen=True)
class ConsoleLogEntry:
    created_at: datetime
    scope: str
    from_status: str
    to_status: str
    details: str


def _project_service(request: Request) -> ProjectService:
    return request.app.state.project_service


def _workflow_service(request: Request) -> WorkflowStateService:
    return request.app.state.workflow_state_service


def _render_template(request: Request, template_name: str, context: Mapping[str, object]) -> str:
    template = request.app.state.template_environment.get_template(template_name)
    return template.render(**dict(context))


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or request.headers.get("hx-request", "").lower() == "true"


def _format_dt(value: datetime) -> str:
    return value.isoformat(sep=" ", timespec="seconds")


def _query_for_run(run_id: str | None) -> str:
    if not run_id:
        return ""
    return f"?{urlencode({'run_id': run_id})}"


def _read_form_field(payload: dict[str, list[str]], key: str) -> str:
    return payload.get(key, [""])[0].strip()


def _resolve_run(request: Request, project_id: str, run_id: str | None) -> NovelRun | None:
    workflow_service = _workflow_service(request)
    if run_id:
        requested = workflow_service.get_novel_run(run_id)
        if requested is not None and requested.project_id == project_id:
            return requested

    active = workflow_service.get_active_novel_run(project_id)
    if active is not None:
        return active
    return workflow_service.get_latest_novel_run(project_id)


def _build_logs(
    workflow_service: WorkflowStateService,
    run: NovelRun,
    chapter_runs: list[ChapterRun],
    *,
    limit: int = 12,
) -> list[ConsoleLogEntry]:
    entries: list[ConsoleLogEntry] = []

    for transition in workflow_service.list_novel_run_transitions(run.id):
        details = (transition.output_summary or transition.error_message or "").strip() or "-"
        entries.append(
            ConsoleLogEntry(
                created_at=transition.created_at,
                scope="novel",
                from_status=transition.from_status,
                to_status=transition.to_status,
                details=details,
            )
        )

    for chapter in chapter_runs:
        for transition in workflow_service.list_chapter_run_transitions(chapter.id):
            details = (transition.output_summary or transition.error_message or "").strip() or "-"
            entries.append(
                ConsoleLogEntry(
                    created_at=transition.created_at,
                    scope=f"chapter-{chapter.chapter_index:03d}",
                    from_status=transition.from_status,
                    to_status=transition.to_status,
                    details=details,
                )
            )

    entries.sort(key=lambda entry: entry.created_at, reverse=True)
    return entries[:limit]


def _derive_failure_reason(
    run: NovelRun,
    chapter_runs: list[ChapterRun],
    novel_transitions: list[NovelRunTransition],
    chapter_transitions: list[ChapterRunTransition],
) -> str:
    if run.status != "FAILED":
        return "-"

    for transition in reversed(novel_transitions):
        if transition.to_status == "FAILED":
            reason = (transition.output_summary or transition.error_message or "").strip()
            if reason:
                return reason

    failed_chapter_ids = {chapter.id for chapter in chapter_runs if getattr(chapter, "status") == "FAILED"}
    for transition in reversed(chapter_transitions):
        if transition.chapter_run_id not in failed_chapter_ids:
            continue
        if transition.to_status == "FAILED":
            reason = (transition.output_summary or transition.error_message or "").strip()
            if reason:
                return reason
    return "Run failed without an explicit error summary."


def _build_status_fragment(request: Request, run: NovelRun | None) -> str:
    if run is None:
        return _render_template(
            request,
            "projects/_status.html",
            {
                "run": None,
                "failure_reason": "-",
                "chapter_runs": [],
            },
        )

    workflow_service = _workflow_service(request)
    chapter_runs = workflow_service.list_chapter_runs(run.id)

    novel_transitions = workflow_service.list_novel_run_transitions(run.id)
    chapter_transitions = [
        transition
        for chapter in chapter_runs
        for transition in workflow_service.list_chapter_run_transitions(chapter.id)
    ]
    failure_reason = _derive_failure_reason(run, chapter_runs, novel_transitions, chapter_transitions)

    return _render_template(
        request,
        "projects/_status.html",
        {
            "run": run,
            "failure_reason": failure_reason,
            "chapter_runs": chapter_runs,
        },
    )


def _build_logs_fragment(request: Request, run: NovelRun | None) -> str:
    if run is None:
        return _render_template(
            request,
            "projects/_logs.html",
            {
                "empty_message": "No logs yet. Logs appear after a run starts.",
                "format_dt": _format_dt,
                "logs": [],
            },
        )

    workflow_service = _workflow_service(request)
    chapter_runs = workflow_service.list_chapter_runs(run.id)
    logs = _build_logs(workflow_service, run, chapter_runs)
    return _render_template(
        request,
        "projects/_logs.html",
        {
            "empty_message": "No transitions logged yet.",
            "format_dt": _format_dt,
            "logs": logs,
        },
    )


def _render_project_new_page(
    request: Request,
    *,
    error: str = "",
    name: str = "",
    chapter_count: str = "1",
    theme_notes: str = "",
) -> HTMLResponse:
    html = _render_template(
        request,
        "projects/new.html",
        {
            "error": error,
            "name": name,
            "chapter_count": chapter_count,
            "theme_notes": theme_notes,
        },
    )
    return HTMLResponse(html)


@router.get("/new", response_class=HTMLResponse)
def project_new_page(request: Request) -> HTMLResponse:
    return _render_project_new_page(request)


@router.post("/new", response_class=HTMLResponse)
async def create_project_from_page(request: Request) -> Response:
    body = (await request.body()).decode("utf-8")
    payload = parse_qs(body, keep_blank_values=True)

    name = _read_form_field(payload, "name")
    chapter_count_text = _read_form_field(payload, "chapter_count")
    theme_notes = _read_form_field(payload, "theme_notes")

    if name == "":
        return _render_project_new_page(
            request,
            error="Project name is required.",
            name=name,
            chapter_count=chapter_count_text,
            theme_notes=theme_notes,
        )

    try:
        chapter_count = int(chapter_count_text)
    except ValueError:
        return _render_project_new_page(
            request,
            error="Chapter count must be a positive integer.",
            name=name,
            chapter_count=chapter_count_text,
            theme_notes=theme_notes,
        )
    if chapter_count <= 0:
        return _render_project_new_page(
            request,
            error="Chapter count must be greater than zero.",
            name=name,
            chapter_count=chapter_count_text,
            theme_notes=theme_notes,
        )

    project = _project_service(request).create_project(
        name=name,
        chapter_count=chapter_count,
        theme_notes=theme_notes or None,
    )
    detail_url = request.url_for("read_project", project_id=project.id)
    return RedirectResponse(detail_url, status_code=status.HTTP_303_SEE_OTHER)


def _to_project_read_response(project: Project) -> ProjectReadResponse:
    return ProjectReadResponse(
        id=project.id,
        name=project.name,
        chapter_count=project.chapter_count,
        theme_notes=project.theme_notes,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.post("", response_model=ProjectCreateResponse, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreateRequest, request: Request) -> ProjectCreateResponse:
    project = _project_service(request).create_project(
        name=payload.name,
        chapter_count=payload.chapter_count,
        theme_notes=payload.theme_notes,
    )
    return ProjectCreateResponse(project_id=project.id, status=project.status)


@router.get("", response_model=list[ProjectReadResponse])
def list_projects(request: Request) -> list[ProjectReadResponse]:
    projects = _project_service(request).list_projects()
    return [_to_project_read_response(project) for project in projects]


@router.get("/{project_id}/console/status", response_class=HTMLResponse)
def project_status_fragment(project_id: str, request: Request) -> HTMLResponse:
    project = _project_service(request).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    run_id = request.query_params.get("run_id")
    run = _resolve_run(request, project_id, run_id)
    return HTMLResponse(_build_status_fragment(request, run))


@router.get("/{project_id}/console/logs", response_class=HTMLResponse)
def project_logs_fragment(project_id: str, request: Request) -> HTMLResponse:
    project = _project_service(request).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    run_id = request.query_params.get("run_id")
    run = _resolve_run(request, project_id, run_id)
    return HTMLResponse(_build_logs_fragment(request, run))


@router.get("/{project_id}", response_model=ProjectReadResponse)
def read_project(project_id: str, request: Request) -> ProjectReadResponse | HTMLResponse:
    project = _project_service(request).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if not _wants_html(request):
        return _to_project_read_response(project)

    run_id = request.query_params.get("run_id")
    run = _resolve_run(request, project_id, run_id)
    effective_run_id = run.id if run is not None else run_id
    poll_query = _query_for_run(effective_run_id)
    project_page = _render_template(
        request,
        "projects/detail.html",
        {
            "project_id": project.id,
            "project_name": project.name,
            "project_status": project.status,
            "project_chapter_count": str(project.chapter_count),
            "project_theme_notes": (project.theme_notes or "-").strip() or "-",
            "project_created_at": _format_dt(project.created_at),
            "project_updated_at": _format_dt(project.updated_at),
            "run_start_action": f"/projects/{project.id}/runs/start",
            "status_poll_url": f"/projects/{project.id}/console/status{poll_query}",
            "logs_poll_url": f"/projects/{project.id}/console/logs{poll_query}",
        },
    )
    return HTMLResponse(project_page)
