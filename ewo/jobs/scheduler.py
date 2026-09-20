"""APScheduler wiring: in-process schedules from config (fixes cron timeouts).

Each schedule entry in ``config.jobs.schedules`` maps a registered job name to
a 5-field cron expression. Every run — scheduled or on-demand — goes through
the registry and records a JobRun row.
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

from ewo.config import Config
from ewo.core.llm import LLMClient
from ewo.jobs.registry import JobRegistry


def build_scheduler(
    config: Config,
    job_registry: JobRegistry,
    session_factory: sessionmaker[Session],
    llm: LLMClient,
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    for job_name, cron in config.jobs.schedules.items():
        if not cron or job_name not in job_registry.jobs:
            continue
        scheduler.add_job(
            job_registry.run,
            CronTrigger.from_crontab(cron),
            args=[job_name, session_factory, config, llm],
            id=job_name,
            max_instances=1,
            coalesce=True,
        )
    return scheduler
