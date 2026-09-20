"""Task service layer: plain functions over a sync Session."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ewo.core.people import NotFoundError
from ewo.db.models import ExternalLink, Note, Tag, Task, TaskPriority, TaskSource, TaskStatus


def _get_or_create_tags(session: Session, names: list[str]) -> list[Tag]:
    tags: dict[str, Tag] = {}
    for name in names:
        normalized = name.strip().lower()
        if not normalized or normalized in tags:
            continue
        tag = session.scalars(select(Tag).where(Tag.name == normalized)).first()
        if tag is None:
            tag = Tag(name=normalized)
            session.add(tag)
        tags[normalized] = tag
    return list(tags.values())


def create_task(
    session: Session,
    title: str,
    description: str | None = None,
    priority: TaskPriority = TaskPriority.NORMAL,
    source: TaskSource = TaskSource.MANUAL,
    assignee_id: int | None = None,
    due_date: date | None = None,
    tags: list[str] | None = None,
) -> Task:
    task = Task(
        title=title,
        description=description,
        priority=priority,
        source=source,
        assignee_id=assignee_id,
        due_date=due_date,
        tags=_get_or_create_tags(session, tags or []),
    )
    session.add(task)
    session.commit()
    return task


def get_task(session: Session, task_id: int) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise NotFoundError(f"task {task_id} not found")
    return task


def list_tasks(
    session: Session,
    status: TaskStatus | None = None,
    assignee_id: int | None = None,
    tag: str | None = None,
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
    if tag is not None:
        query = query.where(Task.tags.any(Tag.name == tag.strip().lower()))
    return list(session.scalars(query).unique())


def update_task(
    session: Session,
    task_id: int,
    tags: list[str] | None = None,
    **fields: object,
) -> Task:
    task = get_task(session, task_id)
    for key, value in fields.items():
        setattr(task, key, value)
    if tags is not None:
        task.tags = _get_or_create_tags(session, tags)
    session.commit()
    return task


def delete_task(session: Session, task_id: int) -> None:
    task = get_task(session, task_id)
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


def find_link(session: Session, system: str, external_key: str) -> ExternalLink | None:
    return session.scalars(
        select(ExternalLink).where(
            ExternalLink.system == system, ExternalLink.external_key == external_key
        )
    ).first()
