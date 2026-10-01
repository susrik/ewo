"""Pydantic configuration model and loaders.

The server reads ``ewo.json`` by default; the filename can be overridden with
the ``EWO_CONFIG_FILENAME`` environment variable. Click commands use
``config_option`` which validates the file via the same model.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click
from pydantic import BaseModel, Field, ValidationError

CONFIG_ENV_VAR = "EWO_CONFIG_FILENAME"
DEFAULT_CONFIG_FILENAME = "ewo.json"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class StorageConfig(BaseModel):
    """All mutable filesystem state lives under these paths."""

    data_dir: Path = Path("~/.local/share/ewo")
    reports_clone_dir: Path | None = None
    tmp_dir: Path | None = None


class DatabaseConfig(BaseModel):
    url: str = "postgresql+psycopg://ewo:ewo@localhost:5432/ewo"


class LLMConfig(BaseModel):
    """Any OpenAI-protocol-compliant endpoint.

    ``request_timeout`` caps a single completion attempt (seconds). Make sure
    any proxy in front (e.g. litellm ``request_timeout``) allows at least this
    long — slow reasoning models need it for note extraction.
    """

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    smart_model: str = "gpt-4o"
    request_timeout: int = 240


class JiraConfig(BaseModel):
    enabled: bool = False
    base_url: str = ""
    email: str = ""
    api_token: str = ""
    jql: str = ""


class GoogleConfig(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    calendar_id: str = "primary"
    email_from: str = ""
    email_to: str = ""


class DiscordConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    channel_id: int = 0


class ReportsRepoConfig(BaseModel):
    enabled: bool = False
    repo_url: str = ""
    token: str = ""
    branch: str = "main"
    folder: str = "ewo"
    author_name: str = "ewo"
    author_email: str = "ewo@localhost"


class JobsConfig(BaseModel):
    """Cron-style schedules per job name; empty string disables the schedule."""

    schedules: dict[str, str] = Field(default_factory=dict)


class NotesConfig(BaseModel):
    """Read-only access to a tree of markdown notes (ewo never writes into it).

    ``people_dir`` is the folder holding one sub-folder per team member (used to
    seed people and attribute items); ``exclude`` lists relative paths or
    path prefixes to skip when scanning; ``window_days`` bounds a full rescan.
    """

    enabled: bool = False
    root: Path = Path("~/notes")
    people_dir: str = "swd/people"
    exclude: list[str] = Field(default_factory=list)
    window_days: int = 60
    max_file_chars: int = 40_000


class MCPConfig(BaseModel):
    http_enabled: bool = False
    http_port: int = 8765


class NuggetConfig(BaseModel):
    """Defaults for the task created when attaching a nugget to a new task.

    ``default_priority`` seeds the priority for a new task created from a
    nugget whose kind isn't a deadline. ``default_assignee_self`` assigns the
    new task to the owner (``is_self``) when the nugget has no owner.
    """

    default_priority: str = "normal"
    default_assignee_self: bool = True


class Config(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    jira: JiraConfig = Field(default_factory=JiraConfig)
    google: GoogleConfig = Field(default_factory=GoogleConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    reports_repo: ReportsRepoConfig = Field(default_factory=ReportsRepoConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    notes: NotesConfig = Field(default_factory=NotesConfig)
    nugget: NuggetConfig = Field(default_factory=NuggetConfig)
    api_base_url: str = "http://localhost:8000"

    @property
    def data_dir(self) -> Path:
        return self.storage.data_dir.expanduser()

    @property
    def notes_root(self) -> Path:
        return self.notes.root.expanduser()

    @property
    def reports_clone_dir(self) -> Path:
        if self.storage.reports_clone_dir is not None:
            return self.storage.reports_clone_dir.expanduser()
        return self.data_dir / "reports-repo"

    @property
    def tmp_dir(self) -> Path:
        if self.storage.tmp_dir is not None:
            return self.storage.tmp_dir.expanduser()
        return self.data_dir / "tmp"


def config_path_from_env() -> Path:
    """Resolve the config file path from EWO_CONFIG_FILENAME or the default."""
    return Path(os.environ.get(CONFIG_ENV_VAR, DEFAULT_CONFIG_FILENAME))


def load_config(path: Path | None = None) -> Config:
    """Load and validate config from *path* (default: env var / ewo.json)."""
    resolved = path if path is not None else config_path_from_env()
    if not resolved.exists():
        raise FileNotFoundError(f"config file not found: {resolved}")
    data = json.loads(resolved.read_text())
    return Config.model_validate(data)


def _click_config_callback(ctx: click.Context, param: click.Parameter, value: str | None) -> Config:
    """Click callback: validate & parse the config file into a Config."""
    path = Path(value) if value is not None else config_path_from_env()
    try:
        return load_config(path)
    except FileNotFoundError as exc:
        raise click.BadParameter(str(exc)) from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        raise click.BadParameter(f"invalid config file {path}: {exc}") from exc


def config_option(func: Any) -> Any:
    """Shared ``--config`` option for click commands."""
    return click.option(
        "--config",
        "config",
        default=None,
        envvar=CONFIG_ENV_VAR,
        help="Path to config file (default: ewo.json or $EWO_CONFIG_FILENAME).",
        callback=_click_config_callback,
    )(func)
