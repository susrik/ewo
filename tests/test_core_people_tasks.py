"""People and task service layer."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ewo.core import people, tasks
from ewo.core.people import NotFoundError
from ewo.db.models import TaskPriority, TaskSource, TaskStatus


def test_person_crud(session: Session) -> None:
    person = people.create_person(session, "Anna", aliases=["Ann"])
    assert person.id is not None

    fetched = people.get_person(session, person.id)
    assert fetched.name == "Anna"
    assert fetched.aliases == ["Ann"]

    assert people.find_person_by_name(session, "anna") is not None
    assert people.find_person_by_name(session, "nobody") is None

    people.update_person(session, person.id, notes_dir="swd/people/anna")
    assert people.get_person(session, person.id).notes_dir == "swd/people/anna"

    assert [p.name for p in people.list_people(session)] == ["Anna"]

    people.delete_person(session, person.id)
    assert people.list_people(session) == []


def test_single_self(session: Session) -> None:
    me = people.create_person(session, "Me", is_self=True)
    assert people.get_self(session) is me
    other = people.create_person(session, "Other", is_self=True)
    assert people.get_self(session) is other
    assert people.get_person(session, me.id).is_self is False
    people.update_person(session, me.id, is_self=True)
    assert people.get_person(session, other.id).is_self is False


def test_resolve_person(session: Session) -> None:
    james_l = people.create_person(session, "James L", notes_dir="swd/people/james_l")
    james_s = people.create_person(
        session, "James S", notes_dir="swd/people/james_s", aliases=["James"]
    )
    people.create_person(session, "Sam", aliases=["Samuel"])

    # folder wins over an ambiguous name
    assert people.resolve_person(session, "James", "swd/people/james_l/2026-09-01-x.md") is james_l
    assert people.resolve_person(session, None, "swd/people/james_s/old.md") is james_s
    # unique alias resolves, exact name resolves, unknown does not
    assert people.resolve_person(session, "samuel", "misc/note.md").name == "Sam"
    assert people.resolve_person(session, "james s", None) is james_s
    assert people.resolve_person(session, "Nobody", "misc/note.md") is None
    assert people.resolve_person(session, None, None) is None


def test_ambiguous_alias_resolves_to_nobody(session: Session) -> None:
    people.create_person(session, "James L", aliases=["James"])
    people.create_person(session, "James S", aliases=["James"])
    assert people.resolve_person(session, "James", "misc/note.md") is None


def test_seed_from_notes(session: Session) -> None:
    people.create_person(session, "Sam")  # exists without notes_dir -> gets linked
    created = people.seed_from_notes(
        session, ["swd/people/james_l", "swd/people/sam", "swd/people/neda"]
    )
    assert sorted(p.name for p in created) == ["James L", "Neda"]
    assert people.find_person_by_name(session, "Sam").notes_dir == "swd/people/sam"  # type: ignore[union-attr]
    # idempotent
    assert people.seed_from_notes(session, ["swd/people/sam", "swd/people/neda"]) == []


def test_person_not_found(session: Session) -> None:
    with pytest.raises(NotFoundError):
        people.get_person(session, 999)


def test_task_crud_with_tags(session: Session) -> None:
    task = tasks.create_task(
        session,
        "Fix exporter",
        description="details",
        priority=TaskPriority.HIGH,
        source=TaskSource.DISCORD,
        due_date=date(2026, 9, 15),
        tags=["Infra", "infra", "  ", "backend"],
    )
    assert sorted(t.name for t in task.tags) == ["backend", "infra"]

    fetched = tasks.get_task(session, task.id)
    assert fetched.priority == TaskPriority.HIGH

    updated = tasks.update_task(session, task.id, status=TaskStatus.DONE, tags=["done-tag"])
    assert updated.status == TaskStatus.DONE
    assert [t.name for t in updated.tags] == ["done-tag"]

    tasks.delete_task(session, task.id)
    with pytest.raises(NotFoundError):
        tasks.get_task(session, task.id)


def test_task_dates_and_completion(session: Session) -> None:
    task = tasks.create_task(session, "dated", start_date=date(2026, 9, 20))
    assert task.start_date == date(2026, 9, 20)
    assert task.completed_at is None

    done = tasks.update_task(session, task.id, status=TaskStatus.DONE)
    assert done.completed_at is not None
    # staying done does not move the timestamp
    again = tasks.update_task(session, task.id, title="dated 2")
    assert again.completed_at == done.completed_at
    # reopening clears it; dropped does not count as completed
    reopened = tasks.update_task(session, task.id, status=TaskStatus.OPEN)
    assert reopened.completed_at is None
    dropped = tasks.update_task(session, task.id, status=TaskStatus.DROPPED)
    assert dropped.completed_at is None


def test_task_hierarchy(session: Session) -> None:
    epic = tasks.create_task(session, "epic")
    story = tasks.create_task(session, "story", parent_id=epic.id)
    sub = tasks.create_task(session, "sub", parent_id=story.id)
    assert [c.id for c in epic.children] == [story.id]
    assert sub.parent.id == story.id

    with pytest.raises(ValueError, match="own parent"):
        tasks.update_task(session, epic.id, parent_id=epic.id)
    with pytest.raises(ValueError, match="descendant"):
        tasks.update_task(session, epic.id, parent_id=sub.id)
    with pytest.raises(NotFoundError):
        tasks.update_task(session, epic.id, parent_id=999)

    # deleting a parent orphans the child (SET NULL), not deletes it
    tasks.delete_task(session, story.id)
    assert tasks.get_task(session, sub.id).parent_id is None


def test_delete_task_returns_nuggets_to_inbox(session: Session) -> None:
    from ewo.core import nuggets
    from ewo.db.models import NuggetKind, NuggetStatus

    task = tasks.create_task(session, "doomed")
    item, _ = nuggets.upsert_nugget(session, "x.md", 1, "attached bit", NuggetKind.ACTION)
    session.commit()
    nuggets.attach_nugget(session, item.id, task_id=task.id)

    tasks.delete_task(session, task.id)
    nugget = nuggets.get_nugget(session, item.id)
    assert nugget.status == NuggetStatus.NEW and nugget.task_id is None


def test_list_tasks_filters(session: Session) -> None:
    anna = people.create_person(session, "Anna")
    open_task = tasks.create_task(session, "open one", assignee_id=anna.id, tags=["x"])
    done_task = tasks.create_task(session, "done one")
    tasks.update_task(session, done_task.id, status=TaskStatus.DONE)

    default = tasks.list_tasks(session)
    assert [t.id for t in default] == [open_task.id]

    everything = tasks.list_tasks(session, include_closed=True)
    assert {t.id for t in everything} == {open_task.id, done_task.id}

    by_status = tasks.list_tasks(session, status=TaskStatus.DONE)
    assert [t.id for t in by_status] == [done_task.id]

    by_assignee = tasks.list_tasks(session, assignee_id=anna.id)
    assert [t.id for t in by_assignee] == [open_task.id]

    by_tag = tasks.list_tasks(session, tag="X")
    assert [t.id for t in by_tag] == [open_task.id]
    assert tasks.list_tasks(session, tag="none") == []


def test_child_inherits_parent_tags(session: Session) -> None:
    parent = tasks.create_task(session, "epic", tags=["Platform", "Q3"])
    child = tasks.create_task(session, "story", parent_id=parent.id, tags=["q3", "ui"])
    assert sorted(t.name for t in child.tags) == ["platform", "q3", "ui"]

    # inheritance flows through the direct parent, accumulating down the tree
    grand = tasks.create_task(session, "sub", parent_id=child.id)
    assert sorted(t.name for t in grand.tags) == ["platform", "q3", "ui"]

    # tasks without a parent keep only their own tags
    root = tasks.create_task(session, "root", tags=["x"])
    assert [t.name for t in root.tags] == ["x"]


def test_list_tasks_multi_tag_filter(session: Session) -> None:
    only_a = tasks.create_task(session, "a", tags=["alpha"])
    only_b = tasks.create_task(session, "b", tags=["beta"])
    both = tasks.create_task(session, "both", tags=["alpha", "beta"])

    any_match = tasks.list_tasks(session, tags=["Alpha", "beta"])
    assert {t.id for t in any_match} == {only_a.id, only_b.id, both.id}

    all_match = tasks.list_tasks(session, tags=["alpha", "beta"], tag_match="all")
    assert [t.id for t in all_match] == [both.id]

    # the legacy single-tag kwarg merges into the list
    legacy = tasks.list_tasks(session, tag="alpha", tags=["beta"], tag_match="all")
    assert [t.id for t in legacy] == [both.id]

    assert tasks.list_tasks(session, tags=["nope"]) == []


def test_notes_and_links(session: Session) -> None:
    task = tasks.create_task(session, "with note")
    note = tasks.add_note(session, "a note", task_id=task.id)
    assert note.id is not None

    link = tasks.link_external(session, task.id, "jira", "PROJ-1", url="http://j/PROJ-1")
    assert link.external_key == "PROJ-1"
    assert tasks.find_link(session, "jira", "PROJ-1") is not None
    assert tasks.find_link(session, "jira", "PROJ-2") is None

    # one issue on many tasks, but only once per task
    other = tasks.create_task(session, "also linked")
    tasks.link_external(session, other.id, "jira", "PROJ-1")
    assert len(tasks.find_links(session, "jira", "PROJ-1")) == 2
    with pytest.raises(IntegrityError):
        tasks.link_external(session, task.id, "jira", "PROJ-1")
    session.rollback()

    tasks.unlink_external(session, task.id, link.id)
    assert [link.task_id for link in tasks.find_links(session, "jira", "PROJ-1")] == [other.id]
    with pytest.raises(NotFoundError):
        tasks.unlink_external(session, task.id, link.id)
