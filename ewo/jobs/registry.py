"""Job registry: every execution (scheduled or on-demand) records a JobRun.

Jobs are plain callables ``(JobContext) -> str | None`` registered by name.
On-demand execution: ``POST /api/jobs/{name}/run`` or ``ewo jobs run <name>``.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from ewo.config import Config
from ewo.core.llm import LLMClient
from ewo.db.models import JobRun, JobRunStatus


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
    ) -> JobRun:
        if name not in self.jobs:
            raise UnknownJobError(f"unknown job: {name}")
        with session_factory() as session:
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


registry = JobRegistry()
