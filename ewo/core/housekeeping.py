"""Housekeeping: periodic checks & fixes for data-integrity issues.

First check: label inheritance. A child created under a parent inherits the
parent's tags (see ``tasks.create_task``), and tags added to a parent later
are propagated only on explicit user confirmation. This sweep walks the task
tree roots-first and adds any missing ancestor tags to each descendant —
additive only: tags a child already has are never removed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ewo.db.models import Task


@dataclass
class HousekeepingSummary:
    checked: int = 0
    fixed: int = 0

    def as_text(self) -> str:
        return f"checked={self.checked} fixed={self.fixed}"


def propagate_parent_tags(session: Session) -> HousekeepingSummary:
    """Add each task's ancestor tags to the task itself (additive only).

    Roots-first walk: a parent is fixed before its children are visited, so
    tags propagate all the way down the tree in a single pass. Returns the
    number of tasks checked and how many gained at least one tag.
    """
    tasks = list(session.scalars(select(Task).options(selectinload(Task.tags))))
    by_id = {task.id: task for task in tasks}
    children: dict[int | None, list[Task]] = {}
    for task in tasks:
        children.setdefault(task.parent_id, []).append(task)
    summary = HousekeepingSummary()
    queue = deque(children.get(None, []))
    while queue:
        task = queue.popleft()
        summary.checked += 1
        parent = by_id.get(task.parent_id) if task.parent_id is not None else None
        if parent is not None:
            have = {tag.id for tag in task.tags}
            missing = [tag for tag in parent.tags if tag.id not in have]
            if missing:
                task.tags.extend(missing)
                summary.fixed += 1
        queue.extend(children.get(task.id, []))
    return summary


def run_housekeeping(session: Session) -> HousekeepingSummary:
    """Run all integrity checks, committing any fixes."""
    summary = propagate_parent_tags(session)
    session.commit()
    return summary
