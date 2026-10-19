"""Persistent AI "new task" suggestions for the inbox.

The ``inbox_suggest`` job proposes new tasks that group clusters of unattached
inbox nuggets. Those proposals are stored as ``NuggetSuggestion`` rows so the
user can work through them over several actions rather than consuming them all
at once. A newer run replaces the whole set.

Nothing here mutates nuggets: rendering resolves each suggestion's ``nugget_ids``
against the currently ``NEW`` (unattached) nuggets, so nuggets already attached,
dismissed, or marked done elsewhere drop out of view and are never overwritten.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ewo.core.task_organize import Proposal
from ewo.db.models import Nugget, NuggetStatus, NuggetSuggestion


def replace_suggestions(session: Session, proposals: list[Proposal]) -> list[NuggetSuggestion]:
    """Delete every existing suggestion and store *proposals* afresh."""
    session.execute(delete(NuggetSuggestion))
    rows = [
        NuggetSuggestion(
            title=(p.title or "").strip(),
            description=(p.description or "").strip() or None,
            reason=p.reason or None,
            nugget_ids=list(dict.fromkeys(p.nugget_ids)),
            sort_order=index,
        )
        for index, p in enumerate(proposals)
    ]
    session.add_all(rows)
    session.flush()
    return rows


def list_suggestions(session: Session) -> list[NuggetSuggestion]:
    return list(session.scalars(select(NuggetSuggestion).order_by(NuggetSuggestion.sort_order)))


def clear_suggestions(session: Session) -> None:
    session.execute(delete(NuggetSuggestion))
    session.commit()


def delete_suggestion(session: Session, suggestion_id: int) -> None:
    suggestion = session.get(NuggetSuggestion, suggestion_id)
    if suggestion is not None:
        session.delete(suggestion)
        session.commit()


def remaining_nuggets(session: Session, suggestion: NuggetSuggestion) -> list[Nugget]:
    """The suggestion's nuggets still NEW and unattached, in proposed order."""
    by_id = {
        n.id: n
        for n in session.scalars(
            select(Nugget)
            .where(Nugget.status == NuggetStatus.NEW, Nugget.task_id.is_(None))
            .where(Nugget.id.in_(suggestion.nugget_ids or []))
        )
    }
    return [by_id[nid] for nid in suggestion.nugget_ids if nid in by_id]
