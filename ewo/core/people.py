"""People service layer: plain functions over a sync Session."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.db.models import Person


class NotFoundError(Exception):
    """Raised when an entity does not exist."""


def create_person(
    session: Session,
    name: str,
    email: str | None = None,
    jira_account_id: str | None = None,
    discord_user_id: str | None = None,
    is_self: bool = False,
) -> Person:
    person = Person(
        name=name,
        email=email,
        jira_account_id=jira_account_id,
        discord_user_id=discord_user_id,
        is_self=is_self,
    )
    session.add(person)
    session.commit()
    return person


def get_person(session: Session, person_id: int) -> Person:
    person = session.get(Person, person_id)
    if person is None:
        raise NotFoundError(f"person {person_id} not found")
    return person


def find_person_by_name(session: Session, name: str) -> Person | None:
    return session.scalars(select(Person).where(Person.name.ilike(name))).first()


def list_people(session: Session) -> list[Person]:
    return list(session.scalars(select(Person).order_by(Person.name)))


def update_person(session: Session, person_id: int, **fields: object) -> Person:
    person = get_person(session, person_id)
    for key, value in fields.items():
        setattr(person, key, value)
    session.commit()
    return person


def delete_person(session: Session, person_id: int) -> None:
    person = get_person(session, person_id)
    session.delete(person)
    session.commit()
