"""/ and /gui — htmx pages and fragments (tailwind + daisyui via CDN).

Pages: dashboard (/), tasks, inbox, people, jobs. Fragments under /gui/* are
swapped in by htmx; every mutation re-renders the fragment it belongs to.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ewo.config import Config
from ewo.core import colors, nuggets, one_on_ones, people, preferences, task_organize, tasks
from ewo.core import tags as tags_core
from ewo.core.dashboard import build_dashboard
from ewo.core.llm import LLMClient
from ewo.core.notes import NotesReader
from ewo.core.people import NotFoundError
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
templates.env.filters["pillbox_style"] = colors.label_swatch_to_css

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


def _merge_candidates(session: Session, task: Task) -> list[Task]:
    """Open tasks *task* may be merged into (not itself, not its descendants)."""
    excluded = _descendant_ids(task) | {task.id}
    return [t for t in tasks.list_tasks(session) if t.id not in excluded]


def _task_context(
    session: Session,
    status: str = "",
    assignee: str = "",
    tag: str = "",
    tags: list[str] | None = None,
    tag_match: Literal["any", "all"] = "any",
    source: str = "",
    include_closed: bool = False,
    sort: str = "priority",
    sort2: str = "title",
    reverse: bool = False,
) -> dict[str, object]:
    parsed_status = TaskStatus(status) if status else None
    selected_tags = [t for t in (tags or []) if t.strip()]
    rows = tasks.list_tasks(
        session,
        status=parsed_status,
        assignee_id=_opt_int(assignee),
        tag=tag or None,
        tags=selected_tags or None,
        tag_match=tag_match,
        include_closed=include_closed,
    )
    if source:
        rows = [t for t in rows if t.source.value == source]
    rows = tasks.sort_tasks(rows, sort, sort2, reverse)
    return {
        "rows": rows,
        "filters": {
            "status": status,
            "assignee": assignee,
            "tag": tag,
            "tags": selected_tags,
            "tag_match": tag_match,
            "source": source,
            "include_closed": include_closed,
            "sort": sort,
            "sort2": sort2,
            "reverse": reverse,
        },
        "people": people.list_people(session),
        "sources": list(TaskSource),
        "all_tags": tags_core.list_tags(session),
        "sort_fields": list(tasks.SORT_FIELDS),
    }


_FILTER_KEYS = (
    "status",
    "assignee",
    "tag",
    "tags",
    "tag_match",
    "source",
    "include_closed",
    "sort",
    "sort2",
    "reverse",
)
_FILTER_PREF_KEY = "tasks_filter"


def _saved_filter(request: Request, session: Session) -> dict[str, object] | None:
    """The persisted task-list filter, applied only when none was passed."""
    if any(key in request.query_params for key in _FILTER_KEYS):
        return None
    saved = preferences.get_pref(session, _FILTER_PREF_KEY)
    return saved if isinstance(saved, dict) else None


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    status: str = "",
    assignee: str = "",
    tag: str = "",
    tags: Annotated[list[str] | None, Query()] = None,
    tag_match: Literal["any", "all"] = "any",
    source: str = "",
    include_closed: bool = False,
) -> HTMLResponse:
    saved = _saved_filter(request, session)
    if saved is not None:
        status = str(saved.get("status", status))
        assignee = str(saved.get("assignee", assignee))
        tag = str(saved.get("tag", tag))
        tags = saved.get("tags") if isinstance(saved.get("tags"), list) else tags  # type: ignore[assignment]
        tag_match = str(saved.get("tag_match", tag_match))  # type: ignore[assignment]
        source = str(saved.get("source", source))
        include_closed = bool(saved.get("include_closed", include_closed))
    context = _task_context(session, status, assignee, tag, tags, tag_match, source, include_closed)
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
    tags: Annotated[list[str] | None, Query()] = None,
    tag_match: Literal["any", "all"] = "any",
    source: str = "",
    include_closed: bool = False,
    sort: str = "priority",
    sort2: str = "title",
    reverse: bool = False,
) -> HTMLResponse:
    saved = _saved_filter(request, session)
    if saved is not None:
        status = str(saved.get("status", status))
        assignee = str(saved.get("assignee", assignee))
        tag = str(saved.get("tag", tag))
        tags = saved.get("tags") if isinstance(saved.get("tags"), list) else tags  # type: ignore[assignment]
        tag_match = str(saved.get("tag_match", tag_match))  # type: ignore[assignment]
        source = str(saved.get("source", source))
        include_closed = bool(saved.get("include_closed", include_closed))
        sort = str(saved.get("sort", sort))
        sort2 = str(saved.get("sort2", sort2))
        reverse = bool(saved.get("reverse", reverse))
    context = _task_context(
        session,
        status,
        assignee,
        tag,
        tags,
        tag_match,
        source,
        include_closed,
        sort,
        sort2,
        reverse,
    )
    if any(key in request.query_params for key in _FILTER_KEYS):
        preferences.set_pref(session, _FILTER_PREF_KEY, cast(dict[str, object], context["filters"]))
    return _render(
        request,
        "_tasks.html",
        context,
    )


@router.post("/gui/tasks", response_class=HTMLResponse)
def create_task(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
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
    context = _task_context(session)
    if config.llm.api_key:
        # offer to find related existing tasks and group them with the new one
        context["related_task"] = task
        return _render(request, "_tasks_oob.html", context)
    return _render(request, "_tasks.html", context)


# --- task organization (AI) — declared before /gui/tasks/{task_id} so the
# literal "organize" path wins over the id parameter ---


@router.post("/gui/tasks/organize", response_class=HTMLResponse)
def tasks_organize(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
) -> HTMLResponse:
    context: dict[str, object] = {"proposals": [], "proposals_json": "[]", "error": None}
    if not config.llm.api_key:
        context["error"] = "No LLM API key configured."
    else:
        try:
            proposals, _tokens = task_organize.propose_organization(session, llm)
            context["proposals"] = proposals
            context["proposals_json"] = json.dumps([p.model_dump() for p in proposals])
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
    task_id: Annotated[int, Form()],
    title: Annotated[str, Form()],
    session: SessionDep,
) -> HTMLResponse:
    tasks.update_task(session, task_id, title=title.strip())
    return _tasks_after_organize(request, session)


@router.post("/gui/tasks/organize/apply", response_class=HTMLResponse)
def organize_apply(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    proposals_json: Annotated[str, Form()],
    selected: Annotated[list[int] | None, Form()] = None,
) -> HTMLResponse:
    """Apply the checked organization proposals in one submission.

    The proposal JSON round-trips through a hidden form field and is
    re-parsed; only the checked indexes are applied, each via its ``apply_*``
    helper. Failures are reported inline (out-of-band) without aborting the
    batch."""
    error: str | None = None
    try:
        parsed = [task_organize.Proposal.model_validate(p) for p in json.loads(proposals_json)]
        chosen = set(selected or [])
        picked = [p for index, p in enumerate(parsed) if index in chosen]
        if not picked:
            raise ValueError("no proposals selected")
        errors = task_organize.apply_proposals(
            session, picked, jira_base_url=_jira_base_url(config)
        )
        if errors:
            error = "; ".join(errors)
    except (ValueError, json.JSONDecodeError) as exc:
        error = str(exc)
    context = _task_context(session)
    context["organize_error"] = error
    return _render(request, "_tasks_oob.html", context)


@router.post("/gui/tasks/organize/topic", response_class=HTMLResponse)
def organize_topic(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    topic: Annotated[str, Form()],
) -> HTMLResponse:
    """Propose a parent/child grouping of open tasks related to a free-form topic."""
    topic = topic.strip()
    context: dict[str, object] = {"proposal": None, "error": None, "topic": topic}
    if not config.llm.api_key:
        context["error"] = "No LLM API key configured."
    elif not topic:
        context["error"] = "Enter a topic first."
    else:
        try:
            proposal, _tokens = task_organize.propose_topic_group(session, llm, topic)
            if proposal is None:
                context["error"] = f"No tasks found that clearly relate to “{topic}”."
            else:
                context["proposal"] = proposal
                context["tasks_by_id"] = {t.id: t for t in tasks.list_tasks(session)}
        except ValueError as exc:
            context["error"] = str(exc)
    return _render(request, "_topic_group.html", context)


@router.post("/gui/tasks/organize/group", response_class=HTMLResponse)
def organize_group(
    request: Request,
    session: SessionDep,
    child_ids: Annotated[list[int] | None, Form()] = None,
    parent_choice: Annotated[str, Form()] = "new",
    parent_title: Annotated[str, Form()] = "",
    parent_id: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Apply the confirmed grouping, then refresh the task list. A validation
    error is shown inline in the (out-of-band) organize panel."""
    error: str | None = None
    try:
        task_organize.apply_group(
            session,
            child_ids or [],
            new_parent_title=(parent_title.strip() or None) if parent_choice == "new" else None,
            existing_parent_id=_opt_int(parent_id) if parent_choice == "existing" else None,
        )
    except ValueError as exc:
        error = str(exc)
    context = _task_context(session)
    context["organize_error"] = error
    return _render(request, "_tasks_oob.html", context)


