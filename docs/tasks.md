# Managing tasks

Tasks live on the **Tasks page** (`/tasks`). This covers the everyday
browse/manage workflow for basic users. See [Core concepts](concepts.md) for
what a task is.

## Creating and editing

Create a task from the Tasks page or the CLI:

```sh
ewo task add "Title" --assignee-id 2 --priority high --tag ops \
  --start 2026-10-10 --due 2026-11-01 --parent-id 3
```

Open a task's **detail panel** to edit its title, priority, dates, assignee,
tags, description, notes, children (subtasks), and external (Jira) links.
Changing status to done records `completed_at` automatically.

## The tree

Tasks form a single-parent tree: use **parent** to nest subtasks at any depth.
A new child inherits its parent's tags. One Jira issue can be linked from many
tasks; links are unique per task.

## Filtering and sorting

The task list filters by status, assignee, tag, source, and open/closed. The
multi-label filter matches **any** selected label by default, or **all** of
them. You can sort by priority and a secondary field. Your filter selection is
remembered.

## Labels

Labels = tags (see [Core concepts](concepts.md)). Manage them on the
**Labels page** (`/labels`), where each can have a description. Adding a tag to
a task that has descendants offers to propagate it down the tree — see
[Organize (AI)](organize.md).