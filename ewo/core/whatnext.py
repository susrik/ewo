"""Live "what next" ranking — computed on request, never stored.

Deterministic scoring with an optional LLM narrative on top. Signals, in
decreasing weight:

- priority, overdue / due soon
- blocked tasks are pushed down (nothing to do right now)
- mine (assigned to the ``is_self`` person) beats delegated / unassigned
- age since creation and time since last update (old, untouched work surfaces)
- recently seen in notes (the linked inbox item was re-seen in a recent scan)
- a small daily-seeded jitter so equal-scored tasks rotate rather than stick
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.core.llm import LLMClient
from ewo.core.people import get_self
from ewo.core.tasks import list_tasks
from ewo.db.models import NoteItem, Task, TaskPriority, TaskStatus

_PRIORITY_SCORE = {
    TaskPriority.CRITICAL: 100,
    TaskPriority.HIGH: 50,
    TaskPriority.NORMAL: 20,
    TaskPriority.LOW: 5,
}


@dataclass
class RankedTask:
    task: Task
    score: float
    reasons: list[str]


@dataclass
class WhatNext:
    """Top picks plus the runners-up to choose from when the top is ambiguous."""

    focus: list[RankedTask]
    candidates: list[RankedTask]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def score_task(
    task: Task,
    now: datetime | None = None,
    self_id: int | None = None,
    last_seen: datetime | None = None,
    jitter: float = 0.0,
) -> RankedTask:
    now = now or _now()
    score = float(_PRIORITY_SCORE[task.priority])
    reasons = [f"priority {task.priority.value}"]

    if task.due_date is not None:
        days_left = (task.due_date - now.date()).days
        if days_left < 0:
            score += 80
            reasons.append(f"overdue by {-days_left}d")
        elif days_left <= 3:
            score += 40
            reasons.append(f"due in {days_left}d")
        elif days_left <= 14:
            score += 10
            reasons.append(f"due in {days_left}d")

    if task.status == TaskStatus.BLOCKED:
        score -= 30
        reasons.append("blocked")
    elif task.status == TaskStatus.IN_PROGRESS:
        score += 10
        reasons.append("in progress")

    if self_id is not None and task.assignee_id == self_id:
        score += 15
        reasons.append("mine")

    stale_days = (now - task.updated_at).days
    if stale_days >= 7:
        score += min(stale_days, 30)
        reasons.append(f"untouched {stale_days}d")

    age_days = (now - task.created_at).days
    if age_days >= 30:
        score += min(age_days // 30 * 5, 20)
        reasons.append(f"open {age_days}d")

    if last_seen is not None and (now - last_seen).days <= 7:
        score += 10
        reasons.append("in recent notes")

    return RankedTask(task=task, score=score + jitter, reasons=reasons)


def _recent_note_mentions(session: Session) -> dict[int, datetime]:
    rows = session.execute(
        select(NoteItem.task_id, NoteItem.last_seen_at).where(NoteItem.task_id.is_not(None))
    )
    return {int(task_id): seen for task_id, seen in rows}


def rank_all(
    session: Session, now: datetime | None = None, seed: str | None = None
) -> list[RankedTask]:
    """Score every open task; ties are broken by a jitter seeded per day."""
    now = now or _now()
    me = get_self(session)
    self_id = me.id if me else None
    mentions = _recent_note_mentions(session)
    rng = random.Random(seed or now.date().isoformat())
    ranked = [
        score_task(
            task,
            now=now,
            self_id=self_id,
            last_seen=mentions.get(task.id),
            jitter=rng.random(),
        )
        for task in list_tasks(session)
    ]
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked


def rank_tasks(session: Session, limit: int = 10) -> list[RankedTask]:
    return rank_all(session)[:limit]


def what_next(session: Session, focus: int = 3, candidates: int = 5) -> WhatNext:
    ranked = rank_all(session)
    return WhatNext(focus=ranked[:focus], candidates=ranked[focus : focus + candidates])


def what_next_markdown(session: Session, llm: LLMClient | None = None, limit: int = 10) -> str:
    """Markdown 'what next' list; if an LLM is provided, adds a narrative."""
    ranked = rank_tasks(session, limit=limit)
    lines = ["# What next", ""]
    if not ranked:
        lines.append("Nothing tracked — all clear.")
        return "\n".join(lines) + "\n"

    for i, entry in enumerate(ranked, 1):
        task = entry.task
        assignee = f" @{task.assignee.name}" if task.assignee else ""
        lines.append(
            f"{i}. **{task.title}**{assignee} — score {entry.score:.0f}"
            f" ({', '.join(entry.reasons)})"
        )

    if llm is not None:
        summary_input = "\n".join(
            f"- {e.task.title} [{e.task.priority.value}] ({', '.join(e.reasons)})" for e in ranked
        )
        result = llm.complete(
            f"Here are my current top tasks:\n{summary_input}\n\n"
            "In 3-5 sentences, advise what to focus on today and why.",
            system="You are a pragmatic chief of staff for an engineering manager.",
        )
        lines.extend(["", "## Advice", "", result.text])

    return "\n".join(lines) + "\n"


def upcoming_deadlines(session: Session, days: int = 14, today: date | None = None) -> list[Task]:
    today = today or _now().date()
    return [
        t
        for t in list_tasks(session)
        if t.due_date is not None and (t.due_date - today).days <= days
    ]
