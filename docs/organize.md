# Organize (AI)

The AI-assisted tools that propose structure changes to your tasks. Nothing is
persisted until you confirm a proposal — the underlying service layer computes
*stateless* proposals and only applies the confirmed set.

## Organize (AI) — merge / split / create / retitle

The **Organize (AI)** button on the Tasks page asks the smart model for
merge/split/create/retitle proposals. Confirm a proposal to apply it.

## Group topic (AI)

The "Group topic (AI)" form on the Tasks page takes a free-form topic, finds
related open tasks (validated to open ids, capped at 20, at least 2 required),
and proposes re-parenting the confirmed set under exactly one parent — a new
task (`source=manual`) or an existing one. Self-parenting and descendant cycles
are rejected up front.

## Split (AI)

The **Split (AI)** form in a task's edit panel proposes splitting one task into
parts from free-form instructions. You check the parts to apply; the proposal
is re-validated against the currently attached nuggets before anything changes.

## Related (AI)

After creating a task, a "Find related" banner can propose grouping it with
related work, with one optional Q&A round before a topic-group proposal.

## Tag inheritance

Tags are additive through the task tree:

- `create_task` unions the parent's tags into a new child.
- Adding a tag to a task with descendants offers to propagate it downward
  (append-only); tag **removal** is never propagated.
- The `housekeeping` [job](jobs.md) sweeps the tree roots-first and additively
  applies missing parent labels to descendants — it never removes tags.

These all require the LLM to be configured (`llm.api_key`). See
[Configuration](config.md).