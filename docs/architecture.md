# Architecture

ewo is one FastAPI service that owns all data. The web GUI (htmx), CLI, Discord
listener, and MCP server are thin HTTP clients of the same `/api` — they hold
no business logic of their own.

## Layout

```
ewo/
  server/          FastAPI app, /api routes, /gui routes, pydantic schemas
    api.py         user-facing endpoints (CLI, listeners, MCP, external)
    gui.py         GUI-only endpoints + htmx fragments
    schemas.py     a pydantic model for every JSON body in/out
  core/            service layer (Session in, ORM objects / plain data out)
    notes.py         notes-tree reader (read-only)
    note_extract.py  LLM extraction into nuggets (notes_scan)
    nuggets.py       inbox (attach/dismiss/done)
    nugget_match.py  suggested-task matching
    task_organize.py stateless merge/split/create/retitle + topic grouping
    tasks.py, people.py, tags.py, one_on_ones.py, whatnext.py,
    reports.py, dashboard.py, housekeeping.py, report_publisher.py, llm.py,
    job_log.py
  db/              SQLAlchemy models + Alembic migrations
  jobs/            registry, builtin jobs, scheduler, broadcast (live-log hub)
  listeners/       Discord + shared command router (base.py)
  mcp/             tool definitions (tools.py) + stdio/HTTP servers
  cli/             click CLI (thin /api client)
  config.py        pydantic Config + loaders
  integrations/    Jira, Google clients (interfaces with fakes in tests)
```

## Service layer rule

`ewo/core/` takes a `Session` and plain arguments, and returns ORM objects or
plain data — **no FastAPI imports in core**. This keeps business logic
testable without HTTP.

## API vs GUI

- `/api` — user-facing endpoints (CLI, listeners, MCP, external callers).
- `/gui` — GUI-only endpoints, returning htmx fragments (`_*.html`) that htmx
  swaps into place. A mutation returns the fragment it belongs to (row, item,
  list). Pages (`/`, `/tasks`, `/inbox`, `/people`, `/jobs`, `/labels`) extend
  `_layout.html`.

See the full endpoint list in [API](api.md).

## Notes pipeline

```
core/notes.py (reader)   → core/note_extract.py (LLM, notes_scan)
  → Nugget inbox (core/nuggets.py)     → attach → Task(source=notes)
                                          with ExternalLink(system="notes",
                                            external_key="<path>:<line>:<nugget_id>")
```

- Nuggets dedupe on `fingerprint`; reviewed nuggets are never re-surfaced.
- Jira keys in notes are captured on the nugget (`jira_keys`) and become task
  links on attach.
- People are attributed via `Person.notes_dir` (folder wins), then
  `name`/`aliases` (`core/people.resolve_person`).
- `nuggets_match` (also run at the end of `notes_scan`) stores a suggested task
  per new nugget; the inbox groups by it.

## Task organization (AI)

`core/task_organize.py` computes **stateless** merge/split/create/retitle
proposals behind the "Organize (AI)" button — nothing persists until confirmed.
The same module powers topic grouping (`propose_topic_group` / `apply_group`)
and related-task grouping (`propose_related`). Parent choices are validated up
front; self-parent and descendant cycles raise `ValueError` before any change.

## Jobs & live logs

Jobs register in `jobs/builtin.py` via the registry; every execution records a
`JobRun` row. As they run, jobs append `JobLogEntry` rows through
`core/job_log.py` — steps, errors, and per-call LLM detail (model, tier,
prompt/token sizes, timing, truncated preview). The GUI streams a run's log
over SSE (`/gui/jobs/{id}/events`): it first **replays** the persisted entries
(the DB is the source of truth across workers/restarts), then live-tails via
the in-process `jobs/broadcast.py` `BroadcastHub`, and closes with a terminal
`done` event. `config.jobs.log_retention_days` bounds growth (the
`housekeeping` job prunes); `config.jobs.log_prompt_preview_chars` caps preview
size.

## Tags & inheritance

`core/tags.py` (`normalize_name` = strip+lower, CRUD, `descendant_ids`,
`add_tags_to_descendants`). Inheritance is additive: `create_task` unions the
parent's tags into a new child; propagation through `add_tags_to_descendants`
is append-only; removal is never propagated. `update_task(..., tags=…)` **replaces**
the tag set wholesale — never use it to propagate.

## Reports & git

Reports are generated as markdown and published via the `GitRepo` wrapper in
`core/report_publisher.py` — subprocess `git`, no GitPython.

## Listeners & MCP

Discord/other listeners share the command router in `listeners/base.py`;
platform code only adapts transport. MCP tools are defined once in
`mcp/tools.py` and exposed via both stdio and HTTP transports.

## Layering gotchas

- The openai 3.x SDK vendors its own `httpx` (`httpx2`), which `respx` cannot
  intercept — `OpenAILLM` accepts an `http_client` for wire-format tests.
- Any element rendering free-form user text in a flex/grid layout needs
  `min-w-0` on itself and its flex/grid-item ancestors (with `break-words` for
  prose, `break-all` for excerpts/tokens/URLs).