"""ewo CLI — click commands over the HTTP /api.

Every command takes ``--config`` (validated via the shared pydantic callback);
the server address comes from ``api_base_url`` in the config.
"""

from __future__ import annotations

import json
from typing import Any

import click
import httpx

from ewo.config import Config, config_option


def _client(config: Config, timeout: float = 60) -> httpx.Client:
    return httpx.Client(base_url=config.api_base_url, timeout=timeout)


def _echo_json(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, default=str))


@click.group()
def cli() -> None:
    """ewo — task/project management."""


# --- tasks ---


@cli.group()
def task() -> None:
    """Manage tasks."""


@task.command("add")
@click.argument("title")
@click.option("--assignee-id", type=int, default=None)
@click.option("--parent-id", type=int, default=None, help="Make it a sub-task of this task.")
@click.option("--priority", default="normal")
@click.option("--tag", "tags", multiple=True)
@click.option("--start", default=None, help="Start date YYYY-MM-DD")
@click.option("--due", default=None, help="Due date YYYY-MM-DD")
@config_option
def task_add(
    title: str,
    assignee_id: int | None,
    parent_id: int | None,
    priority: str,
    tags: tuple[str, ...],
    start: str | None,
    due: str | None,
    config: Config,
) -> None:
    """Add a task."""
    with _client(config) as client:
        response = client.post(
            "/api/tasks",
            json={
                "title": title,
                "assignee_id": assignee_id,
                "parent_id": parent_id,
                "priority": priority,
                "tags": list(tags),
                "start_date": start,
                "due_date": due,
            },
        )
        response.raise_for_status()
        _echo_json(response.json())


@task.command("list")
@click.option("--assignee-id", type=int, default=None)
@click.option("--all", "include_closed", is_flag=True, default=False)
@config_option
def task_list(assignee_id: int | None, include_closed: bool, config: Config) -> None:
    """List tasks."""
    params: dict[str, str] = {"include_closed": str(include_closed).lower()}
    if assignee_id is not None:
        params["assignee_id"] = str(assignee_id)
    with _client(config) as client:
        response = client.get("/api/tasks", params=params)
        response.raise_for_status()
        for t in response.json():
            assignee = f" @{t['assignee']['name']}" if t.get("assignee") else ""
            click.echo(f"#{t['id']} [{t['status']}] ({t['priority']}) {t['title']}{assignee}")


@task.command("done")
@click.argument("task_id", type=int)
@config_option
def task_done(task_id: int, config: Config) -> None:
    """Mark a task done."""
    with _client(config) as client:
        response = client.patch(f"/api/tasks/{task_id}", json={"status": "done"})
        response.raise_for_status()
        click.echo(f"task #{task_id} done")


# --- people ---


@cli.group()
def person() -> None:
    """Manage people."""


@person.command("add")
@click.argument("name")
@click.option("--jira-account-id", default=None)
@click.option("--discord-user-id", default=None)
@click.option("--notes-dir", default=None, help="Folder in the notes tree, e.g. swd/people/sam")
@click.option("--alias", "aliases", multiple=True, help="Other names used in notes.")
@click.option("--self", "is_self", is_flag=True, default=False, help="This person is me.")
@config_option
def person_add(
    name: str,
    jira_account_id: str | None,
    discord_user_id: str | None,
    notes_dir: str | None,
    aliases: tuple[str, ...],
    is_self: bool,
    config: Config,
) -> None:
    """Add a team member."""
    with _client(config) as client:
        response = client.post(
            "/api/people",
            json={
                "name": name,
                "jira_account_id": jira_account_id,
                "discord_user_id": discord_user_id,
                "notes_dir": notes_dir,
                "aliases": list(aliases),
                "is_self": is_self,
            },
        )
        response.raise_for_status()
        _echo_json(response.json())


@person.command("list")
@config_option
def person_list(config: Config) -> None:
    """List people."""
    with _client(config) as client:
        response = client.get("/api/people")
        response.raise_for_status()
        for p in response.json():
            marker = " (me)" if p["is_self"] else ""
            notes_dir = f" [{p['notes_dir']}]" if p.get("notes_dir") else ""
            click.echo(f"#{p['id']} {p['name']}{marker}{notes_dir}")


@person.command("seed")
@config_option
def person_seed(config: Config) -> None:
    """Create people from the team folders in the notes tree."""
    with _client(config) as client:
        response = client.post("/api/people/seed-from-notes")
        response.raise_for_status()
        created = response.json()["created"]
        for p in created:
            click.echo(f"created #{p['id']} {p['name']} [{p['notes_dir']}]")
        if not created:
            click.echo("nothing to add")


# --- inbox ---


@cli.group()
def inbox() -> None:
    """Nuggets (work items discovered in notes), awaiting review."""


@inbox.command("list")
@click.option("--status", default="new", help="new|attached|dismissed|already_done|all")
@config_option
def inbox_list(status: str, config: Config) -> None:
    """List inbox nuggets."""
    params = {} if status == "all" else {"status": status}
    with _client(config) as client:
        response = client.get("/api/nuggets", params=params)
        response.raise_for_status()
        for item in response.json():
            owner = item["owner"]["name"] if item.get("owner") else (item.get("owner_name") or "-")
            due = f" due {item['due_date']}" if item.get("due_date") else ""
            suggested = (
                f" → task #{item['suggested_task_id']}" if item.get("suggested_task_id") else ""
            )
            jira = f" [{', '.join(item['jira_keys'])}]" if item.get("jira_keys") else ""
            click.echo(
                f"#{item['id']} [{item['kind']}] {item['summary']} @{owner}{due}{jira}"
                f"{suggested}  ({item['path']}:{item['line']})"
            )


