# ewo — agent/maintainer context

Task/project management platform for the owner and their team. One FastAPI
service owns all data; the CLI, htmx web GUI, Discord listener, and MCP server
are thin HTTP clients of the same `/api`.

**Read the docs first.** Human-facing and behavioral detail lives in [`docs/`](docs/)
(`docs/index.md` is the hub): concepts, workflows, jobs, CLI, config,
integrations, architecture, API, and roadmap. Don't re-derive behavior from
code when a doc already describes it — and when you change behavior, update the
docs (see [Documentation](#documentation) below).

## Environment

- Python venv: `~/.virtualenvs/ewo` (activate or call its bin/ directly)
- Run tests: `pytest` (coverage gate ≥95% is enforced via pyproject)
- Type check: `mypy ewo`
- Lint/format: `ruff check ewo tests` and `ruff format ewo tests`
- Run server locally: `python -m ewo.server` (needs `pip install uvicorn` in
  the venv — uvicorn is deliberately NOT in requirements.txt/pyproject; in
  Docker it is installed at image build time)
- Docker: `docker compose up --build` (postgres 17 + server; entrypoint runs
  `alembic upgrade head` first)
- See [`docs/development.md`](docs/development.md) for the full workflow.

## Hard rules

- ALL tool config lives in `pyproject.toml` — never create mypy.ini,
  setup.cfg, .ruff.toml, pytest.ini, etc.
- uvicorn must never appear in requirements.txt or pyproject dependencies.
  It is imported lazily inside `ewo/server/__main__.py:main()` only.
- Every JSON body in or out of the HTTP API has a pydantic model in
  `ewo/server/schemas.py`.
- GUI-only endpoints go under `/gui`; user-facing endpoints (CLI, listeners,
  MCP, external) under `/api`.
- Sync SQLAlchemy only (2.0 style). Keep models dialect-portable: tests run
  on in-memory SQLite, production on Postgres. Schema changes require an
  Alembic migration in `ewo/db/alembic/versions/`.
- Never write files outside the paths in `config.storage` (`data_dir` and
  derived dirs). This keeps Docker overlay writes at zero so
  `read_only: true` can eventually be enabled in docker-compose.yml.
- The notes tree (`config.notes.root`) is **read-only** for ewo. `ewo/core/notes.py`
  is the only module that touches it and it only reads. Marking a stale
  nugget "already done" is recorded in ewo, never written back to the note.
- `Person` rows are team members (plus one `is_self` row for the owner), not
  login accounts — no email, no auth.
- No network access in tests. LLM calls use `FakeLLM`; HTTP-level tests use
  respx; external clients (Jira/Google/Discord/git) sit behind small
  interfaces with fakes in tests.
- Gotcha: the openai 3.x SDK uses a vendored httpx (`httpx2`), which respx
  cannot intercept. `OpenAILLM` therefore accepts an `http_client` parameter;
  wire-format tests inject `httpx2.Client(transport=httpx2.MockTransport(...))`
  (see tests/test_llm.py).
- Config: pydantic `Config` in `ewo/config.py`; default file `ewo.json`,
  env override `EWO_CONFIG_FILENAME`; click commands use `--config` with the
  shared validation callback. Add new settings to the model + `ewo.example.json`.

## Conventions (summary)

Full behavioral detail is in [`docs/architecture.md`](docs/architecture.md) and
the per-topic guides. Short pointers:

- Service layer in `ewo/core/` takes a `Session` (and plain args), returns ORM
  objects or plain data — no FastAPI imports in core.
- Jobs: register in `ewo/jobs/builtin.py` via the registry; every execution
  records a `JobRun` row. On-demand: `POST /api/jobs/{name}/run[?full=true]` /
  `ewo jobs run <name> [--full]`. Long jobs: `wait=false` / `--no-wait` runs
  in a daemon thread and returns 202 with the run id; dead-process runs are
  failed at startup (`mark_interrupted_runs`).
- Notes pipeline: `core/notes.py` (reader) → `core/note_extract.py` (LLM,
  `notes_scan`) → `Nugget` inbox (`core/nuggets.py`) → attach links the nugget
  to a task via an `ExternalLink(system="notes", …)`. See [`docs/inbox.md`](docs/inbox.md).
- Task organization (AI) is **stateless**: `core/task_organize.py` proposes,
  nothing persists until confirmed. See [`docs/organize.md`](docs/organize.md).
- Tags/labels are additive through the tree; removal is never propagated; never
  propagate via `update_task(..., tags=…)`. See [`docs/organize.md`](docs/organize.md).
- GUI fragments (`_*.html`) are what htmx swaps in; a mutation returns the
  fragment it belongs to. Free-form text in flex/grid needs `min-w-0` +
  `break-words`/`break-all`. See [`docs/architecture.md`](docs/architecture.md).
- Reports use subprocess `git` via `GitRepo` in `ewo/core/report_publisher.py`
  — no GitPython.
- Discord/other listeners share the command router in `ewo/listeners/base.py`;
  MCP tools are defined once in `ewo/mcp/tools.py`.

## Future work

See [`docs/roadmap.md`](docs/roadmap.md).

## Documentation

Docs live in [`docs/`](docs/) as interlinked markdown, one focused file per
topic, readable on GitHub. The hub is `docs/index.md`, organized by audience
(basic users, advanced users, developers).

When maintaining or writing docs:

- Add/update the topic file that owns the subject; keep each file concise and
  focused on one use case or concern.
- Link between files for navigation; don't duplicate prose that already has a
  home.
- Keep the three audiences in mind: basic users (browse/manage), advanced users
  (inference, jobs, integrations), developers (code, architecture, API).
- Reflect behavior changes in the relevant doc in the same change set, and keep
  `docs/index.md` and the top-level `README.md` links current when files are
  added, renamed, or removed.
- Prefer moving low-level/behavioral detail out of this file and into `docs/`;
  keep `AGENTS.md` a human-friendly starting point plus the hard, code-level
  rules.