"""Task service layer: plain functions over a sync Session."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ewo.core.people import NotFoundError
from ewo.core.tags import normalize_name
from ewo.db.models import (
    ExternalLink,
    Note,
    Nugget,
    NuggetStatus,
    Tag,
    Task,
    TaskPriority,
    TaskSource,
    TaskStatus,
    utcnow,
)


def _get_or_create_tags(session: Session, names: list[str]) -> list[Tag]:
    tags: dict[str, Tag] = {}
    for name in names:
        normalized = normalize_name(name)
        if not normalized or normalized in tags:
            continue
        tag = session.scalars(select(Tag).where(Tag.name == normalized)).first()
        if tag is None:
            tag = Tag(name=normalized)
            session.add(tag)
        tags[normalized] = tag
    return list(tags.values())


def _check_parent(session: Session, task_id: int | None, parent_id: int | None) -> None:
    """Validate a parent assignment: the parent must exist and must not be the
    task itself or one of its descendants (single-parent tree, no cycles)."""
    if parent_id is None:
        return
    if task_id is not None and parent_id == task_id:
        raise ValueError("a task cannot be its own parent")
    ancestor = session.get(Task, parent_id)
    if ancestor is None:
        raise NotFoundError(f"parent task {parent_id} not found")
    if task_id is not None:
        while ancestor is not None:
            if ancestor.id == task_id:
                raise ValueError(f"parent task {parent_id} is a descendant of task {task_id}")
            ancestor = ancestor.parent


def create_task(
    session: Session,
    title: str,
    description: str | None = None,
    priority: TaskPriority = TaskPriority.NORMAL,
    source: TaskSource = TaskSource.MANUAL,
    assignee_id: int | None = None,
    parent_id: int | None = None,
    start_date: date | None = None,
    due_date: date | None = None,
    tags: list[str] | None = None,
) -> Task:
    _check_parent(session, None, parent_id)
    effective_tags = list(tags or [])
    if parent_id is not None:
        parent = session.get(Task, parent_id)
        if parent is not None:
            # children inherit the parent's labels (additive union, deduped by
            # normalized name inside _get_or_create_tags)
            effective_tags += [t.name for t in parent.tags]
    task = Task(
        title=title,
        description=description,
        priority=priority,
        source=source,
        assignee_id=assignee_id,
        parent_id=parent_id,
        start_date=start_date,
        due_date=due_date,
        tags=_get_or_create_tags(session, effective_tags),
    )
    session.add(task)
    session.commit()
    return task


def get_task(session: Session, task_id: int) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise NotFoundError(f"task {task_id} not found")
    return task


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so user search text is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_tasks(
    session: Session, query: str, exclude_ids: Iterable[int] = (), limit: int = 20
) -> list[Task]:
    """Open tasks whose title contains ``query`` (case-insensitive substring).

    ``exclude_ids`` drops tasks that must not be offered (e.g. a task itself
    and its descendants when choosing a parent/child)."""
    q = query.strip()
    if not q:
        return []
    pattern = f"%{_escape_like(q)}%"
    stmt = (
        select(Task)
        .options(selectinload(Task.tags), selectinload(Task.assignee))
        .where(Task.title.ilike(pattern, escape="\\"))
        .where(Task.status.not_in([TaskStatus.DONE, TaskStatus.DROPPED]))
    )
    if exclude_ids:
        stmt = stmt.where(Task.id.not_in(exclude_ids))
    return list(session.scalars(stmt.order_by(func.lower(Task.title)).limit(limit)))


_PRIORITY_RANK = {
    TaskPriority.CRITICAL: 0,
    TaskPriority.HIGH: 1,
    TaskPriority.NORMAL: 2,
    TaskPriority.LOW: 3,
}

SORT_FIELDS = (
    "priority",
    "title",
    "status",
    "due_date",
    "assignee",
    "source",
    "created_at",
    "updated_at",
)


def _sort_key(task: Task, field: str) -> object:
    """A comparable value for one sort field. Missing values sort last
    (ascending) so critical/earliest items float to the top."""
    if field == "priority":
        return _PRIORITY_RANK[task.priority]
    if field == "title":
        return task.title.lower()
    if field == "status":
        return task.status.value
    if field == "due_date":
        return (task.due_date is None, task.due_date)
    if field == "assignee":
        return (task.assignee is None, task.assignee.name.lower() if task.assignee else "")
    if field == "source":
        return task.source.value
    if field == "created_at":
        return task.created_at
    if field == "updated_at":
        return task.updated_at
    return task.id


def sort_tasks(
    rows: list[Task],
    sort_by: str | None = "priority",
    sort_by_2: str | None = "title",
    reverse: bool = False,
) -> list[Task]:
    """Stable two-key sort of a flat task list.

    Unknown/none fields fall back to the default (priority, then title).
    ``reverse`` flips both keys. Priority uses its own rank (critical first)
    rather than alphabetical ordering.
    """
    primary = sort_by if sort_by in SORT_FIELDS else "priority"
    secondary = sort_by_2 if sort_by_2 in SORT_FIELDS else "title"
    return sorted(
        rows,
        key=lambda t: (_sort_key(t, primary), _sort_key(t, secondary)),
        reverse=reverse,
    )


def list_tasks(
    session: Session,
    status: TaskStatus | None = None,
    assignee_id: int | None = None,
    tag: str | None = None,
    tags: list[str] | None = None,
    tag_match: Literal["any", "all"] = "any",
    include_closed: bool = False,
) -> list[Task]:
    query = (
        select(Task)
        .options(selectinload(Task.tags), selectinload(Task.assignee))
        .order_by(Task.updated_at.desc())
    )
    if status is not None:
        query = query.where(Task.status == status)
    elif not include_closed:
        query = query.where(Task.status.not_in([TaskStatus.DONE, TaskStatus.DROPPED]))
    if assignee_id is not None:
        query = query.where(Task.assignee_id == assignee_id)
    names = [n for n in (normalize_name(t) for t in (tags or [])) if n]
    if tag is not None and normalize_name(tag):
        names.append(normalize_name(tag))
    names = list(dict.fromkeys(names))
    if tag_match == "all":
        for name in names:
            query = query.where(Task.tags.any(Tag.name == name))
    elif names:
        query = query.where(Task.tags.any(Tag.name.in_(names)))
    return list(session.scalars(query).unique())


def update_task(
    session: Session,
    task_id: int,
    tags: list[str] | None = None,
    **fields: object,
) -> Task:
    task = get_task(session, task_id)
    if "parent_id" in fields:
        _check_parent(session, task_id, fields["parent_id"])  # type: ignore[arg-type]
    if "status" in fields:
        new_status = fields["status"]
        if new_status == TaskStatus.DONE and task.status != TaskStatus.DONE:
            task.completed_at = utcnow()
        elif new_status != TaskStatus.DONE and task.status == TaskStatus.DONE:
            task.completed_at = None
    for key, value in fields.items():
        setattr(task, key, value)
    if tags is not None:
        task.tags = _get_or_create_tags(session, tags)
    session.commit()
    return task


def delete_task(session: Session, task_id: int) -> None:
    """Delete a task. Its nuggets return to the inbox; its children's parent
    is cleared (DB SET NULL)."""
    task = get_task(session, task_id)
    for nugget in list(task.nuggets):
        nugget.task_id = None
        nugget.status = NuggetStatus.NEW
        nugget.reviewed_at = None
    session.delete(task)
    session.commit()


def add_note(
    session: Session,
    body: str,
    task_id: int | None = None,
    person_id: int | None = None,
) -> Note:
    note = Note(body=body, task_id=task_id, person_id=person_id)
    session.add(note)
    session.commit()
    return note


def link_external(
    session: Session,
    task_id: int,
    system: str,
    external_key: str,
    url: str | None = None,
    external_status: str | None = None,
) -> ExternalLink:
    link = ExternalLink(
        task_id=task_id,
        system=system,
        external_key=external_key,
        url=url,
        external_status=external_status,
    )
    session.add(link)
    session.commit()
    return link


def unlink_external(session: Session, task_id: int, link_id: int) -> None:
    link = session.get(ExternalLink, link_id)
    if link is None or link.task_id != task_id:
        raise NotFoundError(f"link {link_id} not found on task {task_id}")
    session.delete(link)
    session.commit()


def find_link(session: Session, system: str, external_key: str) -> ExternalLink | None:
    return session.scalars(
        select(ExternalLink).where(
            ExternalLink.system == system, ExternalLink.external_key == external_key
        )
    ).first()


def find_links(session: Session, system: str, external_key: str) -> list[ExternalLink]:
    """All links for an external key — several tasks may share one issue."""
    return list(
        session.scalars(
            select(ExternalLink).where(
                ExternalLink.system == system, ExternalLink.external_key == external_key
            )
        )
    )


def list_attached_nuggets(session: Session, task_id: int) -> list[Nugget]:
    """Nuggets attached to a task, citation order."""
    get_task(session, task_id)
    return list(
        session.scalars(
            select(Nugget)
            .where(Nugget.task_id == task_id, Nugget.status == NuggetStatus.ATTACHED)
            .order_by(Nugget.path, Nugget.line)
        )
    )
