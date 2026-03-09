from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from auto_writing.worker import WorkflowWorker

from .service import WorkflowStateService


class ChapterRunStatusResponse(BaseModel):
    chapter_run_id: str
    chapter_index: int
    status: str


class NovelRunStatusResponse(BaseModel):
    novel_run_id: str
    status: str
    checkpoint_state: str
    chapter_runs: list[ChapterRunStatusResponse]


router = APIRouter(tags=["workflow"])


def _workflow_service(request: Request) -> WorkflowStateService:
    return request.app.state.workflow_state_service


def _to_status_response(request: Request, novel_run_id: str) -> NovelRunStatusResponse:
    workflow_service = _workflow_service(request)
    run = workflow_service.get_novel_run(novel_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novel run not found")

    chapter_runs = workflow_service.list_chapter_runs(novel_run_id)
    return NovelRunStatusResponse(
        novel_run_id=run.id,
        status=run.status,
        checkpoint_state=run.checkpoint_state,
        chapter_runs=[
            ChapterRunStatusResponse(
                chapter_run_id=chapter.id,
                chapter_index=chapter.chapter_index,
                status=chapter.status,
            )
            for chapter in chapter_runs
        ],
    )


@router.post(
    "/projects/{project_id}/runs",
    response_model=NovelRunStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_project_run(project_id: str, request: Request) -> NovelRunStatusResponse:
    workflow_service = _workflow_service(request)
    try:
        run = workflow_service.trigger_novel_run(project_id=project_id, triggered_by="api")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    worker = WorkflowWorker(
        workflow_service,
        runtime_config=request.app.state.runtime_config,
        project_service=request.app.state.project_service,
        llm_client=request.app.state.llm_client,
    )
    _ = worker.run_for_novel_run(run.id)
    return _to_status_response(request, run.id)


@router.post("/projects/{project_id}/runs/start", response_class=RedirectResponse)
def trigger_project_run_from_console(project_id: str, request: Request) -> RedirectResponse:
    workflow_service = _workflow_service(request)
    try:
        run = workflow_service.trigger_novel_run(project_id=project_id, triggered_by="console")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    worker = WorkflowWorker(
        workflow_service,
        runtime_config=request.app.state.runtime_config,
        project_service=request.app.state.project_service,
        llm_client=request.app.state.llm_client,
    )
    _ = worker.run_for_novel_run(run.id)

    detail_url = request.url_for("read_project", project_id=project_id)
    return RedirectResponse(f"{detail_url}?run_id={run.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/runs/{novel_run_id}", response_model=NovelRunStatusResponse)
def read_novel_run_status(novel_run_id: str, request: Request) -> NovelRunStatusResponse:
    return _to_status_response(request, novel_run_id)
