"""MCP tool definitions — single source for stdio and HTTP transports.

Tools are thin wrappers over the HTTP /api, so MCP clients (e.g. opencode)
see exactly the same data as the CLI, GUI, and listeners.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from ewo.config import Config

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer


class EwoApi:
    """Small /api client used by the MCP tools."""

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=config.api_base_url, timeout=60)

    def list_tasks(self, assignee: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if assignee:
            person = self._find_person(assignee)
            if person is None:
                return []
            params["assignee_id"] = str(person["id"])
        response = self._client.get("/api/tasks", params=params)
        response.raise_for_status()
        return list(response.json())

    def _find_person(self, name: str) -> dict[str, Any] | None:
        response = self._client.get("/api/people")
        response.raise_for_status()
        for person in response.json():
            if person["name"].lower() == name.lower():
                return dict(person)
        return None

    def create_task(
        self,
        title: str,
        assignee: str | None = None,
        priority: str = "normal",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        assignee_id: int | None = None
        if assignee:
            person = self._find_person(assignee)
            if person is None:
                raise ValueError(f"unknown person: {assignee}")
            assignee_id = int(person["id"])
        response = self._client.post(
            "/api/tasks",
            json={
                "title": title,
                "assignee_id": assignee_id,
                "priority": priority,
                "tags": tags or [],
                "source": "mcp",
            },
        )
        response.raise_for_status()
        return dict(response.json())

    def update_task_status(self, task_id: int, status: str) -> dict[str, Any]:
        response = self._client.patch(f"/api/tasks/{task_id}", json={"status": status})
        response.raise_for_status()
        return dict(response.json())

    def what_next(self) -> str:
        response = self._client.get("/api/whatnext")
        response.raise_for_status()
        return str(response.json()["markdown"])

    def run_job(self, name: str) -> dict[str, Any]:
        response = self._client.post(f"/api/jobs/{name}/run")
        response.raise_for_status()
        return dict(response.json())


def register_tools(server: MCPServer[Any], api: EwoApi) -> None:
    """Register all ewo tools on an MCPServer instance."""

    @server.tool()
    def ewo_list_tasks(assignee: str | None = None) -> list[dict[str, Any]]:
        """List open tasks, optionally filtered by assignee name."""
        return api.list_tasks(assignee=assignee)

    @server.tool()
    def ewo_create_task(
        title: str,
        assignee: str | None = None,
        priority: str = "normal",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a task (priority: critical|high|normal|low)."""
        return api.create_task(title, assignee=assignee, priority=priority, tags=tags)

    @server.tool()
    def ewo_set_task_status(task_id: int, status: str) -> dict[str, Any]:
        """Set task status (open|in_progress|blocked|done|dropped)."""
        return api.update_task_status(task_id, status)

    @server.tool()
    def ewo_what_next() -> str:
        """What should be focused on right now (ranked task list, markdown)."""
        return api.what_next()

    @server.tool()
    def ewo_run_job(name: str) -> dict[str, Any]:
        """Run a named ewo job on demand (e.g. jira_sync, daily_report)."""
        return api.run_job(name)
