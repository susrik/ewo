"""Live "what next" ranking — computed on request, never stored.

Deterministic scoring (priority, due date, staleness) with an optional LLM
narrative on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ewo.core.llm import LLMClient
from ewo.core.tasks import list_tasks
from ewo.db.models import Task, TaskPriority

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


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def score_task(task: Task, now: datetime | None = None) -> RankedTask:
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

    stale_days = (now - task.updated_at).days
    if stale_days >= 7:
        score += min(stale_days, 30)
        reasons.append(f"untouched {stale_days}d")

    return RankedTask(task=task, score=score, reasons=reasons)


def rank_tasks(session: Session, limit: int = 10) -> list[RankedTask]:
    ranked = [score_task(task) for task in list_tasks(session)]
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:limit]


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
