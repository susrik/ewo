"""Inbox of items discovered in notes: list, accept (→ task), dismiss, already done.

Accepting creates a Task with ``source=notes`` and an external link
``notes:<path>:<line>:<item_id>`` so the citation is visible on the task. The
item id keeps the key unique even when several distinct items are extracted
from the same note line. Nothing here touches the notes tree.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ewo.core import tasks
from ewo.core.people import NotFoundError
from ewo.db.models import (
    NoteItem,
    NoteItemKind,
    NoteItemStatus,
    Task,
    TaskPriority,
    TaskSource,
    utcnow,
)

LINK_SYSTEM = "notes"

_WORD = re.compile(r"[a-z0-9]+")


def fingerprint(path: str, summary: str) -> str:
    """Stable id for an item: path + normalised summary words."""
    words = " ".join(_WORD.findall(summary.lower()))
    return hashlib.sha256(f"{path}\n{words}".encode()).hexdigest()[:40]


def upsert_item(
    session: Session,
    path: str,
    line: int,
    summary: str,
    kind: NoteItemKind,
    excerpt: str | None = None,
    owner_id: int | None = None,
    owner_name: str | None = None,
    due_date: date | None = None,
    job_run_id: int | None = None,
) -> tuple[NoteItem, bool]:
    """Insert a new item or refresh ``last_seen_at`` on the existing one.

    Returns ``(item, created)``. Reviewed items are never reopened.
    """
    fp = fingerprint(path, summary)
    item = session.scalars(select(NoteItem).where(NoteItem.fingerprint == fp)).first()
    if item is not None:
        item.last_seen_at = utcnow()
        item.line = line
        if excerpt:
            item.excerpt = excerpt
        return item, False
    item = NoteItem(
        fingerprint=fp,
        path=path,
        line=line,
        summary=summary[:500],
        excerpt=excerpt,
        kind=kind,
        owner_id=owner_id,
        owner_name=owner_name,
        due_date=due_date,
        job_run_id=job_run_id,
    )
    session.add(item)
    session.flush()
    return item, True


def get_item(session: Session, item_id: int) -> NoteItem:
    item = session.get(NoteItem, item_id)
    if item is None:
        raise NotFoundError(f"note item {item_id} not found")
    return item


def list_items(
    session: Session,
    status: NoteItemStatus | None = NoteItemStatus.NEW,
    owner_id: int | None = None,
    path_prefix: str | None = None,
) -> list[NoteItem]:
    query = (
        select(NoteItem)
        .options(selectinload(NoteItem.owner), selectinload(NoteItem.task))
        .order_by(NoteItem.path, NoteItem.line)
    )
    if status is not None:
        query = query.where(NoteItem.status == status)
    if owner_id is not None:
        query = query.where(NoteItem.owner_id == owner_id)
    if path_prefix:
        query = query.where(NoteItem.path.startswith(path_prefix))
    return list(session.scalars(query))


def count_new(session: Session) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(NoteItem).where(NoteItem.status == NoteItemStatus.NEW)
        )
        or 0
    )


def accept_item(
    session: Session,
    item_id: int,
    priority: TaskPriority = TaskPriority.NORMAL,
    assignee_id: int | None = None,
    due_date: date | None = None,
    title: str | None = None,
) -> Task:
    """Turn an inbox item into a tracked task (linked back to its citation)."""
    item = get_item(session, item_id)
    if item.task_id is not None:
        return tasks.get_task(session, item.task_id)
    task = tasks.create_task(
        session,
        title=title or item.summary,
        description=item.excerpt,
        priority=priority,
        source=TaskSource.NOTES,
        assignee_id=assignee_id if assignee_id is not None else item.owner_id,
        due_date=due_date if due_date is not None else item.due_date,
    )
    tasks.link_external(session, task.id, LINK_SYSTEM, f"{item.path}:{item.line}:{item.id}")
    item.task_id = task.id
    item.status = NoteItemStatus.ACCEPTED
    item.reviewed_at = utcnow()
    session.commit()
    return task


def set_item_status(session: Session, item_id: int, status: NoteItemStatus) -> NoteItem:
    """Dismiss or mark already-done (the note itself is never edited)."""
    item = get_item(session, item_id)
    item.status = status
    item.reviewed_at = utcnow()
    session.commit()
    return item


def update_item(session: Session, item_id: int, **fields: object) -> NoteItem:
    item = get_item(session, item_id)
    for key, value in fields.items():
        setattr(item, key, value)
    session.commit()
    return item
