"""People and task service layer."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from ewo.core import people, tasks
from ewo.core.people import NotFoundError
from ewo.db.models import TaskPriority, TaskSource, TaskStatus


def test_person_crud(session: Session) -> None:
    person = people.create_person(session, "Anna", email="anna@x.com")
    assert person.id is not None

    fetched = people.get_person(session, person.id)
    assert fetched.name == "Anna"

    assert people.find_person_by_name(session, "anna") is not None
    assert people.find_person_by_name(session, "nobody") is None

    people.update_person(session, person.id, email="new@x.com")
    assert people.get_person(session, person.id).email == "new@x.com"

    assert [p.name for p in people.list_people(session)] == ["Anna"]

    people.delete_person(session, person.id)
    assert people.list_people(session) == []


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


def test_notes_and_links(session: Session) -> None:
    task = tasks.create_task(session, "with note")
    note = tasks.add_note(session, "a note", task_id=task.id)
    assert note.id is not None

    link = tasks.link_external(session, task.id, "jira", "PROJ-1", url="http://j/PROJ-1")
    assert link.external_key == "PROJ-1"
    assert tasks.find_link(session, "jira", "PROJ-1") is not None
    assert tasks.find_link(session, "jira", "PROJ-2") is None
