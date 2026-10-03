"""Tag/label service layer: plain functions over a sync Session.

Tags are shared labels that can be attached to any task. A tag may carry an
optional free-text description ("label"). Matching is always done on the
normalized (stripped, lowercased) name.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.core.people import NotFoundError
from ewo.db.models import Tag, Task

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_name(name: str) -> str:
    """Canonical form of a tag name: stripped and lowercased."""
    return name.strip().lower()


def normalize_color(color: str | None) -> str | None:
    """Validate/uppercase a hex color; blank is allowed and means "no color"."""
    if color is None:
        return None
    stripped = color.strip()
    if not stripped:
        return None
    if not _HEX_COLOR.match(stripped):
        raise ValueError("label color must be a hex value like #EE7733")
    return stripped.upper()


def _get_or_create_tags(session: Session, names: list[str]) -> list[Tag]:
    tags: dict[str, Tag] = {}
    for name in names:
        normalized = normalize_name(name)
        if not normalized or normalized in tags:
            continue
        tag = session.scalars(select(Tag).where(Tag.name == normalized)).first()
        if tag is None:
            tag = Tag(name=normalized)
            session.add(tag)
        tags[normalized] = tag
    return list(tags.values())


def get_tag(session: Session, tag_id: int) -> Tag:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise NotFoundError(f"tag {tag_id} not found")
    return tag


def list_tags(session: Session) -> list[Tag]:
    return list(session.scalars(select(Tag).order_by(Tag.name)))


def create_tag(
    session: Session, name: str, description: str | None = None, color: str | None = None
) -> Tag:
    """Get-or-create a tag by normalized name; a given description/color is set."""
    normalized = normalize_name(name)
    if not normalized:
        raise ValueError("tag name cannot be empty")
    resolved_color = normalize_color(color)
    tag = session.scalars(select(Tag).where(Tag.name == normalized)).first()
    if tag is None:
        tag = Tag(name=normalized, description=description, color=resolved_color)
        session.add(tag)
    else:
        if description is not None:
            tag.description = description
        if color is not None:
            tag.color = resolved_color
    session.commit()
    return tag


def update_tag(
    session: Session,
    tag_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    color: str | None = None,
) -> Tag:
    """Rename, re-describe and/or recolor a tag.

    ``None`` leaves a field unchanged; a blank description/color clears the
    field. Renaming to another tag's (normalized) name raises ValueError.
    """
    tag = get_tag(session, tag_id)
    if name is not None:
        normalized = normalize_name(name)
        if not normalized:
            raise ValueError("tag name cannot be empty")
        existing = session.scalars(select(Tag).where(Tag.name == normalized)).first()
        if existing is not None and existing.id != tag.id:
            raise ValueError(f"tag {normalized!r} already exists")
        tag.name = normalized
    if description is not None:
        tag.description = description.strip() or None
    if color is not None:
        tag.color = normalize_color(color)
    session.commit()
    return tag


def delete_tag(session: Session, tag_id: int) -> None:
    """Delete a tag, detaching it from every task first."""
    tag = get_tag(session, tag_id)
    for task in list(tag.tasks):
        task.tags.remove(tag)
    session.delete(tag)
    session.commit()


def descendant_ids(session: Session, task_id: int) -> set[int]:
    """All ids below ``task_id`` in the task tree (iterative children walk)."""
    task = session.get(Task, task_id)
    if task is None:
        raise NotFoundError(f"task {task_id} not found")
    ids: set[int] = set()
    stack = list(task.children)
    while stack:
        child = stack.pop()
        if child.id in ids:
            continue
        ids.add(child.id)
        stack.extend(child.children)
    return ids


def add_tags_to_descendants(session: Session, task_id: int, tag_names: list[str]) -> int:
    """Add tags to every descendant of ``task_id`` (additive only).

    Descendants keep any tags they already have. Returns how many
    descendants gained at least one tag.
    """
    ids = descendant_ids(session, task_id)
    names = [n for n in (normalize_name(name) for name in tag_names) if n]
    if not names or not ids:
        return 0
    tag_objs = _get_or_create_tags(session, names)
    changed = 0
    for descendant_id in ids:
        descendant = session.get(Task, descendant_id)
        if descendant is None:
            continue
        gained = False
        for tag in tag_objs:
            if tag not in descendant.tags:
                descendant.tags.append(tag)
                gained = True
        if gained:
            changed += 1
    session.commit()
    return changed
