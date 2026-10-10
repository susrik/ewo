# ewo

Personal task management for an engineering manager: one FastAPI service with
an htmx web GUI, a `click` CLI, a Discord listener and an MCP server, all thin
clients of the same `/api`.

- **Tasks** — coherent topics: priority, start/due dates (completion is
  auto-recorded), assignee, tags, sub-tasks (single-parent tree), notes, and
  external links (Jira issues).
- **What next** — a live ranking of open work, with an optional LLM narrative.
- **Notes → nuggets → tasks** — reads a markdown notes tree (read-only), pulls
  out outstanding actions/questions/deadlines/risks with `path:line` citations,
  and queues them as *nuggets* for review.
- **People & 1:1s** — your team, with agendas that carry open topics forward.
- **Jobs** — `jira_sync`, `daily_report`, `what_next`, `notes_scan`,
  `nuggets_match`, `housekeeping`; scheduled or on demand, every run recorded.

## Documentation

Start here: **[docs/index.md](docs/index.md)**.

- [Getting started](docs/getting-started.md) — install, run, first walkthrough
- [Core concepts](docs/concepts.md) — the vocabulary
- [Jobs](docs/jobs.md) — the background jobs and when they run
- [HTTP API](docs/api.md) — endpoint reference

For maintainers and agents, see [`AGENTS.md`](AGENTS.md).