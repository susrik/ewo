"""Report publisher: path format, git wrapper behaviour (subprocess mocked)."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from ewo.config import Config, ReportsRepoConfig
from ewo.core.report_publisher import (
    GitError,
    GitRepo,
    publish_report,
    report_relative_path,
)


def test_report_relative_path_windows_safe() -> None:
    when = datetime(2026, 8, 30, 7, 15)
    path = report_relative_path("daily", when, "ewo")
    assert path == "ewo/2026-08/2026-08-30-0715-daily.md"
    assert ":" not in path


def _repo_config(**kwargs: Any) -> ReportsRepoConfig:
    defaults: dict[str, Any] = {
        "enabled": True,
        "repo_url": "https://github.com/me/reports.git",
        "token": "tok123",
        "branch": "main",
        "folder": "ewo",
    }
    defaults.update(kwargs)
    return ReportsRepoConfig(**defaults)


def test_authed_url_injects_token(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path, _repo_config())
    assert repo._authed_url() == "https://x-access-token:tok123@github.com/me/reports.git"


def test_authed_url_passthrough_no_token(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path, _repo_config(token=""))
    assert repo._authed_url() == "https://github.com/me/reports.git"


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_git_run_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: FakeCompleted(returncode=1, stderr="boom")
    )
    repo = GitRepo(tmp_path, _repo_config())
    with pytest.raises(GitError, match="boom"):
        repo.pull()


def test_ensure_clone_skips_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or FakeCompleted())
    GitRepo(tmp_path, _repo_config()).ensure_clone()
    assert calls == []


def test_ensure_clone_runs_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or FakeCompleted())
    workdir = tmp_path / "clone"
    GitRepo(workdir, _repo_config()).ensure_clone()
    assert calls[0][:2] == ["git", "clone"]
    assert "x-access-token:tok123" in calls[0][4]


class FakeGitRepo:
    """Fake at the GitRepo seam for publish_report tests."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.actions: list[str] = []

    def ensure_clone(self) -> None:
        self.actions.append("ensure_clone")

    def pull(self) -> None:
        self.actions.append("pull")

    def commit_and_push(self, relative_path: str, message: str) -> None:
        self.actions.append(f"push:{relative_path}:{message}")


def test_publish_report(tmp_path: Path) -> None:
    config = Config.model_validate(
        {
            "storage": {"data_dir": str(tmp_path)},
            "reports_repo": {"enabled": True, "repo_url": "https://x/y.git", "folder": "ewo"},
        }
    )
    fake = FakeGitRepo(tmp_path / "clone")
    when = datetime(2026, 8, 30, 7, 15)
    relative = publish_report(config, "daily", "# body\n", when, git_repo=fake)  # type: ignore[arg-type]

    assert relative == "ewo/2026-08/2026-08-30-0715-daily.md"
    written = (tmp_path / "clone" / relative).read_text()
    assert written == "# body\n"
    assert fake.actions[0] == "ensure_clone"
    assert fake.actions[1] == "pull"
    expected = "push:ewo/2026-08/2026-08-30-0715-daily.md:report: daily 2026-08-30 07:15"
    assert fake.actions[2] == expected


def test_git_commit_and_push_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or FakeCompleted())
    repo = GitRepo(tmp_path, _repo_config())
    repo.commit_and_push("ewo/x.md", "report: test")
    assert calls[0] == ["git", "add", "ewo/x.md"]
    assert "commit" in calls[1]
    assert "user.name=ewo" in calls[1]
    assert calls[2][:2] == ["git", "push"]
    assert calls[2][-1] == "HEAD:main"
