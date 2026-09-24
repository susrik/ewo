"""Built-in jobs. Schedules come from ``config.jobs.schedules`` (cron syntax).

- jira_sync:    pull configured JQL, create/update linked tasks
- daily_report: generate daily markdown, publish to reports repo, email it
- what_next:    generate a what-next report and publish it
- notes_scan:   extract outstanding items from notes changed since the last
                successful scan into the inbox (``full=true`` rescans the window)
"""

from __future__ import annotations

from datetime import UTC, datetime

from ewo.core import reports
from ewo.core.llm import LLMClient
from ewo.core.note_extract import last_successful_scan, scan_notes
from ewo.core.report_publisher import publish_report
from ewo.core.whatnext import what_next_markdown
from ewo.integrations.google import HttpGoogleClient
from ewo.integrations.jira import HttpJiraClient, sync_jira
from ewo.jobs.registry import JobContext, registry


def _llm_or_none(context: JobContext) -> LLMClient | None:
    """Skip LLM narrative when no API key is configured (reports still work)."""
    return context.llm if context.config.llm.api_key else None


def _publish_and_record(context: JobContext, report_type: str, body: str) -> None:
    repo_path: str | None = None
    if context.config.reports_repo.enabled:
        repo_path = publish_report(
            context.config, report_type, body, datetime.now(UTC).replace(tzinfo=None)
        )
    reports.record_report(
        context.session, report_type, body, repo_path=repo_path, job_run_id=context.job_run.id
    )


@registry.register("jira_sync")
def jira_sync(context: JobContext) -> str:
    config = context.config.jira
    if not config.enabled:
        return "jira disabled"
    counts = sync_jira(context.session, HttpJiraClient(config), config.jql)
    return f"created={counts['created']} updated={counts['updated']}"


@registry.register("daily_report")
def daily_report(context: JobContext) -> str:
    body = reports.daily_report_markdown(context.session, llm=_llm_or_none(context))
    _publish_and_record(context, "daily", body)
    if context.config.google.enabled and context.config.google.email_to:
        client = HttpGoogleClient(context.session, context.config.google)
        client.send_email(
            context.config.google.email_to,
            f"ewo daily report {datetime.now(UTC).strftime('%Y-%m-%d')}",
            body,
        )
    return "ok"


@registry.register("what_next")
def what_next(context: JobContext) -> str:
    body = what_next_markdown(context.session, llm=_llm_or_none(context))
    _publish_and_record(context, "what-next", body)
    return "ok"


@registry.register("notes_scan")
def notes_scan(context: JobContext) -> str:
    notes = context.config.notes
    if not notes.enabled:
        return "notes disabled"
    if not context.config.llm.api_key:
        raise RuntimeError("notes_scan needs llm.api_key")
    since = None if context.flag("full") else last_successful_scan(context.session)
    summary = scan_notes(
        context.session,
        context.llm,
        notes.model_copy(update={"root": context.config.notes_root}),
        since=since,
        job_run_id=context.job_run.id,
    )
    context.add_tokens(summary.tokens)
    if summary.errors and summary.files == len(summary.errors):
        raise RuntimeError("; ".join(summary.errors))
    return summary.as_text()
