"""Read-only Jira client + sync into tracked tasks.

Issues matching the configured JQL are pulled and reflected onto tasks via
``external_links``; existing linked tasks get status/assignee updates for
free (fixes the "list lags reality" problem).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.config import JiraConfig
from ewo.core.tasks import create_task, find_link
from ewo.db.models import ExternalLink, Person, TaskSource, TaskStatus

_DONE_STATUS_CATEGORY = "done"


@dataclass
class JiraIssue:
    key: str
    summary: str
    status: str
    status_category: str  # "new" | "indeterminate" | "done"
    assignee_account_id: str | None
    url: str


class JiraClient(Protocol):
    def search(self, jql: str) -> list[JiraIssue]: ...


class HttpJiraClient:
    """Jira Cloud REST v3, read-only."""

    def __init__(self, config: JiraConfig) -> None:
        self._config = config

    def search(self, jql: str) -> list[JiraIssue]:
        issues: list[JiraIssue] = []
        base = self._config.base_url.rstrip("/")
        auth = (self._config.email, self._config.api_token)
        next_page_token: str | None = None
        with httpx.Client(timeout=30) as client:
            while True:
                params: dict[str, str] = {
                    "jql": jql,
                    "fields": "summary,status,assignee",
                    "maxResults": "100",
                }
                if next_page_token:
                    params["nextPageToken"] = next_page_token
                response = client.get(f"{base}/rest/api/3/search/jql", params=params, auth=auth)
                response.raise_for_status()
                payload = response.json()
                for raw in payload.get("issues", []):
                    fields = raw["fields"]
                    assignee = fields.get("assignee") or {}
                    status = fields.get("status") or {}
                    issues.append(
                        JiraIssue(
                            key=raw["key"],
                            summary=fields.get("summary", ""),
                            status=status.get("name", ""),
                            status_category=(status.get("statusCategory") or {})
                            .get("key", "")
                            .lower(),
                            assignee_account_id=assignee.get("accountId"),
                            url=f"{base}/browse/{raw['key']}",
                        )
                    )
                next_page_token = payload.get("nextPageToken")
                if not next_page_token:
                    return issues


def _map_status(category: str, current: TaskStatus) -> TaskStatus:
    if category == _DONE_STATUS_CATEGORY:
        return TaskStatus.DONE
    if category == "indeterminate":
        return TaskStatus.IN_PROGRESS
    if current in (TaskStatus.DONE, TaskStatus.DROPPED):
        return TaskStatus.OPEN  # reopened in jira
    return current


def sync_jira(session: Session, client: JiraClient, jql: str) -> dict[str, int]:
    """Sync Jira issues into tasks. Returns counters for the job log."""
    created = 0
    updated = 0
    people = list(session.scalars(select(Person).where(Person.jira_account_id.is_not(None))))
    by_account = {p.jira_account_id: p for p in people}

    for issue in client.search(jql):
        link = find_link(session, "jira", issue.key)
        now = datetime.now(UTC).replace(tzinfo=None)
        if link is None:
            task = create_task(
                session,
                title=f"[{issue.key}] {issue.summary}",
                source=TaskSource.JIRA,
                assignee_id=(
                    by_account[issue.assignee_account_id].id
                    if issue.assignee_account_id in by_account
                    else None
                ),
            )
            task.status = _map_status(issue.status_category, task.status)
            task.external_links.append(
                ExternalLink(
                    system="jira",
                    external_key=issue.key,
                    url=issue.url,
                    external_status=issue.status,
                    last_synced_at=now,
                )
            )
            created += 1
        else:
            task = link.task
            new_status = _map_status(issue.status_category, task.status)
            changed = link.external_status != issue.status or task.status != new_status
            link.external_status = issue.status
            link.last_synced_at = now
            task.status = new_status
            if issue.assignee_account_id in by_account:
                task.assignee_id = by_account[issue.assignee_account_id].id
            if changed:
                updated += 1
    session.commit()
    return {"created": created, "updated": updated}
