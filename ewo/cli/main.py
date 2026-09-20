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


def _client(config: Config) -> httpx.Client:
    return httpx.Client(base_url=config.api_base_url, timeout=60)


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
@click.option("--priority", default="normal")
@click.option("--tag", "tags", multiple=True)
@click.option("--due", default=None, help="Due date YYYY-MM-DD")
@config_option
def task_add(
    title: str,
    assignee_id: int | None,
    priority: str,
    tags: tuple[str, ...],
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
                "priority": priority,
                "tags": list(tags),
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
@click.option("--email", default=None)
@click.option("--jira-account-id", default=None)
@click.option("--discord-user-id", default=None)
@config_option
def person_add(
    name: str,
    email: str | None,
    jira_account_id: str | None,
    discord_user_id: str | None,
    config: Config,
) -> None:
    """Add a person."""
    with _client(config) as client:
        response = client.post(
            "/api/people",
            json={
                "name": name,
                "email": email,
                "jira_account_id": jira_account_id,
                "discord_user_id": discord_user_id,
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
            click.echo(f"#{p['id']} {p['name']} {p['email'] or ''}")


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
@config_option
def jobs_run(name: str, config: Config) -> None:
    """Run a job on demand."""
    with _client(config) as client:
        response = client.post(f"/api/jobs/{name}/run")
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
            click.echo(
                f"#{run['id']} {run['job_name']} {run['status']} "
                f"started={run['started_at']} tokens={run['tokens_used']}"
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
