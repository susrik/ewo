"""Housekeeping: additive parent-label propagation over the task tree."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ewo.core import tasks
from ewo.core.housekeeping import HousekeepingSummary, run_housekeeping
from ewo.db.models import Task


def _names(task: Task) -> list[str]:
    return sorted(t.name for t in task.tags)


def test_propagate_parent_tags_fills_missing(session: Session) -> None:
    root = tasks.create_task(session, "root", tags=["root-tag"])
    child = tasks.create_task(session, "child", parent_id=root.id, tags=["extra"])
    grand = tasks.create_task(session, "grand", parent_id=child.id)
    # drift: tags added to parents after the children already exist
    tasks.update_task(session, root.id, tags=["root-tag", "late-root"])
    tasks.update_task(session, child.id, tags=["root-tag", "extra", "late-child"])

    summary = run_housekeeping(session)

    assert summary.checked == 3
    assert summary.fixed == 2  # child gains late-root; grand gains late-root + late-child
    expected = ["extra", "late-child", "late-root", "root-tag"]
    assert _names(tasks.get_task(session, child.id)) == expected
    # roots-first: the grandchild also picks up tags the child just gained
    assert _names(tasks.get_task(session, grand.id)) == expected
    # the root itself is untouched
    assert _names(tasks.get_task(session, root.id)) == ["late-root", "root-tag"]


def test_propagate_parent_tags_is_additive_and_idempotent(session: Session) -> None:
    root = tasks.create_task(session, "root")
    child = tasks.create_task(session, "child", parent_id=root.id, tags=["keep-me"])
    tasks.update_task(session, root.id, tags=["new-tag"])

    assert run_housekeeping(session).fixed == 1
    assert _names(tasks.get_task(session, child.id)) == ["keep-me", "new-tag"]

    second = run_housekeeping(session)
    assert second.checked == 2
    assert second.fixed == 0
    assert _names(tasks.get_task(session, child.id)) == ["keep-me", "new-tag"]


def test_housekeeping_empty_db(session: Session) -> None:
    summary = run_housekeeping(session)
    assert summary == HousekeepingSummary(checked=0, fixed=0)
    assert summary.as_text() == "checked=0 fixed=0"
