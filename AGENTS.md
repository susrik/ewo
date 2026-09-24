# ewo — agent/maintainer context

Task/project management platform for the owner and their team. One FastAPI
service owns all data; the CLI, htmx web GUI, Discord listener, and MCP server
are thin HTTP clients of the same `/api`. See PLAN.md for full design and
rationale.

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
  is the only module that touches it and it only reads. Marking a stale note
  item "already done" is recorded in ewo, never written back to the note.
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

## Conventions

- Service layer in `ewo/core/` takes a `Session` (and plain args), returns ORM
  objects or plain data — no FastAPI imports in core.
- Jobs: register in `ewo/jobs/builtin.py` via the registry; every execution
  (scheduled or on-demand) records a `JobRun` row (its return string goes in
  `JobRun.result`). On-demand: `POST /api/jobs/{name}/run[?full=true]` /
  `ewo jobs run <name> [--full]`; per-run params arrive via `JobContext.params`.
- Notes pipeline: `core/notes.py` (reader) → `core/note_extract.py` (LLM
  extraction, `notes_scan` job) → `NoteItem` inbox (`core/note_items.py`) →
  accept creates a `Task(source=notes)` with an `ExternalLink(system="notes",
  external_key="<path>:<line>")`. Items dedupe on `fingerprint`; reviewed
  items are never re-surfaced. People are attributed via `Person.notes_dir`
  (folder wins) then `name`/`aliases` (`core/people.resolve_person`).
- GUI: pages `/`, `/tasks`, `/inbox`, `/people`, `/jobs` extend
  `_layout.html`; fragments are `_*.html` and are what htmx swaps in. A
  mutation returns the fragment it belongs to (row, item, list).
- Reports are generated as markdown and published to the dedicated GitHub
  repo configured in `reports_repo`; path format
  `<folder>/YYYY-MM/YYYY-MM-DD-HHMM-<type>.md` (HHMM — no colon, Windows-safe).
- Git operations use subprocess `git` via the `GitRepo` wrapper in
  `ewo/core/report_publisher.py` — no GitPython.
- Discord/other listeners share the command router in `ewo/listeners/base.py`;
  platform code only adapts transport.
- MCP tools are defined once in `ewo/mcp/tools.py` and exposed via both stdio
  (for opencode) and HTTP transports.

## Future work (tracked, not yet built)

- Jira: `HttpJiraClient` speaks the Jira **Cloud** v3 API (`/rest/api/3/search/jql`,
  `nextPageToken`, `accountId`). The configured instance is Jira **Server/DC**
  9.x, which needs `/rest/api/2/search?startAt=`, Bearer PAT auth and
  `assignee.name` — every scheduled `jira_sync` currently fails with 403.
- Enable `read_only: true` + `tmpfs` in docker-compose (verify no overlay writes)
- Slack read-only listener
- Meeting-minutes archive integration
- Optional overview document (the dated `overviews/*.md` snapshot) generated
  from the inbox + tasks and published to the reports repo
