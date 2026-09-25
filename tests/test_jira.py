"""Jira integration: sync logic with a fake client, HTTP client via respx."""

from __future__ import annotations

import respx
from httpx import Response
from sqlalchemy.orm import Session

from ewo.config import JiraConfig
from ewo.core import people, tasks
from ewo.db.models import TaskSource, TaskStatus
from ewo.integrations.jira import HttpJiraClient, JiraIssue, sync_jira


class FakeJiraClient:
    def __init__(self, issues: list[JiraIssue]) -> None:
        self.issues = issues

    def search(self, jql: str) -> list[JiraIssue]:
        return self.issues


def _issue(key: str = "PROJ-1", **kwargs: object) -> JiraIssue:
    defaults: dict[str, object] = {
        "key": key,
        "summary": "do the thing",
        "status": "In Progress",
        "status_category": "indeterminate",
        "assignee_account_id": None,
        "url": f"https://j/browse/{key}",
    }
    defaults.update(kwargs)
    return JiraIssue(**defaults)  # type: ignore[arg-type]


def test_sync_creates_new_task(session: Session) -> None:
    counts = sync_jira(session, FakeJiraClient([_issue()]), "jql")
    assert counts == {"created": 1, "updated": 0}

    [task] = tasks.list_tasks(session)
    assert task.title == "[PROJ-1] do the thing"
    assert task.source == TaskSource.JIRA
    assert task.status == TaskStatus.IN_PROGRESS
    [link] = task.external_links
    assert link.external_key == "PROJ-1"
    assert link.external_status == "In Progress"


def test_sync_maps_assignee(session: Session) -> None:
    anna = people.create_person(session, "Anna", jira_account_id="acc-1")
    sync_jira(session, FakeJiraClient([_issue(assignee_account_id="acc-1")]), "jql")
    [task] = tasks.list_tasks(session)
    assert task.assignee_id == anna.id


def test_sync_updates_existing(session: Session) -> None:
    client = FakeJiraClient([_issue(status="In Progress", status_category="indeterminate")])
    sync_jira(session, client, "jql")

    client.issues = [_issue(status="Done", status_category="done")]
    counts = sync_jira(session, client, "jql")
    assert counts == {"created": 0, "updated": 1}

    [task] = tasks.list_tasks(session, include_closed=True)
    assert task.status == TaskStatus.DONE
    assert task.external_links[0].external_status == "Done"


def test_sync_updates_assignee_on_existing(session: Session) -> None:
    client = FakeJiraClient([_issue()])
    sync_jira(session, client, "jql")

    anna = people.create_person(session, "Anna", jira_account_id="acc-1")
    client.issues = [_issue(assignee_account_id="acc-1")]
    sync_jira(session, client, "jql")
    [task] = tasks.list_tasks(session)
    assert task.assignee_id == anna.id


def test_sync_noop_update_not_counted(session: Session) -> None:
    client = FakeJiraClient([_issue()])
    sync_jira(session, client, "jql")
    counts = sync_jira(session, client, "jql")
    assert counts == {"created": 0, "updated": 0}


def test_sync_updates_all_tasks_linked_to_one_issue(session: Session) -> None:
    """Several tasks may track the same Jira issue; sync updates them all."""
    client = FakeJiraClient([_issue()])
    sync_jira(session, client, "jql")
    second = tasks.create_task(session, "manual task on the same issue")
    tasks.link_external(session, second.id, "jira", "PROJ-1")

    client.issues = [_issue(status="Done", status_category="done")]
    counts = sync_jira(session, client, "jql")
    assert counts == {"created": 0, "updated": 2}

    all_tasks = tasks.list_tasks(session, include_closed=True)
    assert {t.status for t in all_tasks} == {TaskStatus.DONE}
    assert {t.external_links[0].external_status for t in all_tasks} == {"Done"}


def test_sync_reopens_done_task(session: Session) -> None:
    client = FakeJiraClient([_issue(status="Done", status_category="done")])
    sync_jira(session, client, "jql")

    client.issues = [_issue(status="To Do", status_category="new")]
    sync_jira(session, client, "jql")
    [task] = tasks.list_tasks(session)
    assert task.status == TaskStatus.OPEN


def test_sync_new_category_keeps_open_status(session: Session) -> None:
    client = FakeJiraClient([_issue(status="To Do", status_category="new")])
    sync_jira(session, client, "jql")
    [task] = tasks.list_tasks(session)
    assert task.status == TaskStatus.OPEN


@respx.mock
def test_http_jira_client_pagination() -> None:
    route = respx.get("https://jira.example/rest/api/3/search/jql").mock(
        side_effect=[
            Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "A-1",
                            "fields": {
                                "summary": "first",
                                "status": {
                                    "name": "Open",
                                    "statusCategory": {"key": "new"},
                                },
                                "assignee": {"accountId": "acc-9"},
                            },
                        }
                    ],
                    "nextPageToken": "page2",
                },
            ),
            Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "A-2",
                            "fields": {
                                "summary": "second",
                                "status": None,
                                "assignee": None,
                            },
                        }
                    ]
                },
            ),
        ]
    )
    client = HttpJiraClient(
        JiraConfig(enabled=True, base_url="https://jira.example/", email="e", api_token="t")
    )
    issues = client.search("project = A")
    assert len(route.calls) == 2
    assert [i.key for i in issues] == ["A-1", "A-2"]
    assert issues[0].assignee_account_id == "acc-9"
    assert issues[0].status_category == "new"
    assert issues[0].url == "https://jira.example/browse/A-1"
    assert issues[1].status == ""
