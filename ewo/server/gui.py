"""/ and /gui — htmx pages and fragments (tailwind + daisyui via CDN).

Pages: dashboard (/), tasks, inbox, people, jobs. Fragments under /gui/* are
swapped in by htmx; every mutation re-renders the fragment it belongs to.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ewo.config import Config
from ewo.core import note_items, one_on_ones, people, tasks
from ewo.core.dashboard import build_dashboard
from ewo.core.llm import LLMClient
from ewo.core.notes import NotesReader
from ewo.core.whatnext import upcoming_deadlines, what_next
from ewo.db.models import (
    JobRun,
    NoteItemKind,
    NoteItemStatus,
    TaskPriority,
    TaskSource,
    TaskStatus,
)
from ewo.jobs.registry import JobRegistry
from ewo.server.deps import get_config, get_llm, get_registry, get_session, get_session_factory

TEMPLATES_DIR = Path(__file__).parent / "templates"

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SessionDep = Annotated[Session, Depends(get_session)]
ConfigDep = Annotated[Config, Depends(get_config)]

_CLOSED = {TaskStatus.DONE, TaskStatus.DROPPED}


def _render(request: Request, name: str, context: dict[str, object]) -> HTMLResponse:
    context.setdefault("statuses", list(TaskStatus))
    context.setdefault("priorities", list(TaskPriority))
    context.setdefault("today", date.today())
    return templates.TemplateResponse(request, name, context)


def _opt_int(value: str) -> int | None:
    return int(value) if value.strip() else None


def _opt_date(value: str) -> date | None:
    return date.fromisoformat(value) if value.strip() else None


# --- dashboard ---


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: SessionDep, config: ConfigDep) -> HTMLResponse:
    return _render(
        request,
        "index.html",
        {
            "page": "dashboard",
            "board": build_dashboard(session, config),
            "next": what_next(session),
            "deadlines": upcoming_deadlines(session),
            "people": people.list_people(session),
        },
    )


# --- tasks ---


def _task_context(
    session: Session,
    status: str = "",
    assignee: str = "",
    tag: str = "",
    source: str = "",
    include_closed: bool = False,
) -> dict[str, object]:
    parsed_status = TaskStatus(status) if status else None
    rows = tasks.list_tasks(
        session,
        status=parsed_status,
        assignee_id=_opt_int(assignee),
        tag=tag or None,
        include_closed=include_closed,
    )
    if source:
        rows = [t for t in rows if t.source.value == source]
    return {
        "tasks": rows,
        "filters": {
            "status": status,
            "assignee": assignee,
            "tag": tag,
            "source": source,
            "include_closed": include_closed,
        },
        "people": people.list_people(session),
        "sources": list(TaskSource),
    }


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(
    request: Request,
    session: SessionDep,
    status: str = "",
    assignee: str = "",
    tag: str = "",
    source: str = "",
    include_closed: bool = False,
) -> HTMLResponse:
    context = _task_context(session, status, assignee, tag, source, include_closed)
    context["page"] = "tasks"
    return _render(request, "tasks.html", context)


@router.get("/gui/tasks", response_class=HTMLResponse)
def task_list(
    request: Request,
    session: SessionDep,
    status: str = "",
    assignee: str = "",
    tag: str = "",
    source: str = "",
    include_closed: bool = False,
) -> HTMLResponse:
    return _render(
        request,
        "_tasks.html",
        _task_context(session, status, assignee, tag, source, include_closed),
    )


@router.post("/gui/tasks", response_class=HTMLResponse)
def create_task(
    request: Request,
    session: SessionDep,
    title: Annotated[str, Form()],
    priority: Annotated[str, Form()] = "normal",
    assignee_id: Annotated[str, Form()] = "",
    due_date: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    compact: Annotated[str, Form()] = "",
) -> HTMLResponse:
    task = tasks.create_task(
        session,
        title=title.strip(),
        priority=TaskPriority(priority),
        assignee_id=_opt_int(assignee_id),
        due_date=_opt_date(due_date),
        tags=[t for t in tags.split(",") if t.strip()],
    )
    if compact:
        return _render(request, "_task_added.html", {"task": task})
    return task_list(request, session)


@router.get("/gui/tasks/{task_id}", response_class=HTMLResponse)
def task_row(request: Request, task_id: int, session: SessionDep) -> HTMLResponse:
    return _render(request, "_task_row.html", {"task": tasks.get_task(session, task_id)})


@router.get("/gui/tasks/{task_id}/edit", response_class=HTMLResponse)
def task_edit(request: Request, task_id: int, session: SessionDep) -> HTMLResponse:
    return _render(
        request,
        "_task_edit.html",
        {"task": tasks.get_task(session, task_id), "people": people.list_people(session)},
    )


@router.post("/gui/tasks/{task_id}", response_class=HTMLResponse)
def task_update(
    request: Request,
    task_id: int,
    session: SessionDep,
    title: Annotated[str, Form()],
    priority: Annotated[str, Form()],
    status: Annotated[str, Form()],
    assignee_id: Annotated[str, Form()] = "",
    due_date: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> HTMLResponse:
    task = tasks.update_task(
        session,
        task_id,
        tags=[t for t in tags.split(",") if t.strip()],
        title=title.strip(),
        priority=TaskPriority(priority),
        status=TaskStatus(status),
        assignee_id=_opt_int(assignee_id),
        due_date=_opt_date(due_date),
        description=description.strip() or None,
    )
    return _render(request, "_task_row.html", {"task": task})


@router.post("/gui/tasks/{task_id}/status", response_class=HTMLResponse)
def set_task_status(
    request: Request,
    task_id: int,
    session: SessionDep,
    status: Annotated[str, Form()],
) -> HTMLResponse:
    task = tasks.update_task(session, task_id, status=TaskStatus(status))
    return _render(request, "_task_row.html", {"task": task})


@router.get("/gui/tasks/{task_id}/detail", response_class=HTMLResponse)
def task_detail(request: Request, task_id: int, session: SessionDep) -> HTMLResponse:
    return _render(request, "_task_detail.html", {"task": tasks.get_task(session, task_id)})


@router.post("/gui/tasks/{task_id}/notes", response_class=HTMLResponse)
def task_add_note(
    request: Request, task_id: int, session: SessionDep, body: Annotated[str, Form()]
) -> HTMLResponse:
    if body.strip():
        tasks.add_note(session, body.strip(), task_id=task_id)
    return task_detail(request, task_id, session)


# --- inbox ---


def _inbox_context(session: Session, status: str = "new", owner: str = "") -> dict[str, object]:
    parsed = None if status == "all" else NoteItemStatus(status)
    items = note_items.list_items(session, status=parsed, owner_id=_opt_int(owner))
    by_path: dict[str, list[object]] = {}
    for item in items:
        by_path.setdefault(item.path, []).append(item)
    return {
        "groups": by_path,
        "count": len(items),
        "filters": {"status": status, "owner": owner},
        "people": people.list_people(session),
        "item_statuses": list(NoteItemStatus),
        "kinds": list(NoteItemKind),
    }


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    status: str = "new",
    owner: str = "",
) -> HTMLResponse:
    context = _inbox_context(session, status, owner)
    context["page"] = "inbox"
    context["notes_enabled"] = config.notes.enabled
    context["last_scan"] = session.scalars(
        select(JobRun)
        .where(JobRun.job_name == "notes_scan")
        .order_by(JobRun.started_at.desc())
        .limit(1)
    ).first()
    return _render(request, "inbox.html", context)


@router.get("/gui/inbox", response_class=HTMLResponse)
def inbox_list(
    request: Request, session: SessionDep, status: str = "new", owner: str = ""
) -> HTMLResponse:
    return _render(request, "_inbox.html", _inbox_context(session, status, owner))


@router.post("/gui/inbox/{item_id}/accept", response_class=HTMLResponse)
def inbox_accept(
    request: Request,
    item_id: int,
    session: SessionDep,
    priority: Annotated[str, Form()] = "normal",
    assignee_id: Annotated[str, Form()] = "",
    due_date: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
) -> HTMLResponse:
    note_items.accept_item(
        session,
        item_id,
        priority=TaskPriority(priority),
        assignee_id=_opt_int(assignee_id),
        due_date=_opt_date(due_date),
        title=title.strip() or None,
    )
    return _render(request, "_inbox_item.html", {"item": note_items.get_item(session, item_id)})


@router.post("/gui/inbox/{item_id}/{action}", response_class=HTMLResponse)
def inbox_resolve(request: Request, item_id: int, action: str, session: SessionDep) -> HTMLResponse:
    status = {"dismiss": NoteItemStatus.DISMISSED, "done": NoteItemStatus.ALREADY_DONE}[action]
    item = note_items.set_item_status(session, item_id, status)
    return _render(request, "_inbox_item.html", {"item": item})


@router.post("/gui/scan", response_class=HTMLResponse)
def run_scan(
    request: Request,
    registry: Annotated[JobRegistry, Depends(get_registry)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    full: Annotated[str, Form()] = "false",
) -> HTMLResponse:
    run = registry.run("notes_scan", session_factory, config, llm, params={"full": full})
    return _render(request, "_scan_result.html", {"run": run})


# --- people ---


def _people_context(session: Session) -> dict[str, object]:
    rows = []
    for person in people.list_people(session):
        meetings = one_on_ones.list_one_on_ones(session, person_id=person.id)
        rows.append(
            {
                "person": person,
                "open_tasks": tasks.list_tasks(session, assignee_id=person.id),
                "inbox_new": len(
                    note_items.list_items(session, status=NoteItemStatus.NEW, owner_id=person.id)
                ),
                "last_meeting": meetings[0] if meetings else None,
            }
        )
    return {"rows": rows}


@router.get("/people", response_class=HTMLResponse)
def people_page(request: Request, session: SessionDep, config: ConfigDep) -> HTMLResponse:
    context = _people_context(session)
    context["page"] = "people"
    context["notes_enabled"] = config.notes.enabled
    return _render(request, "people.html", context)


@router.get("/gui/people", response_class=HTMLResponse)
def people_list(request: Request, session: SessionDep) -> HTMLResponse:
    return _render(request, "_people.html", _people_context(session))


@router.post("/gui/people", response_class=HTMLResponse)
def create_person(
    request: Request,
    session: SessionDep,
    name: Annotated[str, Form()],
    notes_dir: Annotated[str, Form()] = "",
    aliases: Annotated[str, Form()] = "",
    is_self: Annotated[str, Form()] = "",
) -> HTMLResponse:
    people.create_person(
        session,
        name=name.strip(),
        notes_dir=notes_dir.strip() or None,
        aliases=[a.strip() for a in aliases.split(",") if a.strip()],
        is_self=bool(is_self),
    )
    return people_list(request, session)


@router.post("/gui/people/seed", response_class=HTMLResponse)
def seed_people(request: Request, session: SessionDep, config: ConfigDep) -> HTMLResponse:
    if config.notes.enabled:
        reader = NotesReader(config.notes_root)
        people.seed_from_notes(session, reader.people_dirs(config.notes.people_dir))
    return people_list(request, session)


@router.post("/gui/people/{person_id}/self", response_class=HTMLResponse)
def set_self(request: Request, person_id: int, session: SessionDep) -> HTMLResponse:
    people.update_person(session, person_id, is_self=True)
    return people_list(request, session)


@router.get("/gui/one-on-ones", response_class=HTMLResponse)
def one_on_one_list(request: Request, session: SessionDep) -> HTMLResponse:
    return _render(
        request, "_one_on_ones.html", {"meetings": one_on_ones.list_one_on_ones(session)}
    )


# --- jobs ---


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    registry: Annotated[JobRegistry, Depends(get_registry)],
) -> HTMLResponse:
    return _render(
        request,
        "jobs.html",
        {
            "page": "jobs",
            "jobs": [(name, config.jobs.schedules.get(name, "")) for name in registry.names()],
            "runs": _recent_runs(session),
        },
    )


def _recent_runs(session: Session, limit: int = 20) -> list[JobRun]:
    return list(session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)))


@router.get("/gui/job-runs", response_class=HTMLResponse)
def job_runs(request: Request, session: SessionDep) -> HTMLResponse:
    return _render(request, "_job_runs.html", {"runs": _recent_runs(session)})


@router.post("/gui/jobs/{name}/run", response_class=HTMLResponse)
def run_job(
    request: Request,
    name: str,
    registry: Annotated[JobRegistry, Depends(get_registry)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    session: SessionDep,
) -> HTMLResponse:
    registry.run(name, session_factory, config, llm)
    return _render(request, "_job_runs.html", {"runs": _recent_runs(session)})
