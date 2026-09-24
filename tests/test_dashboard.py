"""Dashboard numbers and warnings."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from ewo.config import Config
from ewo.core import note_items, people, tasks
from ewo.core.dashboard import build_dashboard
from ewo.db.models import JobRun, JobRunStatus, NoteItemKind, TaskPriority, TaskStatus


def test_dashboard_counts_and_warnings(session: Session, config: Config) -> None:
    today = date(2026, 9, 21)
    tasks.create_task(session, "crit", priority=TaskPriority.CRITICAL)
    tasks.create_task(session, "late", due_date=today - timedelta(days=1))
    tasks.create_task(session, "soon", due_date=today + timedelta(days=3))
    blocked = tasks.create_task(session, "blocked")
    tasks.update_task(session, blocked.id, status=TaskStatus.BLOCKED)
    done = tasks.create_task(session, "done")
    tasks.update_task(session, done.id, status=TaskStatus.DONE)
    note_items.upsert_item(session, "x.md", 1, "new item", NoteItemKind.ACTION)
    session.add(JobRun(job_name="notes_scan", status=JobRunStatus.FAILED))
    session.add(
        JobRun(
            job_name="jira_sync",
            status=JobRunStatus.FAILED,
            started_at=datetime.now() - timedelta(days=2),
        )
    )
    session.commit()

    board = build_dashboard(session, config, today=today)
    assert board.open_tasks == 4
    assert board.critical == 1 and board.overdue == 1 and board.due_soon == 1
    assert board.blocked == 1
    assert board.inbox_new == 1
    assert board.failed_jobs_24h == 1
    assert board.last_scan is not None and board.last_scan.status == JobRunStatus.FAILED
    assert any("disabled" in w for w in board.warnings)
    assert any("is_self" in w for w in board.warnings)
    assert any("last notes scan failed" in w for w in board.warnings)
    assert any("failed in the last 24h" in w for w in board.warnings)


def test_dashboard_llm_warning_and_clean(session: Session, config: Config) -> None:
    config.notes.enabled = True
    board = build_dashboard(session, config)
    assert any("No LLM API key" in w for w in board.warnings)

    config.llm.api_key = "sk"
    people.create_person(session, "Me", is_self=True)
    assert build_dashboard(session, config).warnings == []
