"""Numbers and warnings for the dashboard page."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ewo.config import Config
from ewo.core import note_items
from ewo.core.people import get_self
from ewo.core.tasks import list_tasks
from ewo.db.models import JobRun, JobRunStatus, TaskPriority, TaskStatus


@dataclass
class Dashboard:
    open_tasks: int = 0
    critical: int = 0
    overdue: int = 0
    due_soon: int = 0
    blocked: int = 0
    inbox_new: int = 0
    failed_jobs_24h: int = 0
    last_scan: JobRun | None = None
    warnings: list[str] = field(default_factory=list)


def build_dashboard(session: Session, config: Config, today: date | None = None) -> Dashboard:
    today = today or datetime.now(UTC).date()
    board = Dashboard()

    for task in list_tasks(session):
        board.open_tasks += 1
        if task.priority == TaskPriority.CRITICAL:
            board.critical += 1
        if task.status == TaskStatus.BLOCKED:
            board.blocked += 1
        if task.due_date is not None:
            if task.due_date < today:
                board.overdue += 1
            elif (task.due_date - today).days <= 7:
                board.due_soon += 1

    board.inbox_new = note_items.count_new(session)

    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
    board.failed_jobs_24h = (
        session.scalar(
            select(func.count())
            .select_from(JobRun)
            .where(JobRun.status == JobRunStatus.FAILED, JobRun.started_at >= since)
        )
        or 0
    )
    board.last_scan = session.scalars(
        select(JobRun)
        .where(JobRun.job_name == "notes_scan")
        .order_by(JobRun.started_at.desc())
        .limit(1)
    ).first()

    if not config.notes.enabled:
        board.warnings.append("Notes scanning is disabled (config.notes.enabled).")
    elif not config.llm.api_key:
        board.warnings.append("No LLM API key configured — notes_scan cannot run.")
    if get_self(session) is None:
        board.warnings.append(
            "No person is marked as you (is_self) — items from notes can't be assigned to you."
        )
    if board.last_scan is not None and board.last_scan.status == JobRunStatus.FAILED:
        board.warnings.append("The last notes scan failed — see Jobs.")
    if board.failed_jobs_24h:
        board.warnings.append(f"{board.failed_jobs_24h} job run(s) failed in the last 24h.")
    return board
