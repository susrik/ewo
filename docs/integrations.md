# Integrations

ewo talks to Jira, Google, Discord, and a reports GitHub repo — all read-only or
send-only integrations behind small interfaces. See [Configuration](config.md)
for the matching config fields.

## Jira

Create a read-only API token, set `jira.base_url` / `email` / `api_token`, and
a JQL covering the projects to track. The `jira_sync` [job](jobs.md) creates
tasks for new issues and keeps linked task status/assignee in step with Jira.
Map people to Jira via `ewo person add "Anna" --jira-account-id <accountId>`.

> Known gap: the Jira client targets the Cloud v3 API, but the configured
> instance is Jira Server/DC 9.x — see [Roadmap](roadmap.md).

## Google (one-time)

1. Create an OAuth client (Desktop) on a dedicated Google account; publish the
   consent screen "In production" so refresh tokens don't expire weekly.
2. Put `client_id`/`client_secret` in `ewo.json`.
3. Run `ewo google auth` — the token is stored in the database and refreshed
   automatically from then on.

Google is used to send the daily report email (`email_to`) and read the
calendar.

## Reports repo

Create a dedicated GitHub repo + a PAT with repo write, then set
`reports_repo.{repo_url,token,…}`. Reports land at
`<folder>/YYYY-MM/YYYY-MM-DD-HHMM-<type>.md` and are also stored in the DB.

## Discord

Create a bot (message content intent enabled), set `discord.token`, optionally
restrict to `discord.channel_id`, and set `discord.enabled: true`. Commands:

- `!task <title> [@person] [#tag] [!priority]`
- `!done <id>`
- `!tasks [@person]`
- `!next`
- `!help`

## MCP

- **stdio (opencode)** — register `python -m ewo.mcp.server` with env
  `EWO_CONFIG_FILENAME=/path/to/ewo.json` (the ewo server must be running).
- **HTTP service** — set `mcp.http_enabled: true` (+ `mcp.http_port`) and run
  `python -m ewo.mcp.server` as a service.

Tools: `ewo_list_tasks`, `ewo_create_task`, `ewo_set_task_status`,
`ewo_what_next`, `ewo_run_job`, `ewo_inbox_list`, `ewo_inbox_attach`,
`ewo_inbox_resolve`.