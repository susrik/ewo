"""Job registry: every execution (scheduled or on-demand) records a JobRun.

Jobs are plain callables ``(JobContext) -> str | None`` registered by name.
On-demand execution: ``POST /api/jobs/{name}/run`` or ``ewo jobs run <name>``
— synchronously by default, in a background daemon thread with
``wait=false`` / ``--no-wait`` for long jobs.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ewo.config import Config
from ewo.core.llm import LLMClient
from ewo.db.models import JobRun, JobRunStatus, utcnow


@dataclass
class JobContext:
    session: Session
    config: Config
    llm: LLMClient
    job_run: JobRun
    params: dict[str, str] = field(default_factory=dict)
    """Optional per-run parameters (e.g. ``{"full": "true"}`` from the API)."""

    def add_tokens(self, count: int) -> None:
        self.job_run.tokens_used += count

    def flag(self, name: str) -> bool:
        return self.params.get(name, "").lower() in {"1", "true", "yes"}


JobFunc = Callable[[JobContext], str | None]


class UnknownJobError(Exception):
    pass


@dataclass
class JobRegistry:
    jobs: dict[str, JobFunc] = field(default_factory=dict)

    def register(self, name: str) -> Callable[[JobFunc], JobFunc]:
        def decorator(func: JobFunc) -> JobFunc:
            self.jobs[name] = func
            return func

        return decorator

    def names(self) -> list[str]:
        return sorted(self.jobs)

    def run(
        self,
        name: str,
        session_factory: sessionmaker[Session],
        config: Config,
        llm: LLMClient,
        params: dict[str, str] | None = None,
        job_run_id: int | None = None,
    ) -> JobRun:
        if name not in self.jobs:
            raise UnknownJobError(f"unknown job: {name}")
        with session_factory() as session:
            job_run = session.get(JobRun, job_run_id) if job_run_id is not None else None
            if job_run is None:
                job_run = JobRun(job_name=name)
                session.add(job_run)
                session.commit()
            context = JobContext(
                session=session, config=config, llm=llm, job_run=job_run, params=params or {}
            )
            try:
                job_run.result = self.jobs[name](context)
                job_run.status = JobRunStatus.SUCCESS
            except Exception:
                job_run.status = JobRunStatus.FAILED
                job_run.error = traceback.format_exc()
            job_run.finished_at = datetime.now(UTC).replace(tzinfo=None)
            session.commit()
            return job_run

    def run_async(
        self,
        name: str,
        session_factory: sessionmaker[Session],
        config: Config,
        llm: LLMClient,
        params: dict[str, str] | None = None,
    ) -> int:
        """Create the JobRun and execute in a daemon thread; returns the run id."""
        if name not in self.jobs:
            raise UnknownJobError(f"unknown job: {name}")
        with session_factory() as session:
            job_run = JobRun(job_name=name)
            session.add(job_run)
            session.commit()
            run_id = job_run.id
        threading.Thread(
            target=self.run,
            args=(name, session_factory, config, llm),
            kwargs={"params": params, "job_run_id": run_id},
            daemon=True,
        ).start()
        return run_id


registry = JobRegistry()


def mark_interrupted_runs(session: Session) -> int:
    """Fail JobRuns left 'running' by a dead process (they never update again).

    Called once at server startup; returns the number of runs marked.
    """
    stale = list(session.scalars(select(JobRun).where(JobRun.status == JobRunStatus.RUNNING)))
    for run in stale:
        run.status = JobRunStatus.FAILED
        run.error = "interrupted: server restarted"
        run.finished_at = utcnow()
    session.commit()
    return len(stale)
