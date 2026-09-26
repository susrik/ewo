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
from ewo.core import nuggets, one_on_ones, people, task_organize, tasks
from ewo.core.dashboard import build_dashboard
from ewo.core.llm import LLMClient
from ewo.core.notes import NotesReader
from ewo.core.whatnext import upcoming_deadlines, what_next
from ewo.db.models import (
    JobRun,
    Nugget,
    NuggetKind,
    NuggetStatus,
    Task,
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


def _jira_base_url(config: Config) -> str | None:
    return config.jira.base_url or None


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


def _task_tree(rows: list[Task]) -> list[tuple[Task, int]]:
    """(task, depth) pairs in display order: children right after their parent.

    Tasks whose parent is filtered out are shown as roots.
    """
    by_parent: dict[int | None, list[Task]] = {}
    for row in rows:
        by_parent.setdefault(row.parent_id, []).append(row)
    ids = {row.id for row in rows}
    ordered: list[tuple[Task, int]] = []

    def emit(task: Task, depth: int) -> None:
        ordered.append((task, depth))
        for child in by_parent.get(task.id, []):
            emit(child, depth + 1)

    for row in rows:
        if row.parent_id is None or row.parent_id not in ids:
            emit(row, 0)
    return ordered


def _descendant_ids(task: Task) -> set[int]:
    ids: set[int] = set()
    stack = list(task.children)
    while stack:
        child = stack.pop()
        ids.add(child.id)
        stack.extend(child.children)
    return ids


def _parent_candidates(session: Session, task: Task) -> list[Task]:
    """Open tasks that may be *task*'s parent (not itself, not its descendants)."""
    excluded = _descendant_ids(task) | {task.id}
    candidates = [t for t in tasks.list_tasks(session) if t.id not in excluded]
    if task.parent is not None and task.parent.id not in {t.id for t in candidates}:
        candidates.insert(0, task.parent)
    return candidates


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
        "rows": _task_tree(rows),
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
    config: ConfigDep,
    status: str = "",
    assignee: str = "",
    tag: str = "",
    source: str = "",
    include_closed: bool = False,
) -> HTMLResponse:
    context = _task_context(session, status, assignee, tag, source, include_closed)
    context["page"] = "tasks"
    context["llm_enabled"] = bool(config.llm.api_key)
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
    parent_id: Annotated[str, Form()] = "",
    start_date: Annotated[str, Form()] = "",
    due_date: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    compact: Annotated[str, Form()] = "",
) -> HTMLResponse:
    task = tasks.create_task(
        session,
        title=title.strip(),
        priority=TaskPriority(priority),
        assignee_id=_opt_int(assignee_id),
        parent_id=_opt_int(parent_id),
        start_date=_opt_date(start_date),
        due_date=_opt_date(due_date),
        tags=[t for t in tags.split(",") if t.strip()],
    )
    if compact:
        return _render(request, "_task_added.html", {"task": task})
    return task_list(request, session)


# --- task organization (AI) — declared before /gui/tasks/{task_id} so the
# literal "organize" path wins over the id parameter ---


@router.post("/gui/tasks/organize", response_class=HTMLResponse)
def tasks_organize(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
) -> HTMLResponse:
    context: dict[str, object] = {"proposals": [], "error": None}
    if not config.llm.api_key:
        context["error"] = "No LLM API key configured."
    else:
        try:
            proposals, _tokens = task_organize.propose_organization(session, llm)
            context["proposals"] = proposals
        except ValueError as exc:
            context["error"] = str(exc)
    return _render(request, "_organize.html", context)


def _tasks_after_organize(request: Request, session: Session) -> HTMLResponse:
    """Re-render the task list and clear the proposals panel (out-of-band)."""
    context = _task_context(session)
    return _render(request, "_tasks_oob.html", context)


@router.post("/gui/tasks/organize/merge", response_class=HTMLResponse)
def organize_merge(
    request: Request,
    session: SessionDep,
    into_id: Annotated[int, Form()],
    from_id: Annotated[int, Form()],
) -> HTMLResponse:
    task_organize.apply_merge(session, into_id, from_id)
    return _tasks_after_organize(request, session)


@router.post("/gui/tasks/organize/split", response_class=HTMLResponse)
def organize_split(
    request: Request,
    session: SessionDep,
    task_id: Annotated[int, Form()],
    title: Annotated[str, Form()],
    nugget_ids: Annotated[str, Form()] = "",
) -> HTMLResponse:
    ids = [int(part) for part in nugget_ids.split(",") if part.strip()]
    task_organize.apply_split(session, task_id, ids, title.strip())
    return _tasks_after_organize(request, session)


@router.post("/gui/tasks/organize/create", response_class=HTMLResponse)
def organize_create(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    title: Annotated[str, Form()],
    nugget_ids: Annotated[str, Form()] = "",
) -> HTMLResponse:
    ids = [int(part) for part in nugget_ids.split(",") if part.strip()]
    task_organize.apply_create(session, title.strip(), ids, jira_base_url=_jira_base_url(config))
    return _tasks_after_organize(request, session)


