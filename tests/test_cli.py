"""CLI commands: click runner + respx-mocked /api."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
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
    result = _invoke(
        ["person", "add", "Anna", "--alias", "Ann", "--notes-dir", "swd/people/anna", "--self"],
        config_file,
    )
    assert result.exit_code == 0  # type: ignore[attr-defined]
    payload = json.loads(respx.calls.last.request.content)
    assert payload["aliases"] == ["Ann"]
    assert payload["notes_dir"] == "swd/people/anna"
    assert payload["is_self"] is True
    assert "email" not in payload

    respx.get(f"{BASE}/api/people").mock(
        return_value=Response(
            200,
            json=[
                {"id": 1, "name": "Anna", "is_self": True, "notes_dir": "swd/people/anna"},
                {"id": 2, "name": "Bob", "is_self": False, "notes_dir": None},
            ],
        )
    )
    result = _invoke(["person", "list"], config_file)
    assert "#1 Anna (me) [swd/people/anna]" in result.output  # type: ignore[attr-defined]
    assert "#2 Bob\n" in result.output  # type: ignore[attr-defined]


@respx.mock
def test_person_seed(config_file: str) -> None:
    respx.post(f"{BASE}/api/people/seed-from-notes").mock(
        return_value=Response(
            200, json={"created": [{"id": 3, "name": "Neda", "notes_dir": "swd/people/neda"}]}
        )
    )
    result = _invoke(["person", "seed"], config_file)
    assert "created #3 Neda [swd/people/neda]" in result.output  # type: ignore[attr-defined]

    respx.post(f"{BASE}/api/people/seed-from-notes").mock(
        return_value=Response(200, json={"created": []})
    )
    result = _invoke(["person", "seed"], config_file)
    assert "nothing to add" in result.output  # type: ignore[attr-defined]


@respx.mock
def test_inbox_commands(config_file: str) -> None:
    respx.get(f"{BASE}/api/nuggets").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 7,
                    "kind": "action",
                    "summary": "chase Matti",
                    "owner": {"name": "Me"},
                    "owner_name": "Erik",
                    "due_date": "2026-10-01",
                    "jira_keys": ["PROJ-1"],
                    "suggested_task_id": None,
                    "path": "eurohpc/x.md",
                    "line": 34,
                },
                {
                    "id": 8,
                    "kind": "risk",
                    "summary": "budget",
                    "owner": None,
                    "owner_name": None,
                    "due_date": None,
                    "jira_keys": [],
                    "suggested_task_id": 12,
                    "path": "ai/y.md",
                    "line": 2,
                },
            ],
        )
    )
    result = _invoke(["inbox", "list", "--status", "all"], config_file)
    assert "#7 [action] chase Matti @Me due 2026-10-01 [PROJ-1]  (eurohpc/x.md:34)" in result.output  # type: ignore[attr-defined]
    assert "#8 [risk] budget @- → task #12  (ai/y.md:2)" in result.output  # type: ignore[attr-defined]

    attach = respx.post(f"{BASE}/api/nuggets/7/attach").mock(
        return_value=Response(201, json={"id": 12, "title": "chase Matti"})
    )
    result = _invoke(
        ["inbox", "attach", "7", "--priority", "high", "--due", "2026-10-01"], config_file
    )
    assert "task #12 created: chase Matti" in result.output  # type: ignore[attr-defined]
    sent = json.loads(attach.calls[0].request.content)
    assert sent["priority"] == "high" and sent["task_id"] is None

    result = _invoke(["inbox", "attach", "7", "--task-id", "12"], config_file)
    assert "task #12 attached to: chase Matti" in result.output  # type: ignore[attr-defined]
    assert json.loads(attach.calls[1].request.content)["task_id"] == 12

    respx.post(f"{BASE}/api/nuggets/8/dismiss").mock(return_value=Response(200, json={"id": 8}))
    assert "nugget #8 dismissed" in _invoke(["inbox", "dismiss", "8"], config_file).output  # type: ignore[attr-defined]

    respx.post(f"{BASE}/api/nuggets/8/done").mock(return_value=Response(200, json={"id": 8}))
    assert "already done" in _invoke(["inbox", "done", "8"], config_file).output  # type: ignore[attr-defined]


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

    run_route = respx.post(f"{BASE}/api/jobs/notes_scan/run").mock(
        return_value=Response(200, json={"id": 1, "job_name": "notes_scan", "status": "success"})
    )
    result = _invoke(["jobs", "run", "notes_scan", "--full"], config_file)
    assert "success" in result.output  # type: ignore[attr-defined]
    assert "full=true" in str(run_route.calls[0].request.url)
    assert "wait=true" in str(run_route.calls[0].request.url)

    respx.post(f"{BASE}/api/jobs/notes_scan/run").mock(
        return_value=Response(202, json={"id": 9, "job_name": "notes_scan", "status": "running"})
    )
    result = _invoke(["jobs", "run", "notes_scan", "--full", "--no-wait"], config_file)
    assert "started run #9 (running)" in result.output  # type: ignore[attr-defined]
    assert "wait=false" in str(run_route.calls[1].request.url)

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
                    "result": "files=3 new=2",
                }
            ],
        )
    )
    result = _invoke(["jobs", "runs"], config_file)
    assert "#1 jira_sync success" in result.output  # type: ignore[attr-defined]
    assert "files=3 new=2" in result.output  # type: ignore[attr-defined]


@respx.mock
def test_jobs_run_timeout_is_friendly(config_file: str) -> None:
    respx.post(f"{BASE}/api/jobs/notes_scan/run").mock(side_effect=httpx.ReadTimeout("timed out"))
    result = _invoke(["jobs", "run", "notes_scan"], config_file)
    assert result.exit_code == 0  # type: ignore[attr-defined]
    assert "still running server-side" in result.output  # type: ignore[attr-defined]
    assert "Traceback" not in result.output  # type: ignore[attr-defined]


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
