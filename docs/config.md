# Configuration

ewo reads a single JSON file — `ewo.json` by default, or
`$EWO_CONFIG_FILENAME`. Every CLI command accepts `--config <path>` and
validates the file against the same pydantic model. See `ewo.example.json` for
the full, commented shape.

## Sections

| section | purpose |
|---|---|
| `server` | listen host/port |
| `storage` | `data_dir` root for all mutable files (+ optional `reports_clone_dir`, `tmp_dir`) |
| `database` | SQLAlchemy URL (postgres in docker, sqlite works for dev) |
| `api_base_url` | where CLI/listeners/MCP find the server |
| `llm` | OpenAI-protocol endpoint, key, `model` + `smart_model` names, `request_timeout` (seconds per completion attempt; default 240). LLM narrative is skipped when `api_key` is empty |
| `jira` | read-only sync: `enabled`, `base_url`, `email`, `api_token`, `jql` |
| `google` | OAuth client for the dedicated account (`calendar_id`, `email_from`, `email_to`) |
| `discord` | bot `token` + `channel_id` restriction |
| `reports_repo` | dedicated GitHub repo for markdown reports |
| `jobs.schedules` | job name → 5-field cron expression ("" disables). On demand: `ewo jobs run <name>` |
| `mcp` | HTTP transport toggle + port |
| `notes` | read-only markdown notes tree: `root`, `people_dir`, `exclude`, `window_days`, `max_file_chars` |
| `nugget` | defaults for the task created when attaching a nugget to a new task (`default_priority`, `default_assignee_self`) |
| `gui` | GUI tuning (`task_picker_max_visible`) |

## LLM notes

Any OpenAI-protocol-compliant endpoint works. `request_timeout` caps a single
completion attempt — slow reasoning models can take minutes per note during
extraction, so **any proxy in front** (e.g. litellm's `request_timeout`, 60s by
default) must allow at least `llm.request_timeout` seconds per call, or scans
will stall on 408 retries.

## Storage

All mutable filesystem state lives under `config.storage` (`data_dir` and its
derived dirs). Nothing is ever written outside these paths, which keeps Docker
overlay writes at zero.

## Notes tree — read-only

The notes tree (`notes.root`) is read-only for ewo: it is only ever *read*.
Marking a stale nugget "already done" is recorded in ewo, never written back to
the notes.

### Optional `reports_clone_dir`, `tmp_dir`

Defaults derive from `data_dir` (`reports-repo/` and `tmp/`); override them
under `storage` if you need them elsewhere.