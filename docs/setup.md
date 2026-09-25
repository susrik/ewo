# ewo setup

## Local development

```sh
source ~/.virtualenvs/ewo/bin/activate
pip install -e ".[dev]"
pip install uvicorn          # runtime-only; deliberately not a package dep

cp ewo.example.json ewo.json # then edit: database.url, credentials, etc.
alembic upgrade head
python -m ewo.server         # host/port from ewo.json (default 0.0.0.0:8000)
```

Quality gates:

```sh
pytest                       # coverage gate ≥95%
mypy ewo
ruff check ewo tests && ruff format ewo tests
```

## Docker

```sh
cp ewo.example.json ewo.json   # docker-ready defaults: db@5432, data_dir=/data
docker compose up --build
```

- Entrypoint runs `alembic upgrade head`, then `python -m ewo.server`.
- `./ewo.json` is mounted read-only at `/config/ewo.json`; postgres data in the
  `ewo-pgdata` volume, all app state in `ewo-data` (mounted at `/data`).
- The compose port mapping (`8000:8000`) must match `server.port` in ewo.json.

## Config reference (ewo.json)

Loaded from `ewo.json` or `$EWO_CONFIG_FILENAME`; every CLI command accepts
`--config <path>`. See `ewo.example.json` for the full shape:

| section | purpose |
|---|---|
| `server` | listen host/port |
| `storage` | `data_dir` root for all mutable files (+ optional `reports_clone_dir`, `tmp_dir`) |
| `database` | SQLAlchemy URL (postgres in docker, sqlite works for dev) |
| `llm` | OpenAI-protocol endpoint, key, cheap + smart model names. LLM report narrative is skipped when `api_key` is empty |
| `jira` | read-only sync: base_url, email, api_token, JQL |
| `google` | OAuth client for the dedicated account (gcal read + gmail send) |
| `discord` | bot token + channel restriction |
| `reports_repo` | dedicated GitHub repo for markdown reports |
| `jobs.schedules` | job name → 5-field cron expression ("" disables) |
| `mcp` | HTTP transport toggle + port |
| `notes` | read-only markdown notes tree: `root`, `people_dir`, `exclude`, `window_days`, `max_file_chars` |
| `api_base_url` | where CLI/listeners/MCP find the server |

## Notes → inbox → tasks

ewo can read a tree of markdown notes (never writes to it) and pull the
outstanding work out of it. Set `notes.enabled: true` and `notes.root`, and
make sure `llm.api_key` is set (extraction uses the `smart_model`).

1. **People.** Each team member has a folder `<people_dir>/<name>/` in the
   notes (the `AGENTS.md` inside describes them). `ewo person seed` (or the
   *Seed from notes* button on the People page) creates a person per folder
   with `notes_dir` set. Mark yourself with `ewo person add "Erik" --self`;
   items written in the first person are attributed to you. Add `--alias` for
   other spellings used in notes. A bare first name is resolved by the folder
   the note lives in first, then by unique name/alias — an ambiguous alias
   (two people both called "James") resolves to nobody rather than guessing.
2. **Scan.** `notes_scan` reads every note changed since the last successful
   scan (or, with `--full` / `?full=true`, everything dated or modified within
   `window_days`), passing the folder's `AGENTS.md` chain, the roster and
   today's date to the LLM, and asks for open actions, questions, deadlines
   and risks with a line citation each. Rolling `old.md` files contribute only
   their in-window `## YYYY-MM-DD` sections. Run it with `ewo jobs run
   notes_scan [--full]`, the *Scan notes* buttons on the Inbox page, or on a
   schedule via `jobs.schedules.notes_scan`.
3. **Review.** Nuggets (work items found in notes) land in the Inbox
   (`/inbox`, `ewo inbox list`, `GET /api/nuggets`), pre-grouped by their
   suggested task. Suggestions come from the `nuggets_match` job, which also
   runs at the end of every scan: items repeated across notes files inherit
   the task their twin was attached to, and the LLM matches the rest. For
   each nugget: **attach** adds it to the suggested/selected task (or a new
   one, `source=notes`, citation linked as `notes:<path>:<line>:<id>`; Jira
   keys mentioned in the note become task links), **dismiss** drops it, or
   **already done** records that the note is stale. Reviewed nuggets are
   never re-surfaced by later scans; re-seen open nuggets only bump
   `last_seen_at` (which nudges the linked task up in *what next*).

A full rescan of a two-month window over ~90 notes is on the order of 300k
input tokens; incremental scans are usually a handful of files.

## Task organization

Tasks are coherent pieces of work (topics) that collect nuggets: the task
detail panel lists its attached nuggets, where they can be edited, moved to
another task, or detached back to the inbox. Tasks form a single-parent tree
(sub-tasks of any depth) and have start/due dates plus an auto-recorded
completion time. A task can link to several Jira issues, and one issue can
be linked from several tasks. The *Organize (AI)* button on the Tasks page
asks the smart model for merge/split/create/retitle proposals — nothing
changes until you confirm a proposal.

## Google (one-time)

1. Create an OAuth client (Desktop) on the dedicated Google account; publish
   the consent screen "In production" so refresh tokens don't expire weekly.
2. Put client_id/client_secret in ewo.json.
3. Run `ewo google auth` — the token is stored in the database and refreshed
   automatically from then on.

## Jira

Create a read-only API token, set `jira.base_url/email/api_token`, and a JQL
that covers the projects/tickets to track. `jira_sync` (scheduled or
`ewo jobs run jira_sync`) creates tasks for new issues and keeps linked task
status/assignee in step with Jira. Map people to Jira via
`ewo person add "Anna" --jira-account-id <accountId>`.

## Reports repo

Create a dedicated GitHub repo + a PAT with repo write. Reports land at
`<folder>/YYYY-MM/YYYY-MM-DD-HHMM-<type>.md` and are also stored in the DB
(`ewo` CLI / GUI can list them).

## Discord

Create a bot (message content intent enabled), set `discord.token` and
optionally `discord.channel_id`, and set `discord.enabled: true`. Commands:
`!task <title> [@person] [#tag] [!priority]`, `!done <id>`, `!tasks [@person]`,
`!next`, `!help`.

## MCP

- **stdio (opencode)**: register command `python -m ewo.mcp.server` with env
  `EWO_CONFIG_FILENAME=/path/to/ewo.json` (the ewo server must be running).
- **HTTP service**: set `mcp.http_enabled: true` (+ `mcp.http_port`) and run
  `python -m ewo.mcp.server` as a service.

Tools: `ewo_list_tasks`, `ewo_create_task`, `ewo_set_task_status`,
`ewo_what_next`, `ewo_run_job`, `ewo_inbox_list`, `ewo_inbox_attach`,
`ewo_inbox_resolve`.
