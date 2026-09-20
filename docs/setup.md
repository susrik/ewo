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
| `api_base_url` | where CLI/listeners/MCP find the server |

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
`ewo_what_next`, `ewo_run_job`.