@router.post("/gui/tasks/organize/related", response_class=HTMLResponse)
def organize_related(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    task_id: Annotated[int, Form()],
    answered: Annotated[str, Form()] = "",
    question: Annotated[list[str] | None, Form()] = None,
    answer: Annotated[list[str] | None, Form()] = None,
) -> HTMLResponse:
    """Two-step "find related tasks" flow for a freshly created task.

    First POST (no ``answered`` field) asks the LLM for clarifying questions
    and renders answer inputs; the questions round-trip in hidden form fields
    (one Q&A round max, Q&A state is never persisted server-side). Resubmitting
    with answers proposes a TopicGroupProposal rendered by the shared
    _topic_group.html review; confirmation reuses /gui/tasks/organize/group.
    """
    if not answered:
        context: dict[str, object] = {"error": None, "task": None, "questions": []}
        if not config.llm.api_key:
            context["error"] = "No LLM API key configured."
        else:
            try:
                result, _tokens = task_organize.propose_related(session, llm, task_id)
                if isinstance(result, task_organize.RelatedQuestions):
                    context["task"] = tasks.get_task(session, task_id)
                    context["questions"] = result.questions
            except (NotFoundError, ValueError) as exc:
                context["error"] = str(exc)
        return _render(request, "_related_questions.html", context)
    context = {"proposal": None, "error": None, "topic": ""}
    if not config.llm.api_key:
        context["error"] = "No LLM API key configured."
    else:
        try:
            task = tasks.get_task(session, task_id)
            qa = [
                (q.strip(), a.strip())
                for q, a in zip(question or [], answer or [], strict=False)
                if q.strip()
            ]
            result, _tokens = task_organize.propose_related(session, llm, task_id, qa=qa)
            if result is None:
                context["error"] = f"No tasks found that clearly relate to #{task.id} {task.title}."
            elif isinstance(result, task_organize.TopicGroupProposal):
                context["topic"] = f"#{task.id} {task.title}"
                context["proposal"] = result
                context["tasks_by_id"] = {t.id: t for t in tasks.list_tasks(session)}
        except (NotFoundError, ValueError) as exc:
            context["error"] = str(exc)
    return _render(request, "_topic_group.html", context)


