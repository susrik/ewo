# Jobs

Jobs are named units of background work. Each is a registered function in
`ewo/jobs/builtin.py`; schedules come from `config.jobs.schedules` (5-field
cron). Every run — scheduled or on demand — records a `JobRun` row whose
return string lands in `JobRun.result`.

## The jobs

| name | what it does |
|---|---|
| `jira_sync` | Pull the configured JQL, create/update linked tasks. No-op if Jira disabled. |
| `daily_report` | Generate the daily markdown, publish it to the reports repo, and (if Google is enabled) email it. |
| `what_next` | Generate the what-next report and publish it. |
| `notes_scan` | Extract outstanding nuggets from notes changed since the last successful scan (`full=true` rescans the whole window), then suggest candidate tasks for the new nuggets. Requires `llm.api_key`. |
| `nuggets_match` | (Re)compute suggested tasks for unreviewed nuggets. Requires `llm.api_key`. |
| `housekeeping` | Data-integrity sweep: additive parent-label propagation through the task tree. |

`daily_report`, `what_next`, and `jira_sync` still work without an LLM API key;
the LLM narrative is simply skipped. `notes_scan` and `nuggets_match` require
one.

## Starting them

- **Scheduled** — set a cron expression per job in `config.jobs.schedules`
  (empty string disables it). Runs go through the in-process scheduler.
- **On demand** — `ewo jobs run <name> [--full] [--no-wait]`, or
  `POST /api/jobs/{name}/run?full=true&wait=false`, or the buttons on the Jobs
  page, or `ewo_run_job` (MCP).

### Long jobs

A full `notes_scan` can be slow (reasoning models take minutes per note). Run
it in the background:

```sh
ewo jobs run notes_scan --full --no-wait
ewo jobs runs     # watch progress
```

`--no-wait` (or `wait=false`) starts the job in a daemon thread and returns the
run id immediately. Runs left `running` by a dead process are marked failed at
startup.

## Inspecting

- `ewo jobs list` — registered job names.
- `ewo jobs runs` — recent runs with status, start time, tokens used, result.
- `GET /api/jobs`, `GET /api/jobs/runs`.

See [What next](what-next.md) for the reports these jobs produce, and
[Configuration](config.md) for `jobs.schedules`.