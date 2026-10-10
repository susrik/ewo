# CLI reference

`ewo` is a `click` CLI that speaks the HTTP `/api`. Every command accepts
`--config <path>` (default `ewo.json` or `$EWO_CONFIG_FILENAME`); the server
address comes from `api_base_url`.

## Tasks

```sh
ewo task add <title> [--assignee-id N] [--parent-id N] [--priority P]
  [--tag T]* [--start YYYY-MM-DD] [--due YYYY-MM-DD]
ewo task list [--assignee-id N] [--all]
ewo task done <id>
```

## People

```sh
ewo person add <name> [--jira-account-id …] [--discord-user-id …]
  [--notes-dir …] [--alias …]* [--self]
ewo person list
ewo person seed
```

## Inbox

```sh
ewo inbox list [--status new|attached|dismissed|already_done|all]
ewo inbox attach <id> [--task-id N] [--priority P] [--assignee-id N] [--due DATE]
ewo inbox dismiss <id>
ewo inbox done <id>
```

## 1:1s

```sh
ewo oneonone create <person_id> <scheduled_for>
ewo oneonone topic <meeting_id> <topic>
ewo oneonone agenda <meeting_id>
```

## What next, jobs, google

```sh
ewo whatnext [--llm]

ewo jobs list
ewo jobs run <name> [--full] [--timeout S] [--no-wait]
ewo jobs runs

ewo google auth     # one-time OAuth consent; stores token in the DB
```

See [Jobs](jobs.md) for job details, [Inbox](inbox.md) for the review workflow,
and [Integrations](integrations.md) for Google setup.