# ewo

Personal task management for an engineering manager: one FastAPI service with
an htmx web GUI, a `click` CLI, a Discord listener and an MCP server, all thin
clients of the same `/api`.

- **Tasks** with priority, due date, assignee (team member), tags, notes and
  external links (Jira, notes citations).
- **What next** — a live ranking of open work (priority, deadlines, blocked,
  mine vs delegated, age, recent mentions in notes, a little daily jitter for
  ties) with an optional LLM narrative.
- **Notes → inbox → tasks** — reads a markdown notes tree (read-only), asks the
  LLM for outstanding actions/questions/deadlines/risks with `path:line`
  citations, and queues them for review. Accept to track, dismiss, or mark
  "already done" without touching the note.
- **People** are team members linked to their notes folder; **1:1** agendas
  carry open topics forward.
- **Jobs** (`jira_sync`, `daily_report`, `what_next`, `notes_scan`) run on a
  cron schedule or on demand; every run is recorded.

See `docs/setup.md` for installation and configuration, `AGENTS.md` for the
architecture rules.
