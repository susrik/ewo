# Getting started

ewo is a personal task/project manager built around one FastAPI service. A web
GUI, a `click` CLI, a Discord listener, and an MCP server all talk to the same
HTTP API, so data is always in one place.

What it does, at a glance:

- **Tasks** — coherent topics with priority, dates, assignee, tags, subtasks,
  notes, and links to Jira issues.
- **What next** — a live ranking of open work so you know what to pick up.
- **Notes → nuggets → tasks** — ewo *reads* your existing markdown notes
  (never edits them) and turns outstanding actions, questions, deadlines, and
  risks into an inbox for review.
- **People & 1:1s** — your team, with 1:1 agendas that carry open topics forward.

See [Core concepts](concepts.md) for the vocabulary.

## Install & run (local)

```sh
source ~/.virtualenvs/ewo/bin/activate
pip install -e ".[dev]"
pip install uvicorn          # runtime-only; deliberately not a package dep

cp ewo.example.json ewo.json # then edit: database.url, credentials, etc.
alembic upgrade head
python -m ewo.server         # host/port from ewo.json (default 0.0.0.0:8000)
```

Open <http://localhost:8000> for the web GUI. The dashboard shows a board,
the "what next" ranking, upcoming deadlines, and your people.

## Run with Docker

```sh
cp ewo.example.json ewo.json   # docker-ready defaults: db@5432, data_dir=/data
docker compose up --build
```

Postgres 17 + the server; the entrypoint runs `alembic upgrade head` first.

## First walkthrough

1. **Seed your team.** If your notes have a folder per person, run
   `ewo person seed` or use *Seed from notes* on the People page. Mark
   yourself: `ewo person add "You" --self`.
2. **Create a task** on the [Tasks](tasks.md) page (or `ewo task add "…"`).
3. **Optional — enable notes.** Set `notes.enabled: true` and `notes.root` in
   `ewo.json` (plus a valid `llm.api_key`), then scan the notes into your
   [inbox](inbox.md).
4. **Focus** with *What next* or `ewo whatnext`.

Every CLI command accepts `--config <path>`; the server address comes from
`api_base_url` in the config. See [Configuration](config.md) for all settings.