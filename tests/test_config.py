"""Config loading, env override, derived paths, click callback."""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from ewo.config import (
    CONFIG_ENV_VAR,
    Config,
    config_option,
    config_path_from_env,
    load_config,
)


def write_config(path: Path, data: dict[str, object] | None = None) -> Path:
    path.write_text(json.dumps(data or {}))
    return path


def test_defaults() -> None:
    config = Config()
    assert config.server.host == "0.0.0.0"
    assert config.server.port == 8000
    assert config.reports_repo.folder == "ewo"


def test_load_config_from_explicit_path(tmp_path: Path) -> None:
    path = write_config(tmp_path / "c.json", {"server": {"port": 9000}})
    config = load_config(path)
    assert config.server.port == 9000


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.json")


def test_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_config(tmp_path / "env.json", {"server": {"port": 7777}})
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    assert config_path_from_env() == path
    assert load_config().server.port == 7777


def test_default_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    assert config_path_from_env() == Path("ewo.json")


def test_storage_derived_paths(tmp_path: Path) -> None:
    config = Config.model_validate({"storage": {"data_dir": str(tmp_path)}})
    assert config.data_dir == tmp_path
    assert config.reports_clone_dir == tmp_path / "reports-repo"
    assert config.tmp_dir == tmp_path / "tmp"


def test_storage_explicit_overrides(tmp_path: Path) -> None:
    config = Config.model_validate(
        {
            "storage": {
                "data_dir": str(tmp_path),
                "reports_clone_dir": str(tmp_path / "rc"),
                "tmp_dir": str(tmp_path / "t"),
            }
        }
    )
    assert config.reports_clone_dir == tmp_path / "rc"
    assert config.tmp_dir == tmp_path / "t"


@click.command()
@config_option
def _dummy(config: Config) -> None:
    click.echo(f"port={config.server.port}")


def test_click_callback_valid(tmp_path: Path) -> None:
    from click.testing import CliRunner

    path = write_config(tmp_path / "ok.json", {"server": {"port": 1234}})
    result = CliRunner().invoke(_dummy, ["--config", str(path)])
    assert result.exit_code == 0
    assert "port=1234" in result.output


def test_click_callback_missing_file(tmp_path: Path) -> None:
    from click.testing import CliRunner

    result = CliRunner().invoke(_dummy, ["--config", str(tmp_path / "nope.json")])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_click_callback_invalid_json(tmp_path: Path) -> None:
    from click.testing import CliRunner

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    result = CliRunner().invoke(_dummy, ["--config", str(bad)])
    assert result.exit_code != 0
    assert "invalid config" in result.output


def test_click_callback_schema_error(tmp_path: Path) -> None:
    from click.testing import CliRunner

    path = write_config(tmp_path / "schema.json", {"server": {"port": "not-a-port"}})
    result = CliRunner().invoke(_dummy, ["--config", str(path)])
    assert result.exit_code != 0
    assert "invalid config" in result.output
