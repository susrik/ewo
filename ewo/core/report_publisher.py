"""Publish markdown reports to the dedicated GitHub repo.

Path format: ``<folder>/YYYY-MM/YYYY-MM-DD-HHMM-<type>.md`` (HHMM without a
colon for Windows compatibility). Git operations use subprocess ``git`` via
the small ``GitRepo`` wrapper — trivially faked in tests.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from ewo.config import Config, ReportsRepoConfig


class GitError(Exception):
    pass


class GitRepo:
    """Thin wrapper around subprocess git for a single local clone."""

    def __init__(self, workdir: Path, repo_config: ReportsRepoConfig) -> None:
        self.workdir = workdir
        self.config = repo_config

    def _authed_url(self) -> str:
        """Inject the token into the https remote URL."""
        parsed = urlparse(self.config.repo_url)
        if self.config.token and parsed.scheme == "https":
            netloc = f"x-access-token:{self.config.token}@{parsed.netloc}"
            return urlunparse(parsed._replace(netloc=netloc))
        return self.config.repo_url

    def _run(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.workdir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise GitError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout

    def ensure_clone(self) -> None:
        if (self.workdir / ".git").exists():
            return
        self.workdir.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            "clone",
            "--branch",
            self.config.branch,
            self._authed_url(),
            str(self.workdir),
            cwd=self.workdir.parent,
        )

    def pull(self) -> None:
        self._run("pull", "--rebase", self._authed_url(), self.config.branch)

    def commit_and_push(self, relative_path: str, message: str) -> None:
        self._run("add", relative_path)
        self._run(
            "-c",
            f"user.name={self.config.author_name}",
            "-c",
            f"user.email={self.config.author_email}",
            "commit",
            "-m",
            message,
        )
        self._run("push", self._authed_url(), f"HEAD:{self.config.branch}")


def report_relative_path(report_type: str, when: datetime, folder: str) -> str:
    """``<folder>/YYYY-MM/YYYY-MM-DD-HHMM-<type>.md``"""
    month_dir = when.strftime("%Y-%m")
    filename = f"{when.strftime('%Y-%m-%d-%H%M')}-{report_type}.md"
    return f"{folder}/{month_dir}/{filename}"


def publish_report(
    config: Config,
    report_type: str,
    body: str,
    when: datetime,
    git_repo: GitRepo | None = None,
) -> str:
    """Write the report into the clone, commit, and push. Returns repo path."""
    repo = git_repo or GitRepo(config.reports_clone_dir, config.reports_repo)
    repo.ensure_clone()
    repo.pull()

    relative = report_relative_path(report_type, when, config.reports_repo.folder)
    target = repo.workdir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)

    message = f"report: {report_type} {when.strftime('%Y-%m-%d %H:%M')}"
    repo.commit_and_push(relative, message)
    return relative
