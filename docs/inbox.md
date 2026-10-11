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

## Filtering and bulk actions

New nuggets (the `new` view) can be handled in bulk:

- **Filter** — a search box filters the visible nuggets by case-insensitive
  match on the nugget title or note text. Filtering happens in the browser, so
  it only ever affects what is currently shown.
- **Select** — tick nuggets individually, or use *Select all* to tick every
  visible nugget (uncheck it to clear the selection).
- **Bulk actions** — apply **Attach to task…** (an existing task via the task
  picker, or *＋ New task* to create one task from all selected), **Already
  done**, or **Dismiss** to every selected nugget.

Selection and bulk actions apply only to nuggets currently visible after
filtering; hidden nuggets are never affected. The per-task *Attach selected*
shortcut (when a suggested task groups several nuggets) remains available
alongside the general selection.

When a suggested task groups several nuggets, two checkboxes sit side by side
on each row; they're colour-coded (with a legend above the bulk toolbar) so
they aren't confused:

- the **blue** checkbox — *Include in the group attach* — controls which
  nuggets the per-task *Attach selected to #N* shortcut attaches;
- the **purple** checkbox — *Select for bulk actions* — is the general
  selection, feeding the purple bulk toolbar (*Select all*, *Attach to task…*,
  *＋ New task*, *Already done*, *Dismiss*).

Both colours come from the inbox selection tokens in
`ewo/server/static/theme.css` — `--select-attach` (blue) and `--select-bulk`
(purple). Change those two variables to re-theme either workflow; the purple
bulk-toolbar tint is derived from `--select-bulk`, so it follows automatically.

After any bulk operation the filter string is kept, the list stays filtered to
it, and the "showing *M* of *N*" hint reflects the nuggets still matching.
Bulk operations only ever touch nuggets that are still **new** (unattached) —
a nugget you have since attached, dismissed, or marked done is never
re-processed, and the selection count omits it.

## Suggest new tasks

"Suggest new tasks" (Inbox) asks the `inbox_suggest` [job](jobs.md) to propose
tasks that group clusters of *unmatched* nuggets. It streams a live log while
the LLM runs, then shows the proposals for review — nothing is created until
you tick the nuggets and confirm. See [Jobs](jobs.md#live-logs) for the
streaming, and [Organize](organize.md) for the underlying stateless proposal
logic.

The proposals are **kept** once generated, so you can work through several
groups without re-running the LLM:

- Each proposal can be turned into a task at a time (*Create task*), or its
  matched nuggets dismissed / marked done in bulk (*Already done*, *Dismiss*).
- The panel can be **collapsed** to get it out of the way (the state is
  remembered), and each proposal — or the whole panel — can be **dismissed**.
- Running "Suggest new tasks" again replaces the previous set.
- The panel always reflects the current inbox: nuggets you attach, dismiss, or
  mark done elsewhere (including from the main list) drop out of a proposal and
  are never overwritten or undone. A proposal disappears once none of its
  nuggets remain.

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