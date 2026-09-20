"""What-next ranking and report generation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from ewo.core import people, reports, tasks
from ewo.core.llm import FakeLLM
from ewo.core.whatnext import rank_tasks, score_task, what_next_markdown
from ewo.db.models import TaskPriority, TaskStatus


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def test_score_priority_and_due(session: Session) -> None:
    critical = tasks.create_task(session, "critical", priority=TaskPriority.CRITICAL)
    low = tasks.create_task(session, "low", priority=TaskPriority.LOW)
    assert score_task(critical).score > score_task(low).score

    overdue = tasks.create_task(session, "overdue", due_date=date.today() - timedelta(days=2))
    entry = score_task(overdue)
    assert any("overdue" in r for r in entry.reasons)

    soon = tasks.create_task(session, "soon", due_date=date.today() + timedelta(days=1))
    assert any("due in" in r for r in score_task(soon).reasons)


def test_score_staleness(session: Session) -> None:
    task = tasks.create_task(session, "stale")
    stale_entry = score_task(task, now=_now() + timedelta(days=10))
    assert any("untouched" in r for r in stale_entry.reasons)


def test_rank_and_markdown(session: Session) -> None:
    anna = people.create_person(session, "Anna")
    tasks.create_task(session, "big one", priority=TaskPriority.CRITICAL, assignee_id=anna.id)
    tasks.create_task(session, "small one", priority=TaskPriority.LOW)

    ranked = rank_tasks(session)
    assert ranked[0].task.title == "big one"

    markdown = what_next_markdown(session)
    assert "1. **big one** @Anna" in markdown
    assert "## Advice" not in markdown


def test_whatnext_markdown_with_llm(session: Session) -> None:
    tasks.create_task(session, "a task")
    fake = FakeLLM(responses=["focus on the task"])
    markdown = what_next_markdown(session, llm=fake)
    assert "## Advice" in markdown
    assert "focus on the task" in markdown
    assert "a task" in str(fake.calls[0]["prompt"])


def test_whatnext_markdown_empty(session: Session) -> None:
    markdown = what_next_markdown(session)
    assert "all clear" in markdown


def test_daily_report(session: Session) -> None:
    anna = people.create_person(session, "Anna")
    tasks.create_task(session, "for anna", assignee_id=anna.id)
    tasks.create_task(session, "floating")
    done = tasks.create_task(session, "finished")
    tasks.update_task(session, done.id, status=TaskStatus.DONE)

    markdown = reports.daily_report_markdown(session)
    assert "## Status summary" in markdown
    assert "- open: 2" in markdown
    assert "- done: 1" in markdown
    assert "### Anna" in markdown
    assert "### (unassigned)" in markdown
    assert "# What next" in markdown


def test_daily_report_empty(session: Session) -> None:
    markdown = reports.daily_report_markdown(session)
    assert "no tasks tracked" in markdown
    assert "- (none)" in markdown


def test_record_and_list_reports(session: Session) -> None:
    saved = reports.record_report(session, "daily", "body", repo_path="ewo/x.md")
    assert saved.id is not None
    listed = reports.list_reports(session)
    assert [r.id for r in listed] == [saved.id]
