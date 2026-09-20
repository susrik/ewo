"""1:1 meetings: discussion topics, agendas, carry-forward of open items."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ewo.core.people import NotFoundError, get_person
from ewo.core.tasks import list_tasks
from ewo.db.models import AgendaItem, AgendaItemStatus, OneOnOne


def create_one_on_one(session: Session, person_id: int, scheduled_for: date) -> OneOnOne:
    """Create a 1:1; open agenda items from the previous 1:1 carry forward."""
    get_person(session, person_id)  # validate
    meeting = OneOnOne(person_id=person_id, scheduled_for=scheduled_for)

    previous = session.scalars(
        select(OneOnOne)
        .where(OneOnOne.person_id == person_id)
        .order_by(OneOnOne.scheduled_for.desc())
    ).first()
    if previous is not None:
        for item in previous.agenda_items:
            if item.status == AgendaItemStatus.OPEN:
                meeting.agenda_items.append(AgendaItem(topic=item.topic))

    session.add(meeting)
    session.commit()
    return meeting


def get_one_on_one(session: Session, meeting_id: int) -> OneOnOne:
    meeting = session.get(OneOnOne, meeting_id)
    if meeting is None:
        raise NotFoundError(f"one-on-one {meeting_id} not found")
    return meeting


def list_one_on_ones(session: Session, person_id: int | None = None) -> list[OneOnOne]:
    query = (
        select(OneOnOne)
        .options(selectinload(OneOnOne.agenda_items), selectinload(OneOnOne.person))
        .order_by(OneOnOne.scheduled_for.desc())
    )
    if person_id is not None:
        query = query.where(OneOnOne.person_id == person_id)
    return list(session.scalars(query).unique())


def add_agenda_item(session: Session, meeting_id: int, topic: str) -> AgendaItem:
    meeting = get_one_on_one(session, meeting_id)
    item = AgendaItem(one_on_one_id=meeting.id, topic=topic)
    session.add(item)
    session.commit()
    return item


def set_agenda_item_status(session: Session, item_id: int, status: AgendaItemStatus) -> AgendaItem:
    item = session.get(AgendaItem, item_id)
    if item is None:
        raise NotFoundError(f"agenda item {item_id} not found")
    item.status = status
    session.commit()
    return item


def build_agenda_markdown(session: Session, meeting_id: int) -> str:
    """Assemble a 1:1 agenda: topics + the person's open tasks."""
    meeting = get_one_on_one(session, meeting_id)
    person = meeting.person
    lines = [f"# 1:1 — {person.name} — {meeting.scheduled_for.isoformat()}", ""]

    lines.append("## Discussion topics")
    open_items = [i for i in meeting.agenda_items if i.status == AgendaItemStatus.OPEN]
    if open_items:
        lines.extend(f"- [ ] {item.topic}" for item in open_items)
    else:
        lines.append("- (none)")

    lines.extend(["", "## Open tasks"])
    tasks = list_tasks(session, assignee_id=person.id)
    if tasks:
        for task in tasks:
            due = f" (due {task.due_date.isoformat()})" if task.due_date else ""
            lines.append(f"- [{task.status.value}] {task.title} — {task.priority.value}{due}")
    else:
        lines.append("- (none)")

    return "\n".join(lines) + "\n"
