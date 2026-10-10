# ewo documentation

Personal task/project management for an engineering manager and their team.
One FastAPI service owns all data; the CLI, web GUI, Discord listener, and MCP
server are thin clients of the same HTTP API.

Pick a starting point by audience:

## Basic users
Non-developers who browse and manage tasks, people, and the inbox.

- [Getting started](getting-started.md) — install, run, first walkthrough
- [Core concepts](concepts.md) — tasks, subtasks, tags, nuggets, people, 1:1s
- [Managing tasks](tasks.md) — the day-to-day task workflow
- [Inbox](inbox.md) — reviewing work discovered in your notes
- [People](people.md) — your team and 1:1 agendas

## Advanced users
Non-developers who want the power features: inference, jobs, integrations.

- [What next](what-next.md) — the live ranking of what to focus on
- [Organize (AI)](organize.md) — AI task organization and tag inheritance
- [Jobs](jobs.md) — the background jobs and when they run
- [CLI reference](cli.md) — every `ewo` command
- [Configuration](config.md) — the `ewo.json` reference
- [Integrations](integrations.md) — Jira, Google, Discord, MCP, reports

## Developers
Engineers extending or understanding the codebase.

- [Architecture](architecture.md) — code layout and module responsibilities
- [Development](development.md) — environment, quality gates, testing rules
- [HTTP API](api.md) — the `/api` and `/gui` endpoint reference
- [Roadmap](roadmap.md) — known gaps and future work