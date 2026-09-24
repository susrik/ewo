"""What-next ranking and report generation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from ewo.core import note_items, people, reports, tasks
from ewo.core.llm import FakeLLM
from ewo.core.whatnext import (
    rank_all,
    rank_tasks,
    score_task,
    upcoming_deadlines,
    what_next,
    what_next_markdown,
)
from ewo.db.models import NoteItemKind, TaskPriority, TaskStatus


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


def test_score_more_signals(session: Session) -> None:
    me = people.create_person(session, "Me", is_self=True)
    mine = tasks.create_task(session, "mine", assignee_id=me.id)
    theirs = tasks.create_task(session, "theirs")
    assert score_task(mine, self_id=me.id).score > score_task(theirs, self_id=me.id).score
    assert "mine" in score_task(mine, self_id=me.id).reasons

    blocked = tasks.create_task(session, "blocked")
    tasks.update_task(session, blocked.id, status=TaskStatus.BLOCKED)
    assert score_task(blocked).score < score_task(theirs).score
    assert "blocked" in score_task(blocked).reasons

    started = tasks.create_task(session, "started")
    tasks.update_task(session, started.id, status=TaskStatus.IN_PROGRESS)
    assert "in progress" in score_task(started).reasons

    fortnight = tasks.create_task(session, "soonish", due_date=date.today() + timedelta(days=10))
    assert any(r.startswith("due in") for r in score_task(fortnight).reasons)

    old = score_task(theirs, now=_now() + timedelta(days=70))
    assert "open 70d" in old.reasons

    recent = score_task(theirs, last_seen=_now() - timedelta(days=1))
    assert "in recent notes" in recent.reasons
    assert (
        "in recent notes" not in score_task(theirs, last_seen=_now() - timedelta(days=30)).reasons
    )


def test_rank_all_uses_self_notes_and_jitter(session: Session) -> None:
    me = people.create_person(session, "Me", is_self=True)
    a = tasks.create_task(session, "a", assignee_id=me.id)
    tasks.create_task(session, "b")
    tasks.create_task(session, "c")
    item, _ = note_items.upsert_item(session, "n.md", 1, "b", NoteItemKind.ACTION)
    session.commit()
    note_items.accept_item(session, item.id)  # creates a 4th task linked to the note
    linked = item.task_id

    ranked = rank_all(session, seed="fixed")
    by_title = {r.task.title: r for r in ranked}
    assert ranked[0].task.id == a.id  # mine wins
    assert "in recent notes" in next(r for r in ranked if r.task.id == linked).reasons
    # jitter breaks the tie between equal b and c, deterministically for a seed
    assert by_title["b"].score != by_title["c"].score
    again = rank_all(session, seed="fixed")
    assert [r.task.id for r in again] == [r.task.id for r in ranked]

    result = what_next(session, focus=1, candidates=2)
    assert len(result.focus) == 1 and len(result.candidates) == 2


def test_upcoming_deadlines(session: Session) -> None:
    tasks.create_task(session, "soon", due_date=date.today() + timedelta(days=3))
    tasks.create_task(session, "late", due_date=date.today() - timedelta(days=3))
    tasks.create_task(session, "far", due_date=date.today() + timedelta(days=60))
    tasks.create_task(session, "none")
    assert sorted(t.title for t in upcoming_deadlines(session)) == ["late", "soon"]


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