@router.post("/gui/tasks/organize/split/propose", response_class=HTMLResponse)
def organize_split_propose(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    task_id: Annotated[int, Form()],
    instructions: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Propose an AI split of one task into parts; render the review form in the
    task's edit panel. Stateless: the validated proposal round-trips through
    hidden form fields and is re-validated at apply time."""
    instructions = instructions.strip()
    llm_enabled = bool(config.llm.api_key)
    context = _edit_context(session, task_id, llm_enabled=llm_enabled)
    context["error"] = None
    context["proposal"] = None
    context["proposal_json"] = ""
    context["instructions"] = instructions
    if not config.llm.api_key:
        context["error"] = "No LLM API key configured."
    elif not instructions:
        context["error"] = "Enter split instructions first."
    else:
        try:
            proposal, _tokens = task_organize.propose_split_parts(
                session, llm, task_id, instructions
            )
            context["proposal"] = proposal
            context["proposal_json"] = proposal.model_dump_json()
        except ValueError as exc:
            context["error"] = str(exc)
    return _render(request, "_split_proposal.html", context)


@router.post("/gui/tasks/organize/split/apply", response_class=HTMLResponse)
def organize_split_apply(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    task_id: Annotated[int, Form()],
    mode: Annotated[str, Form()],
    proposal: Annotated[str, Form()],
    selected: Annotated[list[int] | None, Form()] = None,
) -> HTMLResponse:
    """Re-validate the hidden proposal against the task's current nuggets and
    apply only the checked parts, then refresh the task list."""
    error: str | None = None
    try:
        parsed = task_organize.SplitPartsProposal.model_validate_json(proposal)
        if parsed.mode != mode:
            raise ValueError("split mode does not match the proposal")
        attached = tasks.list_attached_nuggets(session, task_id)
        validated = task_organize._validate_split_parts(parsed, attached)
        chosen = set(selected or [])
        picked = [part for index, part in enumerate(validated.parts) if index in chosen]
        if not picked:
            raise ValueError("no split parts selected")
        task_organize.apply_split_parts(session, task_id, picked, validated.mode)
    except ValueError as exc:
        error = str(exc)
    context = _task_context(session)
    context["split_error"] = error
    return _render(request, "_split_applied.html", context)


@router.get("/gui/tasks/{task_id}", response_class=HTMLResponse)
def task_row(request: Request, task_id: int, session: SessionDep) -> HTMLResponse:
    return _render(request, "_task_row.html", {"task": tasks.get_task(session, task_id)})


@router.get("/gui/tasks/{task_id}/edit", response_class=HTMLResponse)
def task_edit(
    request: Request, task_id: int, session: SessionDep, config: ConfigDep
) -> HTMLResponse:
    return _render(
        request,
        "_task_edit.html",
        _edit_context(session, task_id, llm_enabled=bool(config.llm.api_key)),
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
    before = {t.name for t in tasks.get_task(session, task_id).tags}
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
    added = sorted({t.name for t in task.tags} - before)
    if added and tags_core.descendant_ids(session, task_id):
        return _render(request, "_task_row_oob.html", {"task": task, "added_tags": added})
    return _render(request, "_task_row.html", {"task": task})


@router.post("/gui/tasks/{task_id}/merge", response_class=HTMLResponse)
def task_merge(
    request: Request,
    task_id: int,
    session: SessionDep,
    into_id: Annotated[int, Form()],
) -> HTMLResponse:
    """Merge this task into *into_id* (this task is deleted); refresh the list."""
    task_organize.apply_merge(session, into_id, task_id)
    return _tasks_after_organize(request, session)


@router.post("/gui/tasks/{task_id}/description/propose", response_class=HTMLResponse)
def task_description_propose(
    request: Request,
    task_id: int,
    session: SessionDep,
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
) -> HTMLResponse:
    """AI suggests an updated description from the task's attached nuggets."""
    context: dict[str, object] = {
        "task": tasks.get_task(session, task_id),
        "suggested_description": None,
        "error": None,
    }
    if not config.llm.api_key:
        context["error"] = "No LLM API key configured."
    else:
        try:
            description, _tokens = task_organize.propose_description(session, llm, task_id)
            context["suggested_description"] = description
        except ValueError as exc:
            context["error"] = str(exc)
    return _render(request, "_description_preview.html", context)


@router.post("/gui/tasks/{task_id}/description/apply", response_class=HTMLResponse)
def task_description_apply(
    request: Request,
    task_id: int,
    session: SessionDep,
    config: ConfigDep,
    description: Annotated[str, Form()],
) -> HTMLResponse:
    tasks.update_task(session, task_id, description=description.strip() or None)
    return _render(
        request,
        "_task_detail.html",
        _detail_context(session, task_id, llm_enabled=bool(config.llm.api_key)),
    )


@router.post("/gui/tasks/{task_id}/propagate-tags", response_class=HTMLResponse)
def task_propagate_tags(
    request: Request,
    task_id: int,
    session: SessionDep,
    names: Annotated[str, Form()] = "",
) -> HTMLResponse:
    tag_names = [n.strip() for n in names.split(",") if n.strip()]
    if tag_names:
        tags_core.add_tags_to_descendants(session, task_id, tag_names)
    return _render(request, "_tasks_oob.html", _task_context(session))


@router.post("/gui/tasks/{task_id}/status", response_class=HTMLResponse)
def set_task_status(
    request: Request,
    task_id: int,
    session: SessionDep,
    status: Annotated[str, Form()],
) -> HTMLResponse:
    task = tasks.update_task(session, task_id, status=TaskStatus(status))
    return _render(request, "_task_row.html", {"task": task})


@router.post("/gui/tasks/{task_id}/detach", response_class=HTMLResponse)
def task_detach(
    request: Request,
    task_id: int,
    session: SessionDep,
    config: ConfigDep,
) -> HTMLResponse:
    """Remove a task's parent (detach from the parent's children list), then
    re-render the former parent's detail panel."""
    task = tasks.get_task(session, task_id)
    parent_id = task.parent_id
    tasks.update_task(session, task_id, parent_id=None)
    if parent_id is None:
        return _render(
            request,
            "_task_detail.html",
            _detail_context(session, task_id, llm_enabled=bool(config.llm.api_key)),
        )
    return _render(
        request,
        "_task_detail.html",
        _detail_context(session, parent_id, llm_enabled=bool(config.llm.api_key)),
    )


def _detail_context(session: Session, task_id: int, llm_enabled: bool = False) -> dict[str, object]:
    return {
        "task": tasks.get_task(session, task_id),
        "open_tasks": tasks.list_tasks(session),
        "kinds": list(NuggetKind),
        "llm_enabled": llm_enabled,
    }


def _edit_context(session: Session, task_id: int, llm_enabled: bool = False) -> dict[str, object]:
    task = tasks.get_task(session, task_id)
    return {
        "task": task,
        "people": people.list_people(session),
        "parent_candidates": _parent_candidates(session, task),
        "merge_candidates": _merge_candidates(session, task),
        "llm_enabled": llm_enabled,
    }


@router.get("/gui/tasks/{task_id}/detail", response_class=HTMLResponse)
def task_detail(
    request: Request, task_id: int, session: SessionDep, config: ConfigDep
) -> HTMLResponse:
    return _render(
        request,
        "_task_detail.html",
        _detail_context(session, task_id, llm_enabled=bool(config.llm.api_key)),
    )


@router.post("/gui/tasks/{task_id}/notes", response_class=HTMLResponse)
def task_add_note(
    request: Request,
    task_id: int,
    session: SessionDep,
    config: ConfigDep,
    body: Annotated[str, Form()],
) -> HTMLResponse:
    if body.strip():
        tasks.add_note(session, body.strip(), task_id=task_id)
    return task_detail(request, task_id, session, config)


@router.post("/gui/tasks/{task_id}/children", response_class=HTMLResponse)
def task_add_child(
    request: Request,
    task_id: int,
    session: SessionDep,
    config: ConfigDep,
    title: Annotated[str, Form()],
) -> HTMLResponse:
    if title.strip():
        tasks.create_task(session, title=title.strip(), parent_id=task_id)
    return task_detail(request, task_id, session, config)


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
    return _render(request, "_task_jira.html", {"task": task})


@router.post("/gui/tasks/{task_id}/links/{link_id}/delete", response_class=HTMLResponse)
def task_delete_link(
    request: Request, task_id: int, link_id: int, session: SessionDep
) -> HTMLResponse:
    tasks.unlink_external(session, task_id, link_id)
    return _render(request, "_task_jira.html", {"task": tasks.get_task(session, task_id)})


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
    selected: Annotated[str, Form()] = "",
    nugget_ids: Annotated[list[int] | None, Form()] = None,
) -> HTMLResponse:
    """Attach the selected nuggets suggested for *task_id* (all when none chosen)."""
    items = nuggets.list_nuggets(session, status=NuggetStatus.NEW, owner_id=_opt_int(owner))
    chosen = set(nugget_ids or []) if selected else None
    for item in items:
        if item.suggested_task_id == task_id and (chosen is None or item.id in chosen):
            nuggets.attach_nugget(
                session, item.id, task_id=task_id, jira_base_url=_jira_base_url(config)
            )
    return _render(request, "_inbox.html", _inbox_context(session, status, owner))


