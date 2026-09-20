"""Report generation: daily overview, what-next, 1:1 agenda."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.core.llm import LLMClient
from ewo.core.tasks import list_tasks
from ewo.core.whatnext import what_next_markdown
from ewo.db.models import Report, Task, TaskStatus


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def daily_report_markdown(session: Session, llm: LLMClient | None = None) -> str:
    """Daily overview: task counts per status, per-person open work, what-next."""
    now = _now()
    lines = [f"# Daily report — {now.strftime('%Y-%m-%d %H:%M')}", ""]

    all_tasks = list(session.scalars(select(Task)))
    lines.append("## Status summary")
    for status in TaskStatus:
        count = sum(1 for t in all_tasks if t.status == status)
        if count:
            lines.append(f"- {status.value}: {count}")
    if not all_tasks:
        lines.append("- no tasks tracked")

    lines.extend(["", "## Open tasks by person"])
    open_tasks = list_tasks(session)
    by_person: dict[str, list[Task]] = {}
    for task in open_tasks:
        name = task.assignee.name if task.assignee else "(unassigned)"
        by_person.setdefault(name, []).append(task)
    if by_person:
        for name in sorted(by_person):
            lines.append(f"### {name}")
            for task in by_person[name]:
                due = f" (due {task.due_date.isoformat()})" if task.due_date else ""
                lines.append(f"- [{task.status.value}] {task.title}{due}")
    else:
        lines.append("- (none)")

    lines.extend(["", what_next_markdown(session, llm=llm)])
    return "\n".join(lines)


def record_report(
    session: Session,
    report_type: str,
    body: str,
    repo_path: str | None = None,
    job_run_id: int | None = None,
) -> Report:
    report = Report(report_type=report_type, body=body, repo_path=repo_path, job_run_id=job_run_id)
    session.add(report)
    session.commit()
    return report


def list_reports(session: Session, limit: int = 50) -> list[Report]:
    return list(session.scalars(select(Report).order_by(Report.created_at.desc()).limit(limit)))
