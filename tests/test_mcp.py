"""MCP tools: registered once, exercised against the live test app."""

from __future__ import annotations

import anyio
import pytest
from fastapi.testclient import TestClient

from ewo.config import Config
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
    }


def test_mcp_tool_calls_end_to_end(config: Config, client: TestClient) -> None:
    server = build_server(config, api=EwoApi(config, client=client))

    async def calls() -> None:
        await server.call_tool("ewo_create_task", {"title": "via mcp"})
        await server.call_tool("ewo_list_tasks", {})
        await server.call_tool("ewo_what_next", {})
        await server.call_tool("ewo_run_job", {"name": "what_next"})

    anyio.run(calls)
    [task] = client.get("/api/tasks").json()
    assert task["title"] == "via mcp"

    async def set_status() -> None:
        await server.call_tool("ewo_set_task_status", {"task_id": task["id"], "status": "done"})

    anyio.run(set_status)
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "done"
