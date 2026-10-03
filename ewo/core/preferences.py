"""Key/value UI preferences — one JSON row per key (e.g. saved filter state)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ewo.db.models import UIPreference


def get_pref(session: Session, key: str) -> dict[str, object] | None:
    row = session.get(UIPreference, key)
    return dict(row.value) if row is not None else None


def set_pref(session: Session, key: str, value: dict[str, object]) -> None:
    row = session.get(UIPreference, key)
    if row is None:
        session.add(UIPreference(key=key, value=value))
    else:
        row.value = value
    session.commit()
