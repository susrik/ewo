"""UI preference key/value store."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ewo.core import preferences


def test_get_set_pref(session: Session) -> None:
    assert preferences.get_pref(session, "tasks_filter") is None

    preferences.set_pref(session, "tasks_filter", {"status": "done"})
    assert preferences.get_pref(session, "tasks_filter") == {"status": "done"}

    # overwriting updates the same row rather than adding a second one
    preferences.set_pref(session, "tasks_filter", {"status": "open"})
    assert preferences.get_pref(session, "tasks_filter") == {"status": "open"}
