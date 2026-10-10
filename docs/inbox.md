# Inbox

The inbox turns your existing markdown notes into a review queue of work. ewo
**reads** a tree of notes (it never writes into it), asks an LLM for the
outstanding actions, questions, deadlines, and risks — each with a `path:line`
citation — and queues them as **nuggets**, pre-grouped by a suggested task.

## The pipeline

1. **People** — each team member has a folder in the notes tree; `ewo person
   seed` (or *Seed from notes*) creates a person per folder with `notes_dir`
   set. First-person items are attributed to the person marked `--self`.
2. **Scan** — the `notes_scan` [job](jobs.md) reads every note changed since
   the last successful scan (or, with `--full`, everything in the window) and
   extracts nuggets.
3. **Review** — nuggets land in the Inbox (`/inbox`, `ewo inbox list`,
   `GET /api/nuggets`), grouped under their suggested task.

## Review actions

For each nugget:

- **Attach** — link it to an existing task, or create a new task
  (`source=notes`) with the nugget as a `notes:<path>:<line>:<id>` citation.
  Jira keys mentioned in the note become task links.
- **Dismiss** — drop it (not actionable).
- **Already done** — record that the note is stale (the note itself is never
  modified).

Reviewed nuggets are never re-surfaced by later scans; re-seen open nuggets
bump `last_seen_at`, nudging their linked task up in *What next*.

## CLI

```sh
ewo inbox list                         # new | attached | dismissed | already_done | all
ewo inbox attach <id> [--task-id N]    # attach, or create a new task
ewo inbox dismiss <id>
ewo inbox done <id>
```

Suggestion quality depends on the `nuggets_match` job, which also runs at the
end of every scan. See [Jobs](jobs.md) for how and when scans run, and
[Configuration](config.md) for the `notes.*` and `llm.*` settings.