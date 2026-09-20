"""1:1 meetings: creation, carry-forward, agenda markdown."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from ewo.core import one_on_ones, people, tasks
from ewo.core.people import NotFoundError
from ewo.db.models import AgendaItemStatus


def test_create_and_agenda(session: Session) -> None:
    anna = people.create_person(session, "Anna")
    tasks.create_task(session, "ship exporter", assignee_id=anna.id, due_date=date(2026, 9, 1))

    meeting = one_on_ones.create_one_on_one(session, anna.id, date(2026, 9, 2))
    one_on_ones.add_agenda_item(session, meeting.id, "career growth")

    markdown = one_on_ones.build_agenda_markdown(session, meeting.id)
    assert "# 1:1 — Anna — 2026-09-02" in markdown
    assert "- [ ] career growth" in markdown
    assert "ship exporter" in markdown
    assert "(due 2026-09-01)" in markdown


def test_agenda_empty_sections(session: Session) -> None:
    bob = people.create_person(session, "Bob")
    meeting = one_on_ones.create_one_on_one(session, bob.id, date(2026, 9, 2))
    markdown = one_on_ones.build_agenda_markdown(session, meeting.id)
    assert markdown.count("- (none)") == 2


def test_carry_forward(session: Session) -> None:
    anna = people.create_person(session, "Anna")
    first = one_on_ones.create_one_on_one(session, anna.id, date(2026, 9, 2))
    kept = one_on_ones.add_agenda_item(session, first.id, "still open")
    discussed = one_on_ones.add_agenda_item(session, first.id, "was discussed")
    one_on_ones.set_agenda_item_status(session, discussed.id, AgendaItemStatus.DISCUSSED)

    second = one_on_ones.create_one_on_one(session, anna.id, date(2026, 9, 16))
    topics = [i.topic for i in second.agenda_items]
    assert topics == ["still open"]
    assert kept.status == AgendaItemStatus.OPEN


def test_list_one_on_ones(session: Session) -> None:
    anna = people.create_person(session, "Anna")
    bob = people.create_person(session, "Bob")
    one_on_ones.create_one_on_one(session, anna.id, date(2026, 9, 2))
    one_on_ones.create_one_on_one(session, bob.id, date(2026, 9, 3))

    assert len(one_on_ones.list_one_on_ones(session)) == 2
    assert len(one_on_ones.list_one_on_ones(session, person_id=anna.id)) == 1


def test_not_found_errors(session: Session) -> None:
    with pytest.raises(NotFoundError):
        one_on_ones.create_one_on_one(session, 999, date(2026, 9, 2))
    with pytest.raises(NotFoundError):
        one_on_ones.get_one_on_one(session, 999)
    with pytest.raises(NotFoundError):
        one_on_ones.add_agenda_item(session, 999, "x")
    with pytest.raises(NotFoundError):
        one_on_ones.set_agenda_item_status(session, 999, AgendaItemStatus.DROPPED)
