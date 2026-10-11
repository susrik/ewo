# Core concepts

Short glossary of ewo's building blocks.

## Task
A coherent piece of work **topic**. Fields: title, priority
(`critical|high|normal|low`), start/due dates, an assignee, tags, notes, and
external links (Jira issues). When a task is marked done, its `completed_at`
is recorded automatically. Tasks form a **single-parent tree** of subtasks at
any depth. See [Managing tasks](tasks.md).

## Tag (label)
Tags are how you label/categorize tasks. Tags can carry an optional
description. A new subtask inherits its parent's tags (additive). See
[Organize (AI)](organize.md) for how tags propagate through a tree.

## Nugget
A unit of outstanding work discovered in your notes: an action, question,
deadline, or risk, cited by `path:line`. Nuggets land in the **inbox** for
review across three views. See [Inbox](inbox.md).

## Inbox
Where unreviewed nuggets wait. For each you can **attach** to a task (existing
or new), **dismiss**, or mark **already done**. Reviewed nuggets are never
re-surfaced.

## Person
A team member (not a login account — no email, no auth). One `is_self` row
represents you, the owner. A person can be linked to a notes folder
(`notes_dir`), a Jira account, and a Discord user. See [People](people.md).

## 1:1
A recurring one-on-one meeting whose agenda carries open topics forward.
See [People](people.md).

## What next
A live ranking of open work by priority, deadlines, blocked state, ownership,
age, and recent mention in notes — with an optional LLM narrative. See
[What next](what-next.md).

## Job
A named background unit of work (`jira_sync`, `daily_report`, `what_next`,
`notes_scan`, `nuggets_match`, `housekeeping`) that runs on a schedule or on
demand. Every run is recorded. See [Jobs](jobs.md).

## Report
Generated markdown (daily / what-next) published to a dedicated GitHub repo and
stored in the database. See [What next](what-next.md) and
[Integrations](integrations.md).