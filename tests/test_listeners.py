"""Command router (platform-agnostic listener core), backed by the live test app."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from ewo.listeners.base import HELP_TEXT, CommandRouter


@pytest.fixture()
def router(client: TestClient) -> CommandRouter:
    # TestClient is an httpx.Client — the router talks to the real app.
    return CommandRouter("http://testserver", client=client)


def test_non_command_ignored(router: CommandRouter) -> None:
    assert router.handle("just chatting") is None
    assert router.handle("!unknowncmd stuff") is None


def test_help(router: CommandRouter) -> None:
    assert router.handle("!help") == HELP_TEXT


def test_task_create_simple(router: CommandRouter, client: TestClient) -> None:
    reply = router.handle("!task fix the exporter")
    assert reply is not None and "created task #" in reply
    [task] = client.get("/api/tasks").json()
    assert task["title"] == "fix the exporter"
    assert task["source"] == "discord"


def test_task_create_full(router: CommandRouter, client: TestClient) -> None:
    client.post("/api/people", json={"name": "Anna"})
    reply = router.handle("!task fix the exporter @Anna #infra !high")
    assert reply is not None and "created task" in reply
    [task] = client.get("/api/tasks").json()
    assert task["title"] == "fix the exporter"
    assert task["priority"] == "high"
    assert task["assignee"]["name"] == "Anna"
    assert task["tags"][0]["name"] == "infra"


def test_task_unknown_person(router: CommandRouter) -> None:
    assert router.handle("!task do thing @Nobody") == "unknown person: Nobody"


def test_task_empty_title(router: CommandRouter) -> None:
    reply = router.handle("!task !high")
    assert reply is not None and reply.startswith("usage:")


def test_done(router: CommandRouter, client: TestClient) -> None:
    task = client.post("/api/tasks", json={"title": "t"}).json()
    reply = router.handle(f"!done {task['id']}")
    assert reply == f"task #{task['id']} marked done"
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "done"


def test_done_bad_args(router: CommandRouter) -> None:
    assert router.handle("!done notanumber") == "usage: !done <task-id>"
    assert router.handle("!done 999") == "task 999 not found"


def test_tasks_list(router: CommandRouter, client: TestClient) -> None:
    assert router.handle("!tasks") == "no open tasks"

    client.post("/api/people", json={"name": "Anna"})
    router.handle("!task work item @Anna")
    reply = router.handle("!tasks @Anna")
    assert reply is not None and "work item" in reply and "@Anna" in reply

    assert router.handle("!tasks @Ghost") == "unknown person: Ghost"


def test_next(router: CommandRouter) -> None:
    router.handle("!task important thing !critical")
    reply = router.handle("!next")
    assert reply is not None and "important thing" in reply


def test_http_error_reported() -> None:
    class ExplodingClient(httpx.Client):
        def get(self, *args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("no server")

    router = CommandRouter("http://x", client=ExplodingClient())
    reply = router.handle("!tasks")
    assert reply is not None and "error talking to ewo server" in reply
