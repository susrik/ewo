"""python -m ewo.server: config-driven uvicorn launch (uvicorn mocked)."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from ewo.config import CONFIG_ENV_VAR
from ewo.server.__main__ import main


def test_main_runs_uvicorn_with_config_host_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "ewo.json"
    config_path.write_text(json.dumps({"server": {"host": "127.0.0.1", "port": 9999}}))
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))

    calls: list[dict[str, object]] = []
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = lambda app, **kwargs: calls.append({"app": app, **kwargs})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    main()

    assert calls == [
        {
            "app": "ewo.server.app:create_app",
            "factory": True,
            "host": "127.0.0.1",
            "port": 9999,
        }
    ]
