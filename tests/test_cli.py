"""CLI commands: click runner + respx-mocked /api."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx
from click.testing import CliRunner
from httpx import Response

from ewo.cli.main import cli

BASE = "http://testserver"


@pytest.fixture()
def config_file(tmp_path: Path) -> Iterator[str]:
    path = tmp_path / "ewo.json"
    path.write_text(json.dumps({"api_base_url": BASE, "database": {"url": "sqlite://"}}))
    yield str(path)


def _invoke(args: list[str], config_file: str) -> object:
    return CliRunner().invoke(cli, [*args, "--config", config_file])


@respx.mock
def test_task_add(config_file: str) -> None:
    route = respx.post(f"{BASE}/api/tasks").mock(
        return_value=Response(201, json={"id": 1, "title": "new task"})
    )
    result = _invoke(
        ["task", "add", "new task", "--priority", "high", "--tag", "x", "--due", "2026-09-01"],
        config_file,
    )
    assert result.exit_code == 0  # type: ignore[attr-defined]
    payload = json.loads(route.calls[0].request.content)
    assert payload["title"] == "new task"
    assert payload["priority"] == "high"
    assert payload["tags"] == ["x"]
    assert payload["due_date"] == "2026-09-01"


@respx.mock
def test_task_list(config_file: str) -> None:
    respx.get(f"{BASE}/api/tasks").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 1,
                    "status": "open",
                    "priority": "high",
                    "title": "t1",
                    "assignee": {"name": "Anna"},
                },
                {"id": 2, "status": "open", "priority": "low", "title": "t2", "assignee": None},
            ],
        )
    )
    result = _invoke(["task", "list", "--assignee-id", "1", "--all"], config_file)
    assert result.exit_code == 0  # type: ignore[attr-defined]
    assert "#1 [open] (high) t1 @Anna" in result.output  # type: ignore[attr-defined]
    assert "#2 [open] (low) t2" in result.output  # type: ignore[attr-defined]


@respx.mock
def test_task_done(config_file: str) -> None:
    respx.patch(f"{BASE}/api/tasks/5").mock(return_value=Response(200, json={"id": 5}))
    result = _invoke(["task", "done", "5"], config_file)
    assert result.exit_code == 0  # type: ignore[attr-defined]
    assert "task #5 done" in result.output  # type: ignore[attr-defined]


@respx.mock
def test_person_add_and_list(config_file: str) -> None:
    respx.post(f"{BASE}/api/people").mock(
        return_value=Response(201, json={"id": 1, "name": "Anna"})
    )
    result = _invoke(["person", "add", "Anna", "--email", "a@x.com"], config_file)
    assert result.exit_code == 0  # type: ignore[attr-defined]

    respx.get(f"{BASE}/api/people").mock(
        return_value=Response(200, json=[{"id": 1, "name": "Anna", "email": None}])
    )
    result = _invoke(["person", "list"], config_file)
    assert "#1 Anna" in result.output  # type: ignore[attr-defined]


@respx.mock
def test_oneonone_commands(config_file: str) -> None:
    respx.post(f"{BASE}/api/one-on-ones").mock(
        return_value=Response(201, json={"id": 3, "person_id": 1})
    )
    result = _invoke(["oneonone", "create", "1", "2026-09-02"], config_file)
    assert result.exit_code == 0  # type: ignore[attr-defined]

    respx.get(f"{BASE}/api/one-on-ones/3/agenda.md").mock(
        return_value=Response(200, json={"markdown": "# agenda md"})
    )
    result = _invoke(["oneonone", "agenda", "3"], config_file)
    assert "# agenda md" in result.output  # type: ignore[attr-defined]

    respx.post(f"{BASE}/api/one-on-ones/3/agenda").mock(
        return_value=Response(201, json={"id": 9, "topic": "t"})
    )
    result = _invoke(["oneonone", "topic", "3", "t"], config_file)
    assert result.exit_code == 0  # type: ignore[attr-defined]


@respx.mock
def test_whatnext(config_file: str) -> None:
    route = respx.get(f"{BASE}/api/whatnext").mock(
        return_value=Response(200, json={"markdown": "# What next"})
    )
    result = _invoke(["whatnext", "--llm"], config_file)
    assert "# What next" in result.output  # type: ignore[attr-defined]
    assert "use_llm=true" in str(route.calls[0].request.url)


@respx.mock
def test_jobs_commands(config_file: str) -> None:
    respx.get(f"{BASE}/api/jobs").mock(
        return_value=Response(200, json={"jobs": ["daily_report", "jira_sync"]})
    )
    result = _invoke(["jobs", "list"], config_file)
    assert "daily_report" in result.output  # type: ignore[attr-defined]

    respx.post(f"{BASE}/api/jobs/jira_sync/run").mock(
        return_value=Response(200, json={"id": 1, "job_name": "jira_sync", "status": "success"})
    )
    result = _invoke(["jobs", "run", "jira_sync"], config_file)
    assert "success" in result.output  # type: ignore[attr-defined]

    respx.get(f"{BASE}/api/jobs/runs").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 1,
                    "job_name": "jira_sync",
                    "status": "success",
                    "started_at": "2026-08-30T07:00:00",
                    "tokens_used": 0,
                }
            ],
        )
    )
    result = _invoke(["jobs", "runs"], config_file)
    assert "#1 jira_sync success" in result.output  # type: ignore[attr-defined]


def test_google_auth(config_file: str, monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[dict[str, object]] = []
    monkeypatch.setattr(
        "ewo.integrations.google.run_consent_flow",
        lambda cfg: {"refresh_token": "r", "access_token": "a"},
    )
    monkeypatch.setattr(
        "ewo.integrations.google.save_token", lambda session, token: saved.append(token)
    )
    result = _invoke(["google", "auth"], config_file)
    assert result.exit_code == 0  # type: ignore[attr-defined]
    assert "google token stored" in result.output  # type: ignore[attr-defined]
    assert saved == [{"refresh_token": "r", "access_token": "a"}]
