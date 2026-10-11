# Inbox

The inbox turns your existing markdown notes into a review queue of work. ewo
**reads** a tree of notes (it never writes into it), asks an LLM for the
outstanding actions, questions, deadlines, and risks — each with a `path:line`
citation — and queues them as **nuggets**. The inbox presents them in three
views (tabs): **Review**, **Suggested tasks**, and **Suggested new tasks**.

## The pipeline

1. **People** — each team member has a folder in the notes tree; `ewo person
   seed` (or *Seed from notes*) creates a person per folder with `notes_dir`
   set. First-person items are attributed to the person marked `--self`.
2. **Scan** — the `notes_scan` [job](jobs.md) reads every note changed since
   the last successful scan (or, with `--full`, everything in the window) and
   extracts nuggets.
3. **Review** — nuggets land in the Inbox (`/inbox`, `ewo inbox list`,
   `GET /api/nuggets`), shown in three tabs. Aside from **Review**, each tab is
   based on AI suggestions, described below.

## Views and filters

The inbox has three mutually-exclusive **views**, selected with a tab strip at
the top of the Inbox card (the active tab is kept in the URL, so refreshing or
sharing a link restores it):

- **Review** — every nugget matching the current filters, in one flat list,
  with the bulk toolbar above it (when the status is `new`).
- **Suggested tasks** — nuggets grouped under the existing task that
  `nuggets_match` matched them to, one panel per task.
- **Suggested new tasks** — the AI's proposed brand-new tasks (`NuggetSuggestion`
  rows), one editable panel per proposal.

The **status** and **owner** dropdowns filter every view; the **search** box
filters the currently shown nuggets client-side and reports a *showing N of M*
hint. Each of the three views is colour-coded so panels read clearly apart
(purple review, blue suggested tasks, teal suggested new tasks); change the
`--theme-review` / `--theme-suggested` / `--theme-new` tokens in
`ewo/server/static/theme.css` to re-theme a whole view.

### Selection and bulk actions

Each new nugget has exactly **one** selection checkbox, used regardless of the
active view. Nuggets ticked in one view stay ticked when you switch views, and
an always-visible **N selected** count (top right of the filter row) keeps the
selection in sight. Actions always apply only to the nuggets you can currently
see + have selected — a hidden or other-view nugget is never touched.

- **Review** (`new` status): a purple bulk toolbar with *Select all*, *Attach to
  task…* (pick an existing task, or *＋ New task* to make one from the
  selection), *Already done*, and *Dismiss* — applied to every selected nugget.
- **Suggested tasks**: each task panel has its own *Select all/none*, an
  *Attach* button (attach that group's selected nuggets to the task), *Already
  done*, *Dismiss*, and a *x of y selected* count.
- **Suggested new tasks**: each proposal panel has its own *Select all/none*,
  *Create and Attach* (create the task and attach its selected nuggets),
  *Already done*, *Dismiss*, and a *x of y selected* count.

Filtering drops a nugget from view and from the selection (so bulk/group
actions never affect it), mirroring the rule that bulk operations only ever
touch nuggets that are still **new** (unattached).

## Review actions

Each nugget row also offers its own **Attach to task…** picker (choose an
existing task, or create a new one `source=notes` with the nugget as a
`notes:<path>:<line>:<id>` citation), **Already done**, and **Dismiss**;
Jira keys mentioned in the note become task links. Reviewed nuggets are never
re-surfaced by later scans; re-seen open nuggets bump `last_seen_at`, nudging
their linked task up in *What next*.

## Suggest new tasks

The **Suggested new tasks** tab asks the `inbox_suggest` [job](jobs.md) to
propose tasks that group clusters of *unmatched* nuggets. Press **Generate
suggestions** (or **Regenerate** once proposals exist) to stream a live log
while the LLM runs; the proposals then appear for review — nothing is created
until you edit, tick the nuggets, and confirm. See [Jobs](jobs.md#live-logs)
for the streaming, and [Organize](organize.md) for the underlying stateless
proposal logic. The **Suggested tasks** tab relies on `nuggets_match`, which
runs automatically at the end of every scan (its *Recompute* button re-runs it
on demand).

The proposals are **kept** once generated, so you can work through several
groups without re-running the LLM:

- Each proposal panel is an editable draft: a **title** and a **description**
  (one paragraph written by the LLM), committed with **Create and Attach**,
  which creates the task and attaches the selected nuggets.
- Selected nuggets can also be dismissed / marked done in bulk (*Already done*,
  *Dismiss*), and a proposal — or the whole set — can be **discarded**.
- Running *Generate* again replaces the previous set.
- The tab always reflects the current inbox: nuggets you attach, dismiss, or
  mark done elsewhere (including from the other views) drop out of a proposal
  and are never overwritten or undone. A proposal disappears once none of its
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