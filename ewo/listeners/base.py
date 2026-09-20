"""Platform-agnostic command router shared by all listeners.

Listeners (Discord now, Slack later) only adapt transport: they hand incoming
text to ``CommandRouter.handle`` and send back the returned reply. All data
operations go through the HTTP /api like every other client.

Commands:
    !task <title> [@person] [#tag ...] [!critical|!high|!low]
    !done <task-id>
    !tasks [@person]
    !next
    !help
"""

from __future__ import annotations

import re

import httpx

_MENTION_RE = re.compile(r"@(\w[\w.-]*)")
_TAG_RE = re.compile(r"#(\w[\w-]*)")
_PRIORITY_RE = re.compile(r"!(critical|high|normal|low)\b")

HELP_TEXT = (
    "commands:\n"
    "  !task <title> [@person] [#tag] [!critical|!high|!low] — add a task\n"
    "  !done <task-id> — mark a task done\n"
    "  !tasks [@person] — list open tasks\n"
    "  !next — what to focus on\n"
    "  !help — this message"
)


class CommandRouter:
    """Parses chat commands and executes them against the /api."""

    def __init__(self, api_base_url: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=api_base_url, timeout=30)

    def _person_id(self, name: str) -> int | None:
        response = self._client.get("/api/people")
        response.raise_for_status()
        for person in response.json():
            if person["name"].lower() == name.lower():
                return int(person["id"])
        return None

    def _cmd_task(self, args: str) -> str:
        title = args
        assignee_id: int | None = None
        mention = _MENTION_RE.search(args)
        if mention:
            assignee_id = self._person_id(mention.group(1))
            if assignee_id is None:
                return f"unknown person: {mention.group(1)}"
            title = _MENTION_RE.sub("", title)
        tags = _TAG_RE.findall(args)
        title = _TAG_RE.sub("", title)
        priority_match = _PRIORITY_RE.search(args)
        priority = priority_match.group(1) if priority_match else "normal"
        title = _PRIORITY_RE.sub("", title).strip()
        if not title:
            return "usage: !task <title> [@person] [#tag] [!priority]"
        response = self._client.post(
            "/api/tasks",
            json={
                "title": title,
                "assignee_id": assignee_id,
                "tags": tags,
                "priority": priority,
                "source": "discord",
            },
        )
        response.raise_for_status()
        task = response.json()
        return f"created task #{task['id']}: {task['title']}"

    def _cmd_done(self, args: str) -> str:
        if not args.strip().isdigit():
            return "usage: !done <task-id>"
        task_id = int(args.strip())
        response = self._client.patch(f"/api/tasks/{task_id}", json={"status": "done"})
        if response.status_code == 404:
            return f"task {task_id} not found"
        response.raise_for_status()
        return f"task #{task_id} marked done"

    def _cmd_tasks(self, args: str) -> str:
        params: dict[str, str] = {}
        mention = _MENTION_RE.search(args)
        if mention:
            person_id = self._person_id(mention.group(1))
            if person_id is None:
                return f"unknown person: {mention.group(1)}"
            params["assignee_id"] = str(person_id)
        response = self._client.get("/api/tasks", params=params)
        response.raise_for_status()
        tasks = response.json()
        if not tasks:
            return "no open tasks"
        lines = [
            f"#{t['id']} [{t['status']}] {t['title']}"
            + (f" @{t['assignee']['name']}" if t.get("assignee") else "")
            for t in tasks[:20]
        ]
        return "\n".join(lines)

    def _cmd_next(self, args: str) -> str:
        response = self._client.get("/api/whatnext")
        response.raise_for_status()
        return str(response.json()["markdown"])

    def handle(self, text: str) -> str | None:
        """Handle one message; returns a reply or None if not a command."""
        stripped = text.strip()
        if not stripped.startswith("!"):
            return None
        command, _, args = stripped.partition(" ")
        handlers = {
            "!task": self._cmd_task,
            "!done": self._cmd_done,
            "!tasks": self._cmd_tasks,
            "!next": self._cmd_next,
        }
        if command == "!help":
            return HELP_TEXT
        handler = handlers.get(command)
        if handler is None:
            return None
        try:
            return handler(args)
        except httpx.HTTPError as exc:
            return f"error talking to ewo server: {exc}"
