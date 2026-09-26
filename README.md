# ewo

Personal task management for an engineering manager: one FastAPI service with
an htmx web GUI, a `click` CLI, a Discord listener and an MCP server, all thin
clients of the same `/api`.

- **Tasks** are coherent topics: priority, start/due dates (completion is
  auto-recorded), assignee, tags, sub-tasks (single-parent tree), notes and
  external links (Jira issues — many per task and shared across tasks, notes
  citations).
- **What next** — a live ranking of open work (priority, deadlines, blocked,
  mine vs delegated, age, recent mentions in notes, a little daily jitter for
  ties) with an optional LLM narrative.
- **Notes → nuggets → tasks** — reads a markdown notes tree (read-only), asks
  the LLM for outstanding actions/questions/deadlines/risks with `path:line`
  citations, and queues them as *nuggets* in the inbox, pre-grouped by their
  suggested task. Attach to an existing task or a new one (Jira keys in the
  note become task links), dismiss, or mark "already done" without touching
  the note. An *Organize (AI)* button proposes task merges/splits/creates.
- **People** are team members linked to their notes folder; **1:1** agendas
  carry open topics forward.
- **Jobs** (`jira_sync`, `daily_report`, `what_next`, `notes_scan`,
  `nuggets_match`) run on a cron schedule or on demand; every run is recorded.

See `docs/setup.md` for installation and configuration, `AGENTS.md` for the
architecture rules.
