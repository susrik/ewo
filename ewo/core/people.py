"""People service layer: plain functions over a sync Session.

People are team members (plus one ``is_self`` row for the owner), not login
accounts. They can be linked to a folder in the notes tree so items found in
notes are attributed to the right person.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.db.models import Person


class NotFoundError(Exception):
    """Raised when an entity does not exist."""


def create_person(
    session: Session,
    name: str,
    jira_account_id: str | None = None,
    discord_user_id: str | None = None,
    is_self: bool = False,
    notes_dir: str | None = None,
    aliases: list[str] | None = None,
) -> Person:
    person = Person(
        name=name,
        jira_account_id=jira_account_id,
        discord_user_id=discord_user_id,
        is_self=is_self,
        notes_dir=notes_dir,
        aliases=aliases or [],
    )
    session.add(person)
    if is_self:
        _clear_other_self(session, person)
    session.commit()
    return person


def _clear_other_self(session: Session, keep: Person) -> None:
    """There is exactly one owner: setting ``is_self`` unsets it elsewhere."""
    for other in session.scalars(select(Person).where(Person.is_self.is_(True))):
        if other is not keep:
            other.is_self = False


def get_person(session: Session, person_id: int) -> Person:
    person = session.get(Person, person_id)
    if person is None:
        raise NotFoundError(f"person {person_id} not found")
    return person


def get_self(session: Session) -> Person | None:
    return session.scalars(select(Person).where(Person.is_self.is_(True))).first()


def find_person_by_name(session: Session, name: str) -> Person | None:
    return session.scalars(select(Person).where(Person.name.ilike(name))).first()


def list_people(session: Session) -> list[Person]:
    return list(session.scalars(select(Person).order_by(Person.name)))


def update_person(session: Session, person_id: int, **fields: object) -> Person:
    person = get_person(session, person_id)
    for key, value in fields.items():
        setattr(person, key, value)
    if person.is_self:
        _clear_other_self(session, person)
    session.commit()
    return person


def delete_person(session: Session, person_id: int) -> None:
    person = get_person(session, person_id)
    session.delete(person)
    session.commit()


def resolve_person(
    session: Session, name: str | None, note_path: str | None = None
) -> Person | None:
    """Map a name used in notes to a Person.

    A note inside a person's ``notes_dir`` wins over name matching, so a bare
    "James" in ``swd/people/james_l/`` resolves unambiguously. Otherwise the
    name is matched case-insensitively against ``name`` and ``aliases``; an
    ambiguous alias (shared by several people) resolves to nobody.
    """
    people = list_people(session)
    if note_path:
        folder = PurePosixPath(note_path).parent.as_posix()
        for person in people:
            if person.notes_dir and (
                folder == person.notes_dir or folder.startswith(person.notes_dir + "/")
            ):
                return person
    if not name:
        return None
    wanted = name.strip().lower()
    matches = [
        p for p in people if p.name.lower() == wanted or any(a.lower() == wanted for a in p.aliases)
    ]
    return matches[0] if len(matches) == 1 else None


def seed_from_notes(session: Session, notes_dirs: list[str]) -> list[Person]:
    """Create a Person per team-member folder (``swd/people/<name>``) if missing.

    The folder name becomes the person's name (``james_l`` → ``James L``); the
    folder path is stored as ``notes_dir``. Existing people are left alone,
    except that an unlinked person with a matching name gets ``notes_dir`` set.
    """
    created: list[Person] = []
    existing = {p.notes_dir for p in list_people(session) if p.notes_dir}
    for notes_dir in notes_dirs:
        if notes_dir in existing:
            continue
        name = _name_from_dir(notes_dir)
        person = find_person_by_name(session, name)
        if person is None:
            person = Person(name=name, notes_dir=notes_dir, aliases=[])
            session.add(person)
            created.append(person)
        elif person.notes_dir is None:
            person.notes_dir = notes_dir
    session.commit()
    return created


def _name_from_dir(notes_dir: str) -> str:
    return " ".join(part.capitalize() for part in PurePosixPath(notes_dir).name.split("_"))
