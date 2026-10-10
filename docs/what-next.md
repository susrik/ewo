# What next

**What next** is a live answer to "what should I focus on right now?". It ranks
open work by priority, deadlines, blocked state, whether it's yours or
delegated, age, and recent mention in your notes — plus a little daily jitter
for ties — and can append an LLM narrative.

See it:

- On the dashboard (home page).
- `ewo whatnext [--llm]` (CLI; `--llm` adds the narrative).
- `ewo_what_next` (MCP).

## Reports

Two generated reports summarize where things stand:

- **daily** (`daily_report` job) — a daily markdown digest.
- **what-next** (`what_next` job) — the current "what next" ranking.

Reports are stored in the database (`GET /api/reports`, `ewo`/GUI can list
them) and, when `reports_repo` is configured, published to a dedicated GitHub
repo at `<folder>/YYYY-MM/YYYY-MM-DD-HHMM-<type>.md`. The LLM narrative is
skipped when no API key is configured, but the report still works.

See [Jobs](jobs.md) for scheduling, and [Integrations](integrations.md) for the
reports repo and Google email delivery.