@router.post("/gui/inbox/suggest-creates", response_class=HTMLResponse)
def inbox_suggest_creates(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
) -> HTMLResponse:
    """AI proposes new tasks that group clusters of unmapped notes; nothing is
    persisted until the user picks notes and confirms."""
    context: dict[str, object] = {"proposals": [], "nuggets_by_id": {}, "error": None}
    if not config.llm.api_key:
        context["error"] = "No LLM API key configured."
    else:
        try:
            proposals, _tokens = task_organize.propose_inbox_creates(session, llm)
            new_nuggets = nuggets.list_nuggets(session, status=NuggetStatus.NEW)
            context["proposals"] = proposals
            context["nuggets_by_id"] = {n.id: n for n in new_nuggets}
        except ValueError as exc:
            context["error"] = str(exc)
    return _render(request, "_inbox_create.html", context)


@router.post("/gui/inbox/create-group", response_class=HTMLResponse)
def inbox_create_group(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    title: Annotated[str, Form()],
    status: Annotated[str, Form()] = "new",
    owner: Annotated[str, Form()] = "",
    nugget_ids: Annotated[list[int] | None, Form()] = None,
) -> HTMLResponse:
    """Create a task from the user-confirmed cluster of notes and refresh the inbox."""
    ids = [n for n in (nugget_ids or [])]
    error: str | None = None
    if not title.strip():
        error = "New task needs a title."
    elif not ids:
        error = "Select at least one note to create a task."
    else:
        task_organize.apply_create(
            session, title.strip(), ids, jira_base_url=_jira_base_url(config)
        )
    context = _inbox_context(session, status, owner)
    context["inbox_create_error"] = error
    return _render(request, "_inbox_oob.html", context)


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
    if not task_id:
        return _render(
            request,
            "_nugget_new_task.html",
            _nugget_new_task_context(session, nugget_id, config),
        )
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


