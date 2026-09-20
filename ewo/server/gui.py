"""/ and /gui — htmx pages and fragments (tailwind + daisyui via CDN)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.core import one_on_ones, people, tasks
from ewo.core.whatnext import rank_tasks
from ewo.db.models import JobRun, TaskPriority, TaskStatus
from ewo.server.deps import get_session

TEMPLATES_DIR = Path(__file__).parent / "templates"

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "tasks": tasks.list_tasks(session),
            "people": people.list_people(session),
            "ranked": rank_tasks(session, limit=5),
            "statuses": list(TaskStatus),
            "priorities": list(TaskPriority),
        },
    )


@router.get("/gui/tasks", response_class=HTMLResponse)
def task_list(request: Request, session: SessionDep, include_closed: bool = False) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_tasks.html",
        {
            "tasks": tasks.list_tasks(session, include_closed=include_closed),
            "statuses": list(TaskStatus),
        },
    )


@router.post("/gui/tasks", response_class=HTMLResponse)
def create_task(
    request: Request,
    session: SessionDep,
    title: Annotated[str, Form()],
    priority: Annotated[str, Form()] = "normal",
    assignee_id: Annotated[str, Form()] = "",
) -> HTMLResponse:
    tasks.create_task(
        session,
        title=title,
        priority=TaskPriority(priority),
        assignee_id=int(assignee_id) if assignee_id else None,
    )
    return task_list(request, session)


@router.post("/gui/tasks/{task_id}/status", response_class=HTMLResponse)
def set_task_status(
    request: Request,
    task_id: int,
    session: SessionDep,
    status: Annotated[str, Form()],
) -> HTMLResponse:
    tasks.update_task(session, task_id, status=TaskStatus(status))
    return task_list(request, session)


@router.get("/gui/people", response_class=HTMLResponse)
def people_list(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_people.html", {"people": people.list_people(session)}
    )


@router.post("/gui/people", response_class=HTMLResponse)
def create_person(
    request: Request,
    session: SessionDep,
    name: Annotated[str, Form()],
    email: Annotated[str, Form()] = "",
) -> HTMLResponse:
    people.create_person(session, name=name, email=email or None)
    return people_list(request, session)


@router.get("/gui/one-on-ones", response_class=HTMLResponse)
def one_on_one_list(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_one_on_ones.html", {"meetings": one_on_ones.list_one_on_ones(session)}
    )


@router.get("/gui/job-runs", response_class=HTMLResponse)
def job_runs(request: Request, session: SessionDep) -> HTMLResponse:
    runs = list(session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(20)))
    return templates.TemplateResponse(request, "_job_runs.html", {"runs": runs})
