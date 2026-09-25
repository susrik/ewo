"""Nuggets — work items discovered in the notes tree (the review inbox).

A nugget is a potential piece of work with a citation (``path``:``line``).
Reviewing means attaching it to a task (new or existing), dismissing it, or
marking it already done. Attaching links the task back to the citation
(``notes:<path>:<line>:<nugget_id>``) and adds any Jira keys captured from the
note as task links. Attached nuggets stay visible on their task and can be
moved between tasks or detached back to the inbox. Nothing here touches the
notes tree.
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
    ExternalLink,
    Nugget,
    NuggetKind,
    NuggetStatus,
    Task,
    TaskPriority,
    TaskSource,
    utcnow,
)

LINK_SYSTEM = "notes"
JIRA_SYSTEM = "jira"

_WORD = re.compile(r"[a-z0-9]+")
_JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def norm_words(text: str) -> str:
    """Lowercased word tokens, space-joined — the basis for dedupe/matching."""
    return " ".join(_WORD.findall(text.lower()))


def fingerprint(path: str, summary: str) -> str:
    """Stable id for a nugget: path + normalised summary words."""
    return hashlib.sha256(f"{path}\n{norm_words(summary)}".encode()).hexdigest()[:40]


def find_jira_keys(*texts: str | None) -> list[str]:
    """Jira issue keys (e.g. ``DBOARD3-1111``) mentioned in the given texts."""
    keys: list[str] = []
    for text in texts:
        for match in _JIRA_KEY.findall(text or ""):
            if match not in keys:
                keys.append(match)
    return keys


def upsert_nugget(
    session: Session,
    path: str,
    line: int,
    summary: str,
    kind: NuggetKind,
    excerpt: str | None = None,
    owner_id: int | None = None,
    owner_name: str | None = None,
    due_date: date | None = None,
    jira_keys: list[str] | None = None,
    job_run_id: int | None = None,
) -> tuple[Nugget, bool]:
    """Insert a new nugget or refresh ``last_seen_at`` on the existing one.

    Returns ``(nugget, created)``. Reviewed nuggets are never reopened; their
    Jira keys are unioned with anything newly captured.
    """
    fp = fingerprint(path, summary)
    nugget = session.scalars(select(Nugget).where(Nugget.fingerprint == fp)).first()
    if nugget is not None:
        nugget.last_seen_at = utcnow()
        nugget.line = line
        if excerpt:
            nugget.excerpt = excerpt
        if jira_keys:
            nugget.jira_keys = list(dict.fromkeys([*nugget.jira_keys, *jira_keys]))
        return nugget, False
    nugget = Nugget(
        fingerprint=fp,
        path=path,
        line=line,
        summary=summary[:500],
        excerpt=excerpt,
        kind=kind,
        owner_id=owner_id,
        owner_name=owner_name,
        due_date=due_date,
        jira_keys=list(jira_keys or []),
        job_run_id=job_run_id,
    )
    session.add(nugget)
    session.flush()
    return nugget, True


def get_nugget(session: Session, nugget_id: int) -> Nugget:
    nugget = session.get(Nugget, nugget_id)
    if nugget is None:
        raise NotFoundError(f"nugget {nugget_id} not found")
    return nugget


def list_nuggets(
    session: Session,
    status: NuggetStatus | None = NuggetStatus.NEW,
    owner_id: int | None = None,
    path_prefix: str | None = None,
) -> list[Nugget]:
    query = (
        select(Nugget)
        .options(selectinload(Nugget.owner), selectinload(Nugget.task))
        .order_by(Nugget.path, Nugget.line)
    )
    if status is not None:
        query = query.where(Nugget.status == status)
    if owner_id is not None:
        query = query.where(Nugget.owner_id == owner_id)
    if path_prefix:
        query = query.where(Nugget.path.startswith(path_prefix))
    return list(session.scalars(query))


def count_new(session: Session) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(Nugget).where(Nugget.status == NuggetStatus.NEW)
        )
        or 0
    )


def _citation_key(nugget: Nugget) -> str:
    return f"{nugget.path}:{nugget.line}:{nugget.id}"


def _citation_link(session: Session, nugget: Nugget) -> ExternalLink | None:
    """The nugget's citation link wherever it currently sits (the line number
    may have moved since it was created, so match on path + trailing id)."""
    prefix = f"{nugget.path}:"
    suffix = f":{nugget.id}"
    for link in session.scalars(select(ExternalLink).where(ExternalLink.system == LINK_SYSTEM)):
        if link.external_key.startswith(prefix) and link.external_key.endswith(suffix):
            return link
    return None


def _ensure_citation_link(session: Session, task: Task, nugget: Nugget) -> None:
    key = _citation_key(nugget)
    if any(link.system == LINK_SYSTEM and link.external_key == key for link in task.external_links):
        return
    task.external_links.append(ExternalLink(system=LINK_SYSTEM, external_key=key))
    session.flush()


def ensure_jira_links(
    session: Session, task: Task, jira_keys: list[str], jira_base_url: str | None = None
) -> None:
    """Add Jira links for *jira_keys* the task does not already have."""
    existing = {link.external_key for link in task.external_links if link.system == JIRA_SYSTEM}
    base = jira_base_url.rstrip("/") if jira_base_url else None
    for key in jira_keys:
        if key in existing:
            continue
        url = f"{base}/browse/{key}" if base else None
        task.external_links.append(ExternalLink(system=JIRA_SYSTEM, external_key=key, url=url))
    session.flush()


def attach_nugget(
    session: Session,
    nugget_id: int,
    task_id: int | None = None,
    priority: TaskPriority = TaskPriority.NORMAL,
    assignee_id: int | None = None,
    due_date: date | None = None,
    title: str | None = None,
    jira_base_url: str | None = None,
) -> Task:
    """Attach a nugget to a task — an existing one (``task_id``) or a new one.

    Creating a new task mirrors the nugget's details onto it (title, excerpt,
    owner, due date). Attaching to an existing task adds the nugget as an
    update: citation + Jira links, no task-field changes.
    """
    nugget = get_nugget(session, nugget_id)
    if nugget.task_id is not None:
        return tasks.get_task(session, nugget.task_id)
    if task_id is not None:
        task = tasks.get_task(session, task_id)
    else:
        task = tasks.create_task(
            session,
            title=title or nugget.summary,
            description=nugget.excerpt,
            priority=priority,
            source=TaskSource.NOTES,
            assignee_id=assignee_id if assignee_id is not None else nugget.owner_id,
            due_date=due_date if due_date is not None else nugget.due_date,
        )
    _ensure_citation_link(session, task, nugget)
    ensure_jira_links(session, task, nugget.jira_keys, jira_base_url)
    nugget.task_id = task.id
    nugget.status = NuggetStatus.ATTACHED
    nugget.reviewed_at = utcnow()
    nugget.suggested_task_id = None
    session.commit()
    return task


def move_nugget(session: Session, nugget_id: int, task_id: int) -> Nugget:
    """Move an attached nugget (and its citation link) to another task."""
    nugget = get_nugget(session, nugget_id)
    task = tasks.get_task(session, task_id)
    link = _citation_link(session, nugget)
    if link is not None:
        link.task_id = task.id
    nugget.task_id = task.id
    nugget.status = NuggetStatus.ATTACHED
    nugget.suggested_task_id = None
    session.commit()
    return nugget


def detach_nugget(session: Session, nugget_id: int) -> Nugget:
    """Remove a nugget from its task and return it to the inbox (status NEW)."""
    nugget = get_nugget(session, nugget_id)
    link = _citation_link(session, nugget)
    if link is not None:
        session.delete(link)
    nugget.task_id = None
    nugget.status = NuggetStatus.NEW
    nugget.reviewed_at = None
    nugget.suggested_task_id = None
    session.commit()
    return nugget


def set_nugget_status(session: Session, nugget_id: int, status: NuggetStatus) -> Nugget:
    """Dismiss or mark already-done (the note itself is never edited)."""
    nugget = get_nugget(session, nugget_id)
    nugget.status = status
    nugget.reviewed_at = utcnow()
    nugget.suggested_task_id = None
    session.commit()
    return nugget


def update_nugget(session: Session, nugget_id: int, **fields: object) -> Nugget:
    """Edit a nugget's text/details. The fingerprint is never recomputed, so
    re-scans keep matching this row instead of surfacing a duplicate."""
    nugget = get_nugget(session, nugget_id)
    for key, value in fields.items():
        setattr(nugget, key, value)
    session.commit()
    return nugget
