# Development

How to work on ewo itself. See [Architecture](architecture.md) for the layout.

## Environment

```sh
source ~/.virtualenvs/ewo/bin/activate
pip install -e ".[dev]"
pip install uvicorn   # runtime-only; deliberately not a package dep
```

- Run tests: `pytest` (coverage gate ≥95% is enforced via pyproject)
- Type check: `mypy ewo`
- Lint/format: `ruff check ewo tests` and `ruff format ewo tests`
- Run server: `python -m ewo.server`
- Docker: `docker compose up --build` (postgres 17 + server; entrypoint runs
  `alembic upgrade head` first)

## Hard rules

- **ALL tool config lives in `pyproject.toml`** — never create `mypy.ini`,
  `setup.cfg`, `.ruff.toml`, `pytest.ini`, etc.
- **uvicorn must never appear in requirements.txt or pyproject dependencies.**
  It is imported lazily inside `ewo/server/__main__.py:main()` only (in Docker
  it is installed at image build time).
- **Every JSON body in/out of the HTTP API has a pydantic model** in
  `ewo/server/schemas.py`.
- **GUI-only endpoints under `/gui`; user-facing under `/api`.**
- **Sync SQLAlchemy only (2.0 style), dialect-portable** (tests run on in-memory
  SQLite, production on Postgres). Schema changes require an Alembic migration
  in `ewo/db/alembic/versions/`.
- **Never write files outside `config.storage` paths** (keeps Docker overlay
  writes at zero, enabling `read_only: true`).
- **The notes tree is read-only** — `ewo/core/notes.py` is the only module that
  touches it and it only reads.
- **`Person` rows are team members, not login accounts** — no email, no auth.
- **No network access in tests** — LLM calls use `FakeLLM`; HTTP tests use
  `respx`; external clients (Jira/Google/Discord/git) sit behind interfaces
  with fakes.
- **Config** — pydantic `Config` in `ewo/config.py`; default `ewo.json`, env
  override `EWO_CONFIG_FILENAME`; click uses `--config` with the shared
  validation callback. New settings go in the model **and** `ewo.example.json`.

## Testing gotcha

The openai 3.x SDK vendors `httpx` as `httpx2`, which `respx` can't intercept.
`OpenAILLM` accepts an `http_client` parameter; wire-format tests inject
`httpx2.Client(transport=httpx2.MockTransport(...))` — see `tests/test_llm.py`.

## Browser (Playwright) tests

The `tests/browser/` suite drives the real htmx GUI in a headless Chromium,
verifying the browser behaviour the `TestClient` suite can't observe (htmx
fragment swaps, form submission, checkbox/bulk selection, and the SSE job-log
stream). They are marked `browser` and **excluded from the default `pytest`
run** (and the coverage gate) via the `addopts` in `pyproject.toml`.

One-time setup (needs `uvicorn` in the dev venv, as for `python -m ewo.server`):

```sh
pip install -e ".[test-browser]"
playwright install chromium
```

Run them explicitly:

```sh
pytest -m browser --no-cov
```

Unlike the unit suite, browser tests need network access (htmx/Tailwind/daisyUI
load from CDNs) and bind a live uvicorn server on `127.0.0.1` over a
file-backed SQLite (see `tests/browser/conftest.py`). They are a focused smoke
suite — one test per core page/flow — not a mirror of `tests/test_gui.py`.

## Jobs

Register jobs in `ewo/jobs/builtin.py` via the registry; every execution
(scheduled or on-demand) records a `JobRun` row. Per-run params arrive via
`JobContext.params`. `wait=false` runs in a daemon thread and returns 202 with
the run id; runs left `running` by a dead process are failed at startup
(`mark_interrupted_runs` in the lifespan). See [Jobs](jobs.md) for behavior.