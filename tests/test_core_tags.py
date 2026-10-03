"""Tag (label) service layer: CRUD, normalization, propagation."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from ewo.core import tags as tags_core
from ewo.core import tasks
from ewo.core.people import NotFoundError


def test_normalize_name() -> None:
    assert tags_core.normalize_name("  Infra  ") == "infra"
    assert tags_core.normalize_name("   ") == ""


def test_list_tags_ordered(session: Session) -> None:
    tags_core.create_tag(session, "zebra")
    tags_core.create_tag(session, "alpha")
    assert [t.name for t in tags_core.list_tags(session)] == ["alpha", "zebra"]


def test_create_tag_get_or_create(session: Session) -> None:
    first = tags_core.create_tag(session, "Infra")
    second = tags_core.create_tag(session, "  infra  ")
    assert second.id == first.id
    assert len(tags_core.list_tags(session)) == 1

    with pytest.raises(ValueError, match="empty"):
        tags_core.create_tag(session, "   ")


def test_create_tag_sets_description(session: Session) -> None:
    tag = tags_core.create_tag(session, "infra", description="platform work")
    assert tag.description == "platform work"
    # re-creating with a description updates it; omitting leaves it alone
    updated = tags_core.create_tag(session, "infra", description="platform & ops")
    assert updated.id == tag.id
    assert updated.description == "platform & ops"
    assert tags_core.create_tag(session, "infra").description == "platform & ops"


def test_tag_color(session: Session) -> None:
    tag = tags_core.create_tag(session, "infra", color=" #ee7733 ")
    assert tag.color == "#EE7733"

    updated = tags_core.update_tag(session, tag.id, color="#117733")
    assert updated.color == "#117733"

    cleared = tags_core.update_tag(session, tag.id, color="   ")
    assert cleared.color is None

    with pytest.raises(ValueError, match="hex value"):
        tags_core.update_tag(session, tag.id, color="red")


def test_update_tag(session: Session) -> None:
    tag = tags_core.create_tag(session, "old", description="d")
    updated = tags_core.update_tag(session, tag.id, name="New Name")
    assert updated.name == "new name"
    assert updated.description == "d"  # untouched when not provided

    updated = tags_core.update_tag(session, tag.id, description="  docs  ")
    assert updated.description == "docs"

    cleared = tags_core.update_tag(session, tag.id, description="   ")
    assert cleared.description is None

    with pytest.raises(NotFoundError):
        tags_core.update_tag(session, 999, name="x")


def test_update_tag_name_collision(session: Session) -> None:
    tags_core.create_tag(session, "taken")
    other = tags_core.create_tag(session, "other")
    with pytest.raises(ValueError, match="already exists"):
        tags_core.update_tag(session, other.id, name="TAKEN")
    # renaming to your own normalized name is fine
    assert tags_core.update_tag(session, other.id, name=" other ").name == "other"
    with pytest.raises(ValueError, match="empty"):
        tags_core.update_tag(session, other.id, name="   ")


def test_delete_tag_cascades(session: Session) -> None:
    task = tasks.create_task(session, "t", tags=["doomed", "safe"])
    doomed = next(t for t in task.tags if t.name == "doomed")
    tags_core.delete_tag(session, doomed.id)
    session.expire_all()
    assert [t.name for t in tasks.get_task(session, task.id).tags] == ["safe"]
    assert tags_core.list_tags(session)[0].name == "safe"
    with pytest.raises(NotFoundError):
        tags_core.delete_tag(session, 999)


def test_descendant_ids(session: Session) -> None:
    root = tasks.create_task(session, "root")
    child = tasks.create_task(session, "child", parent_id=root.id)
    grand = tasks.create_task(session, "grand", parent_id=child.id)
    tasks.create_task(session, "unrelated")

    assert tags_core.descendant_ids(session, root.id) == {child.id, grand.id}
    assert tags_core.descendant_ids(session, child.id) == {grand.id}
    assert tags_core.descendant_ids(session, grand.id) == set()
    with pytest.raises(NotFoundError):
        tags_core.descendant_ids(session, 999)


def test_add_tags_to_descendants_additive_and_idempotent(session: Session) -> None:
    root = tasks.create_task(session, "root", tags=["root-tag"])
    child = tasks.create_task(session, "child", parent_id=root.id, tags=["extra"])
    grand = tasks.create_task(session, "grand", parent_id=child.id)

    # inheritance on create already gave both descendants "root-tag" and "extra"
    changed = tags_core.add_tags_to_descendants(session, root.id, ["NEW", "extra"])
    assert changed == 2  # child and grand each gain "new"; "extra" was already there

    assert sorted(t.name for t in tasks.get_task(session, child.id).tags) == [
        "extra",
        "new",
        "root-tag",
    ]
    assert sorted(t.name for t in tasks.get_task(session, grand.id).tags) == [
        "extra",
        "new",
        "root-tag",
    ]
    # the root itself is not modified
    assert [t.name for t in tasks.get_task(session, root.id).tags] == ["root-tag"]

    # idempotent: a second run changes nothing
    assert tags_core.add_tags_to_descendants(session, root.id, ["new", "extra"]) == 0
    # no names or no descendants -> no-op
    assert tags_core.add_tags_to_descendants(session, root.id, []) == 0
    leaf = tasks.create_task(session, "leaf")
    assert tags_core.add_tags_to_descendants(session, leaf.id, ["x"]) == 0
    with pytest.raises(NotFoundError):
        tags_core.add_tags_to_descendants(session, 999, ["x"])
