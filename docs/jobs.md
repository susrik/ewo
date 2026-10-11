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
| `nuggets_match` | (Re)compute suggested tasks for unreviewed nuggets, using the smart model tier. Incremental: skips nuggets that already have a suggestion, and commits results per chunk so a mid-run failure keeps earlier matches. Requires `llm.api_key`. |
| `inbox_suggest` | AI-propose new tasks that group clusters of unmatched inbox nuggets; the proposals are stored as `NuggetSuggestion` rows for the GUI to review until dismissed or replaced by a later run. Considers the 200 most recently seen nuggets, matched in bounded chunks (a chunk whose reply can't be parsed is skipped, not fatal). Requires `llm.api_key`. |
| `housekeeping` | Data-integrity sweep: additive parent-label propagation through the task tree, plus pruning of expired job logs. |

`daily_report`, `what_next`, and `jira_sync` still work without an LLM API key;
the LLM narrative is simply skipped. `notes_scan`, `nuggets_match`, and
`inbox_suggest` require one.

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

## Live logs

Every run records an append-only log of `JobLogEntry` rows as it executes —
steps, errors, and per-call LLM inference detail (which model, prompt/token
sizes, timing, and a truncated prompt preview). The **Jobs** page streams this
log to every open browser session in real time (SSE) for any running or
finished job; the Inbox *Scan notes*, *Recompute*, and *Generate suggestions*
actions stream the same way.

- **Jobs page** — a "Running" banner lists in-flight runs; the *Log* column
  links to each run's log; *run now* opens the live view inline.
- **Indicators** — runs with recorded history show `view (N)`; runs without
  show `—`. A spinner marks a pending LLM ("waiting for …") line.
- **Retention** — logs are pruned after `config.jobs.log_retention_days` days
  by the `housekeeping` job (default 30; `0` keeps them forever). A run's log,
  or all logs, can also be cleared from the GUI or API.

## Inspecting

- `ewo jobs list` — registered job names.
- `ewo jobs runs` — recent runs with status, start time, tokens used, result,
  and a `log=N` count when a run has history.
- `ewo jobs log <run-id>` — one run's live log, prettified.
- `GET /api/jobs`, `GET /api/jobs/runs`, `GET /api/jobs/runs/{id}/log`.

See [What next](what-next.md) for the reports these jobs produce, and
[Configuration](config.md) for `jobs.schedules` and the log-retention knobs.