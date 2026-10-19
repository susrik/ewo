"""Per-run job logging: append ``JobLogEntry`` rows and broadcast them live.

``JobLog`` is the only writer for a run's log. Each ``emit`` inserts a row and
commits immediately (the DB is the source of truth for retention, clearing, and
replay), then hands the entry to ``on_entry`` so the webserver can broadcast it
to open SSE clients for sub-second live updates.

The LLM conveniences capture exactly the inference detail the GUI surfaces:
which model (regular vs smart tier, and its concrete name), how much was
submitted (prompt/system char counts plus a truncated preview), a "waiting for
feedback" line before the call, and tokens + elapsed time after it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from ewo.db.models import JobLogEntry, JobLogKind

# (event_id, payload) — the webserver wires this to the live broadcast bus.
EntryCallback = Callable[[int, dict[str, Any]], None]


class JobLog:
    def __init__(
        self,
        session: Session,
        run_id: int,
        model: str,
        smart_model: str,
        preview_chars: int = 600,
        on_entry: EntryCallback | None = None,
    ) -> None:
        self._session = session
        self.run_id = run_id
        self._model = model
        self._smart_model = smart_model
        self._preview_chars = preview_chars
        self._on_entry = on_entry

    # --- primitives ---

    def info(self, message: str, **meta: object) -> None:
        self._emit(JobLogKind.INFO, message, **meta)

    def step(self, message: str, **meta: object) -> None:
        self._emit(JobLogKind.STEP, message, **meta)

    def error(self, message: str, **meta: object) -> None:
        self._emit(JobLogKind.ERROR, message, **meta)

    def llm_call(
        self,
        smart: bool,
        prompt: str,
        system: str | None = None,
        retry: bool = False,
        **meta: object,
    ) -> None:
        """Record the request about to go to the model (the "waiting" state)."""
        model = self._resolve(smart)
        payload = dict(meta)
        payload.update(
            model=model,
            tier="smart" if smart else "regular",
            prompt_chars=len(prompt),
            system_chars=len(system or ""),
            retry=retry,
            waiting=True,
            preview=prompt[: self._preview_chars],
        )
        message = f"waiting for {model}{' (retry)' if retry else ''} — prompt {len(prompt)} chars"
        self._emit(JobLogKind.WAIT, message, **payload)

    def llm_reply(
        self, smart: bool, tokens: int, elapsed_ms: float | None = None, **meta: object
    ) -> None:
        """Record the model's reply (tokens + elapsed), closing the wait line."""
        model = self._resolve(smart)
        payload = dict(meta)
        payload.update(model=model, tier="smart" if smart else "regular", tokens=tokens)
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
        message = f"← {model}: {tokens} tokens"
        if elapsed_ms is not None:
            message += f" in {elapsed_ms / 1000:.1f}s"
        self._emit(JobLogKind.LLM, message, **payload)

    # --- internals ---

    def _resolve(self, smart: bool) -> str:
        return self._smart_model if smart else self._model

    def _emit(self, kind: JobLogKind, message: str, **meta: object) -> None:
        entry = JobLogEntry(run_id=self.run_id, kind=kind.value, message=message, meta=dict(meta))
        self._session.add(entry)
        self._session.commit()
        if self._on_entry is not None:
            self._on_entry(entry.id, self._payload(entry))

    @staticmethod
    def _payload(entry: JobLogEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "kind": entry.kind,
            "message": entry.message,
            "meta": entry.meta or {},
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }
