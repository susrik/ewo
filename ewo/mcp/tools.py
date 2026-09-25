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

    def run_job(self, name: str, full: bool = False) -> dict[str, Any]:
        response = self._client.post(f"/api/jobs/{name}/run", params={"full": str(full).lower()})
        response.raise_for_status()
        return dict(response.json())

    def list_inbox(self) -> list[dict[str, Any]]:
        response = self._client.get("/api/nuggets")
        response.raise_for_status()
        return list(response.json())

    def attach_inbox_item(
        self, item_id: int, task_id: int | None = None, priority: str = "normal"
    ) -> dict[str, Any]:
        response = self._client.post(
            f"/api/nuggets/{item_id}/attach", json={"task_id": task_id, "priority": priority}
        )
        response.raise_for_status()
        return dict(response.json())

    def resolve_inbox_item(self, item_id: int, already_done: bool) -> dict[str, Any]:
        action = "done" if already_done else "dismiss"
        response = self._client.post(f"/api/nuggets/{item_id}/{action}")
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
    def ewo_run_job(name: str, full: bool = False) -> dict[str, Any]:
        """Run a named ewo job on demand (jira_sync, daily_report, what_next, notes_scan,
        nuggets_match).

        ``full=True`` makes notes_scan rescan the whole window instead of only changed notes.
        """
        return api.run_job(name, full=full)

    @server.tool()
    def ewo_inbox_list() -> list[dict[str, Any]]:
        """Unreviewed nuggets found in notes (path:line cited), awaiting attach/dismiss/done."""
        return api.list_inbox()

    @server.tool()
    def ewo_inbox_attach(
        item_id: int, task_id: int | None = None, priority: str = "normal"
    ) -> dict[str, Any]:
        """Attach a nugget to an existing task (task_id) or create a new task from it
        (priority: critical|high|normal|low, used only when creating)."""
        return api.attach_inbox_item(item_id, task_id=task_id, priority=priority)

    @server.tool()
    def ewo_inbox_resolve(item_id: int, already_done: bool = False) -> dict[str, Any]:
        """Dismiss a nugget, or mark it already finished (already_done=True)."""
        return api.resolve_inbox_item(item_id, already_done=already_done)