@inbox.command("attach")
@click.argument("item_id", type=int)
@click.option("--task-id", type=int, default=None, help="Attach to this existing task.")
@click.option("--priority", default="normal")
@click.option("--assignee-id", type=int, default=None)
@click.option("--due", default=None, help="Due date YYYY-MM-DD")
@config_option
def inbox_attach(
    item_id: int,
    task_id: int | None,
    priority: str,
    assignee_id: int | None,
    due: str | None,
    config: Config,
) -> None:
    """Attach a nugget to a task (existing via --task-id, or a new one)."""
    with _client(config) as client:
        response = client.post(
            f"/api/nuggets/{item_id}/attach",
            json={
                "task_id": task_id,
                "priority": priority,
                "assignee_id": assignee_id,
                "due_date": due,
            },
        )
        response.raise_for_status()
        task = response.json()
        verb = "attached to" if task_id else "created"
        click.echo(f"task #{task['id']} {verb}: {task['title']}")


@inbox.command("dismiss")
@click.argument("item_id", type=int)
@config_option
def inbox_dismiss(item_id: int, config: Config) -> None:
    """Dismiss a nugget (not actionable)."""
    with _client(config) as client:
        response = client.post(f"/api/nuggets/{item_id}/dismiss")
        response.raise_for_status()
        click.echo(f"nugget #{item_id} dismissed")


@inbox.command("done")
@click.argument("item_id", type=int)
@config_option
def inbox_done(item_id: int, config: Config) -> None:
    """Mark a nugget as already finished (the note is left untouched)."""
    with _client(config) as client:
        response = client.post(f"/api/nuggets/{item_id}/done")
        response.raise_for_status()
        click.echo(f"nugget #{item_id} marked already done")


# --- one-on-ones ---


@cli.group("oneonone")
def oneonone() -> None:
    """1:1 meetings."""


@oneonone.command("create")
@click.argument("person_id", type=int)
@click.argument("scheduled_for")
@config_option
def oneonone_create(person_id: int, scheduled_for: str, config: Config) -> None:
    """Create a 1:1 (open topics carry forward)."""
    with _client(config) as client:
        response = client.post(
            "/api/one-on-ones",
            json={"person_id": person_id, "scheduled_for": scheduled_for},
        )
        response.raise_for_status()
        _echo_json(response.json())


@oneonone.command("agenda")
@click.argument("meeting_id", type=int)
@config_option
def oneonone_agenda(meeting_id: int, config: Config) -> None:
    """Print the 1:1 agenda markdown."""
    with _client(config) as client:
        response = client.get(f"/api/one-on-ones/{meeting_id}/agenda.md")
        response.raise_for_status()
        click.echo(response.json()["markdown"])


@oneonone.command("topic")
@click.argument("meeting_id", type=int)
@click.argument("topic")
@config_option
def oneonone_topic(meeting_id: int, topic: str, config: Config) -> None:
    """Add a discussion topic."""
    with _client(config) as client:
        response = client.post(f"/api/one-on-ones/{meeting_id}/agenda", json={"topic": topic})
        response.raise_for_status()
        _echo_json(response.json())


# --- whatnext / jobs / google ---


@cli.command("whatnext")
@click.option("--llm", "use_llm", is_flag=True, default=False, help="Add LLM advice.")
@config_option
def whatnext_cmd(use_llm: bool, config: Config) -> None:
    """What should I focus on?"""
    with _client(config) as client:
        response = client.get("/api/whatnext", params={"use_llm": str(use_llm).lower()})
        response.raise_for_status()
        click.echo(response.json()["markdown"])


@cli.group()
def jobs() -> None:
    """Jobs: list, run on demand, view history."""


@jobs.command("list")
@config_option
def jobs_list(config: Config) -> None:
    """List registered jobs."""
    with _client(config) as client:
        response = client.get("/api/jobs")
        response.raise_for_status()
        for name in response.json()["jobs"]:
            click.echo(name)


@jobs.command("run")
@click.argument("name")
@click.option("--full", is_flag=True, default=False, help="notes_scan: rescan the whole window.")
@click.option(
    "--timeout",
    type=float,
    default=600,
    help="Seconds to wait for the job (full scans are slow).",
)
@config_option
def jobs_run(name: str, full: bool, timeout: float, config: Config) -> None:
    """Run a job on demand."""
    with _client(config, timeout=timeout) as client:
        response = client.post(f"/api/jobs/{name}/run", params={"full": str(full).lower()})
        response.raise_for_status()
        _echo_json(response.json())


@jobs.command("runs")
@config_option
def jobs_runs(config: Config) -> None:
    """Show recent job runs."""
    with _client(config) as client:
        response = client.get("/api/jobs/runs")
        response.raise_for_status()
        for run in response.json():
            result = f" {run['result']}" if run.get("result") else ""
            click.echo(
                f"#{run['id']} {run['job_name']} {run['status']} "
                f"started={run['started_at']} tokens={run['tokens_used']}{result}"
            )


@cli.group()
def google() -> None:
    """Google account auth."""


@google.command("auth")
@config_option
def google_auth(config: Config) -> None:
    """One-time interactive OAuth consent; stores the token in the DB."""
    from ewo.db.session import make_engine, make_session_factory
    from ewo.integrations.google import run_consent_flow, save_token

    token = run_consent_flow(config.google)
    factory = make_session_factory(make_engine(config))
    with factory() as session:
        save_token(session, token)
    click.echo("google token stored")
