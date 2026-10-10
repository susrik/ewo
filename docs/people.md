# People & 1:1s

**People** are your team members. They are not login accounts — there is no
email or auth. One row (`is_self`) represents you.

## Managing people

- `ewo person add "Name" [--jira-account-id …] [--discord-user-id …]
  [--notes-dir …] [--alias …] [--self]`
- `ewo person list`
- `ewo person seed` — create one person per team folder in the notes tree
  (also *Seed from notes* on the People page).

A person can carry links to their notes folder, Jira account, and Discord
user. **Aliases** record other spellings used in notes.

## Attribution in notes

When the notes are scanned, items are attributed (see [Inbox](inbox.md)):

1. By the folder the note lives in (your `Person.notes_dir` wins).
2. Then by name/alias. An ambiguous alias (two people both called "James")
   resolves to nobody rather than guessing.

## 1:1 agendas

Create a 1:1 for a person and add discussion topics; the agenda carries open
topics forward between meetings.

```sh
ewo oneonone create <person_id> <scheduled_for>
ewo oneonone topic <meeting_id> "topic"
ewo oneonone agenda <meeting_id>   # print the agenda markdown
```