def _nugget_new_task_context(session: Session, nugget_id: int, config: Config) -> dict[str, object]:
    """Guesses for the pre-filled create dialog when attaching a nugget to a new task."""
    nug = nuggets.get_nugget(session, nugget_id)
    priority = (
        TaskPriority.HIGH
        if nug.kind == NuggetKind.DEADLINE
        else TaskPriority(config.nugget.default_priority)
    )
    assignee_id = nug.owner_id
    if assignee_id is None and config.nugget.default_assignee_self:
        self_person = people.get_self(session)
        assignee_id = self_person.id if self_person is not None else None
    return {
        "nugget": nug,
        "people": people.list_people(session),
        "prefill": {
            "title": nug.summary,
            "description": nug.excerpt,
            "priority": priority,
            "assignee_id": assignee_id,
            "due_date": nug.due_date,
        },
    }


@router.post("/gui/nuggets/{nugget_id}/create", response_class=HTMLResponse)
def nugget_create_task(
    request: Request,
    nugget_id: int,
    session: SessionDep,
    config: ConfigDep,
    title: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    priority: Annotated[str, Form()] = "normal",
    assignee_id: Annotated[str, Form()] = "",
    due_date: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Create a task from the edited nugget guesses and attach the nugget."""
    nuggets.attach_nugget(
        session,
        nugget_id,
        title=title.strip(),
        description=description.strip() or None,
        priority=TaskPriority(priority),
        assignee_id=_opt_int(assignee_id),
        due_date=_opt_date(due_date),
        default_assignee_id=nuggets.default_assignee(session, config.nugget.default_assignee_self),
        jira_base_url=_jira_base_url(config),
    )
    return _render(request, "_nugget.html", {"nugget": nuggets.get_nugget(session, nugget_id)})


@router.get("/gui/nuggets/{nugget_id}/attach-form", response_class=HTMLResponse)
def nugget_attach_form(request: Request, nugget_id: int, session: SessionDep) -> HTMLResponse:
    """Re-render a NEW nugget's attach form (the create-dialog cancel path)."""
    return _render(
        request,
        "_nugget.html",
        {
            "nugget": nuggets.get_nugget(session, nugget_id),
            "open_tasks": tasks.list_tasks(session),
            "people": people.list_people(session),
        },
    )


@router.post("/gui/nuggets/{nugget_id}/edit", response_class=HTMLResponse)
def nugget_edit(
    request: Request,
    nugget_id: int,
    session: SessionDep,
    config: ConfigDep,
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
    return _render(
        request,
        "_task_detail.html",
        _detail_context(session, task_id, llm_enabled=bool(config.llm.api_key)),  # type: ignore[arg-type]
    )


@router.post("/gui/nuggets/{nugget_id}/move", response_class=HTMLResponse)
def nugget_move(
    request: Request,
    nugget_id: int,
    session: SessionDep,
    config: ConfigDep,
    task_id: Annotated[int, Form()],
) -> HTMLResponse:
    """Move a nugget to another task; re-render the (now former) task panel."""
    nugget = nuggets.get_nugget(session, nugget_id)
    source_task_id = nugget.task_id
    nuggets.move_nugget(session, nugget_id, task_id)
    return _render(
        request,
        "_task_detail.html",
        _detail_context(session, source_task_id, llm_enabled=bool(config.llm.api_key)),  # type: ignore[arg-type]
    )


@router.post("/gui/nuggets/{nugget_id}/detach", response_class=HTMLResponse)
def nugget_detach(
    request: Request, nugget_id: int, session: SessionDep, config: ConfigDep
) -> HTMLResponse:
    """Remove a nugget from its task (back to the inbox); re-render the panel."""
    nugget = nuggets.get_nugget(session, nugget_id)
    source_task_id = nugget.task_id
    nuggets.detach_nugget(session, nugget_id)
    return _render(
        request,
        "_task_detail.html",
        _detail_context(session, source_task_id, llm_enabled=bool(config.llm.api_key)),  # type: ignore[arg-type]
    )


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


# --- labels (tags) ---


def _labels_context(session: Session) -> dict[str, object]:
    rows = [{"tag": tag, "task_count": len(tag.tasks)} for tag in tags_core.list_tags(session)]
    return {"rows": rows, "palette": colors.label_palette()}


@router.get("/labels", response_class=HTMLResponse)
def labels_page(request: Request, session: SessionDep) -> HTMLResponse:
    context = _labels_context(session)
    context["page"] = "labels"
    return _render(request, "labels.html", context)


@router.get("/gui/labels", response_class=HTMLResponse)
def label_list(request: Request, session: SessionDep) -> HTMLResponse:
    return _render(request, "_labels.html", _labels_context(session))


@router.post("/gui/labels", response_class=HTMLResponse)
def create_label(
    request: Request,
    session: SessionDep,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    color: Annotated[str, Form()] = "",
) -> HTMLResponse:
    tags_core.create_tag(session, name, description=description.strip() or None, color=color)
    return label_list(request, session)


@router.get("/gui/labels/{tag_id}/edit", response_class=HTMLResponse)
def label_edit(request: Request, tag_id: int, session: SessionDep) -> HTMLResponse:
    return _render(
        request,
        "_label_edit.html",
        {"tag": tags_core.get_tag(session, tag_id), "palette": colors.label_palette()},
    )


@router.post("/gui/labels/{tag_id}", response_class=HTMLResponse)
def label_update(
    request: Request,
    tag_id: int,
    session: SessionDep,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    color: Annotated[str, Form()] = "",
) -> HTMLResponse:
    tags_core.update_tag(session, tag_id, name=name, description=description, color=color)
    return label_list(request, session)


@router.post("/gui/labels/{tag_id}/delete", response_class=HTMLResponse)
def label_delete(request: Request, tag_id: int, session: SessionDep) -> HTMLResponse:
    tags_core.delete_tag(session, tag_id)
    return label_list(request, session)


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
