"""MCP tools: registered once, exercised against the live test app."""

from __future__ import annotations

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ewo.config import Config
from ewo.core import nuggets
from ewo.db.models import NuggetKind
from ewo.mcp.server import build_server
from ewo.mcp.tools import EwoApi


@pytest.fixture()
def api(config: Config, client: TestClient) -> EwoApi:
    return EwoApi(config, client=client)


def test_create_and_list_tasks(api: EwoApi, client: TestClient) -> None:
    client.post("/api/people", json={"name": "Anna"})
    task = api.create_task("mcp task", assignee="anna", priority="high", tags=["x"])
    assert task["title"] == "mcp task"
    assert task["source"] == "mcp"
    assert task["assignee"]["name"] == "Anna"

    tasks = api.list_tasks(assignee="Anna")
    assert [t["id"] for t in tasks] == [task["id"]]
    assert api.list_tasks(assignee="ghost") == []
    assert len(api.list_tasks()) == 1


def test_create_task_unknown_person(api: EwoApi) -> None:
    with pytest.raises(ValueError, match="unknown person"):
        api.create_task("x", assignee="Nobody")


def test_update_status_and_whatnext(api: EwoApi) -> None:
    task = api.create_task("finish me")
    updated = api.update_task_status(int(task["id"]), "done")
    assert updated["status"] == "done"

    markdown = api.what_next()
    assert markdown.startswith("# What next")


def test_run_job(api: EwoApi) -> None:
    run = api.run_job("what_next")
    assert run["status"] == "success"
    scan = api.run_job("notes_scan", full=True)
    assert scan["result"] == "notes disabled"


def test_inbox_tools(api: EwoApi, session: Session) -> None:
    a, _ = nuggets.upsert_nugget(session, "x.md", 1, "Attach me", NuggetKind.ACTION)
    b, _ = nuggets.upsert_nugget(session, "x.md", 2, "Dismiss me", NuggetKind.ACTION)
    c, _ = nuggets.upsert_nugget(session, "x.md", 3, "Done already", NuggetKind.ACTION)
    session.commit()
    assert [i["summary"] for i in api.list_inbox()] == ["Attach me", "Dismiss me", "Done already"]

    task = api.attach_inbox_item(a.id, priority="high")
    assert task["title"] == "Attach me" and task["priority"] == "high"
    assert api.resolve_inbox_item(b.id, already_done=False)["status"] == "dismissed"
    assert api.resolve_inbox_item(c.id, already_done=True)["status"] == "already_done"
    assert api.list_inbox() == []


def test_inbox_attach_to_existing_task(api: EwoApi, session: Session) -> None:
    existing = api.create_task("existing topic")
    a, _ = nuggets.upsert_nugget(session, "x.md", 1, "more detail", NuggetKind.ACTION)
    session.commit()
    task = api.attach_inbox_item(a.id, task_id=int(existing["id"]))
    assert task["id"] == existing["id"] and task["title"] == "existing topic"


def test_build_server_registers_tools(config: Config, client: TestClient) -> None:
    server = build_server(config, api=EwoApi(config, client=client))
    tools = anyio.run(server.list_tools)
    names = {tool.name for tool in tools}
    assert names == {
        "ewo_list_tasks",
        "ewo_create_task",
        "ewo_set_task_status",
        "ewo_what_next",
        "ewo_run_job",
        "ewo_inbox_list",
        "ewo_inbox_attach",
        "ewo_inbox_resolve",
    }


def test_mcp_tool_calls_end_to_end(config: Config, client: TestClient) -> None:
    server = build_server(config, api=EwoApi(config, client=client))

    async def calls() -> None:
        await server.call_tool("ewo_create_task", {"title": "via mcp"})
        await server.call_tool("ewo_list_tasks", {})
        await server.call_tool("ewo_what_next", {})
        await server.call_tool("ewo_run_job", {"name": "what_next"})
        await server.call_tool("ewo_inbox_list", {})

    anyio.run(calls)
    [task] = client.get("/api/tasks").json()
    assert task["title"] == "via mcp"

    async def set_status() -> None:
        await server.call_tool("ewo_set_task_status", {"task_id": task["id"], "status": "done"})

    anyio.run(set_status)
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "done"


def test_mcp_inbox_tools_end_to_end(config: Config, client: TestClient, session: Session) -> None:
    server = build_server(config, api=EwoApi(config, client=client))
    a, _ = nuggets.upsert_nugget(session, "x.md", 1, "Attach via mcp", NuggetKind.ACTION)
    b, _ = nuggets.upsert_nugget(session, "x.md", 2, "Drop via mcp", NuggetKind.ACTION)
    session.commit()

    async def calls() -> None:
        await server.call_tool("ewo_inbox_attach", {"item_id": a.id, "priority": "low"})
        await server.call_tool("ewo_inbox_resolve", {"item_id": b.id, "already_done": True})

    anyio.run(calls)
    [task] = client.get("/api/tasks").json()
    assert task["title"] == "Attach via mcp" and task["priority"] == "low"
    assert client.get("/api/nuggets", params={"status": "already_done"}).json()[0]["id"] == b.id