@router.post("/gui/tasks/organize/retitle", response_class=HTMLResponse)
def organize_retitle(
    request: Request,
    session: SessionDep,
    task_id: Annotated[int, Form()],
    title: Annotated[str, Form()],
) -> HTMLResponse:
    tasks.update_task(session, task_id, title=title.strip())
    return _tasks_after_organize(request, session)


@router.get("/gui/tasks/{task_id}", response_class=HTMLResponse)
def task_row(request: Request, task_id: int, session: SessionDep) -> HTMLResponse:
    return _render(request, "_task_row.html", {"task": tasks.get_task(session, task_id)})


@router.get("/gui/tasks/{task_id}/edit", response_class=HTMLResponse)
def task_edit(request: Request, task_id: int, session: SessionDep) -> HTMLResponse:
    task = tasks.get_task(session, task_id)
    return _render(
        request,
        "_task_edit.html",
        {
            "task": task,
            "people": people.list_people(session),
            "parent_candidates": _parent_candidates(session, task),
        },
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
    parent_id: Annotated[str, Form()] = "",
    start_date: Annotated[str, Form()] = "",
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
        parent_id=_opt_int(parent_id),
        start_date=_opt_date(start_date),
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


def _detail_context(session: Session, task_id: int) -> dict[str, object]:
    return {
        "task": tasks.get_task(session, task_id),
        "open_tasks": tasks.list_tasks(session),
        "kinds": list(NuggetKind),
    }


@router.get("/gui/tasks/{task_id}/detail", response_class=HTMLResponse)
def task_detail(request: Request, task_id: int, session: SessionDep) -> HTMLResponse:
    return _render(request, "_task_detail.html", _detail_context(session, task_id))


@router.post("/gui/tasks/{task_id}/notes", response_class=HTMLResponse)
def task_add_note(
    request: Request, task_id: int, session: SessionDep, body: Annotated[str, Form()]
) -> HTMLResponse:
    if body.strip():
        tasks.add_note(session, body.strip(), task_id=task_id)
    return task_detail(request, task_id, session)


@router.post("/gui/tasks/{task_id}/children", response_class=HTMLResponse)
def task_add_child(
    request: Request, task_id: int, session: SessionDep, title: Annotated[str, Form()]
) -> HTMLResponse:
    if title.strip():
        tasks.create_task(session, title=title.strip(), parent_id=task_id)
    return task_detail(request, task_id, session)


@router.post("/gui/tasks/{task_id}/links", response_class=HTMLResponse)
def task_add_links(
    request: Request,
    task_id: int,
    session: SessionDep,
    config: ConfigDep,
    jira_keys: Annotated[str, Form()] = "",
) -> HTMLResponse:
    task = tasks.get_task(session, task_id)
    keys = nuggets.find_jira_keys(jira_keys.upper())
    if keys:
        nuggets.ensure_jira_links(session, task, keys, _jira_base_url(config))
        session.commit()
    return task_detail(request, task_id, session)


@router.post("/gui/tasks/{task_id}/links/{link_id}/delete", response_class=HTMLResponse)
def task_delete_link(
    request: Request, task_id: int, link_id: int, session: SessionDep
) -> HTMLResponse:
    tasks.unlink_external(session, task_id, link_id)
    return task_detail(request, task_id, session)


# --- inbox (nuggets) ---


def _inbox_context(session: Session, status: str = "new", owner: str = "") -> dict[str, object]:
    parsed = None if status == "all" else NuggetStatus(status)
    items = nuggets.list_nuggets(session, status=parsed, owner_id=_opt_int(owner))
    open_tasks = tasks.list_tasks(session)
    open_by_id = {task.id: task for task in open_tasks}
    groups: list[dict[str, object]] = []
    if parsed == NuggetStatus.NEW:
        # pre-organized: one group per suggested task, then everything else
        suggested: dict[int, list[Nugget]] = {}
        rest: list[Nugget] = []
        for item in items:
            if item.suggested_task_id is not None and item.suggested_task_id in open_by_id:
                suggested.setdefault(item.suggested_task_id, []).append(item)
            else:
                rest.append(item)
        for task_id, group_items in sorted(
            suggested.items(), key=lambda kv: open_by_id[kv[0]].title.lower()
        ):
            groups.append({"kind": "task", "task": open_by_id[task_id], "nuggets": group_items})
        if rest:
            groups.append({"kind": "new", "nuggets": rest})
    else:
        by_path: dict[str, list[Nugget]] = {}
        for item in items:
            by_path.setdefault(item.path, []).append(item)
        groups = [
            {"kind": "path", "path": path, "nuggets": group} for path, group in by_path.items()
        ]
    return {
        "groups": groups,
        "count": len(items),
        "filters": {"status": status, "owner": owner},
        "people": people.list_people(session),
        "open_tasks": open_tasks,
        "item_statuses": list(NuggetStatus),
        "kinds": list(NuggetKind),
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
    context["llm_enabled"] = bool(config.llm.api_key)
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


@router.post("/gui/inbox/suggest", response_class=HTMLResponse)
def inbox_suggest(
    request: Request,
    registry: Annotated[JobRegistry, Depends(get_registry)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    session: SessionDep,
    status: Annotated[str, Form()] = "new",
    owner: Annotated[str, Form()] = "",
) -> HTMLResponse:
    registry.run("nuggets_match", session_factory, config, llm)
    return _render(request, "_inbox.html", _inbox_context(session, status, owner))


@router.post("/gui/inbox/attach-group", response_class=HTMLResponse)
def inbox_attach_group(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    task_id: Annotated[int, Form()],
    status: Annotated[str, Form()] = "new",
    owner: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Attach every listed nugget suggested for *task_id* in one click."""
    items = nuggets.list_nuggets(session, status=NuggetStatus.NEW, owner_id=_opt_int(owner))
    for item in items:
        if item.suggested_task_id == task_id:
            nuggets.attach_nugget(
                session, item.id, task_id=task_id, jira_base_url=_jira_base_url(config)
            )
    return _render(request, "_inbox.html", _inbox_context(session, status, owner))


@router.post("/gui/nuggets/{nugget_id}/attach", response_class=HTMLResponse)
def nugget_attach(
    request: Request,
    nugget_id: int,
    session: SessionDep,
    config: ConfigDep,
    task_id: Annotated[str, Form()] = "",
    priority: Annotated[str, Form()] = "normal",
    assignee_id: Annotated[str, Form()] = "",
    due_date: Annotated[str, Form()] = "",
) -> HTMLResponse:
    nuggets.attach_nugget(
        session,
        nugget_id,
        task_id=_opt_int(task_id),
        priority=TaskPriority(priority),
        assignee_id=_opt_int(assignee_id),
        due_date=_opt_date(due_date),
        jira_base_url=_jira_base_url(config),
    )
    return _render(request, "_nugget.html", {"nugget": nuggets.get_nugget(session, nugget_id)})


@router.post("/gui/nuggets/{nugget_id}/edit", response_class=HTMLResponse)
def nugget_edit(
    request: Request,
    nugget_id: int,
    session: SessionDep,
    summary: Annotated[str, Form()],
    kind: Annotated[str, Form()] = "",
    due_date: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Edit a nugget's text from the task detail panel; re-render the panel."""
    nugget = nuggets.get_nugget(session, nugget_id)
    task_id = nugget.task_id
    fields: dict[str, object] = {"summary": summary.strip()}
    if kind:
        fields["kind"] = NuggetKind(kind)
    fields["due_date"] = _opt_date(due_date)
    nuggets.update_nugget(session, nugget_id, **fields)
    return _render(request, "_task_detail.html", _detail_context(session, task_id))  # type: ignore[arg-type]


@router.post("/gui/nuggets/{nugget_id}/move", response_class=HTMLResponse)
def nugget_move(
    request: Request,
    nugget_id: int,
    session: SessionDep,
    task_id: Annotated[int, Form()],
) -> HTMLResponse:
    """Move a nugget to another task; re-render the (now former) task panel."""
    nugget = nuggets.get_nugget(session, nugget_id)
    source_task_id = nugget.task_id
    nuggets.move_nugget(session, nugget_id, task_id)
    return _render(request, "_task_detail.html", _detail_context(session, source_task_id))  # type: ignore[arg-type]


@router.post("/gui/nuggets/{nugget_id}/detach", response_class=HTMLResponse)
def nugget_detach(request: Request, nugget_id: int, session: SessionDep) -> HTMLResponse:
    """Remove a nugget from its task (back to the inbox); re-render the panel."""
    nugget = nuggets.get_nugget(session, nugget_id)
    source_task_id = nugget.task_id
    nuggets.detach_nugget(session, nugget_id)
    return _render(request, "_task_detail.html", _detail_context(session, source_task_id))  # type: ignore[arg-type]


# declared last: the catch-all action route would otherwise shadow the
# specific /edit /move /detach routes above


@router.post("/gui/nuggets/{nugget_id}/{action}", response_class=HTMLResponse)
def nugget_resolve(
    request: Request, nugget_id: int, action: str, session: SessionDep
) -> HTMLResponse:
    status = {"dismiss": NuggetStatus.DISMISSED, "done": NuggetStatus.ALREADY_DONE}[action]
    nugget = nuggets.set_nugget_status(session, nugget_id, status)
    return _render(request, "_nugget.html", {"nugget": nugget})


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
                    nuggets.list_nuggets(session, status=NuggetStatus.NEW, owner_id=person.id)
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
