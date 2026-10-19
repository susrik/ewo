# HTTP API

Two routers make up the service. Every JSON body in or out has a pydantic model
in `ewo/server/schemas.py`.

- **`/api`** — user-facing endpoints (CLI, listeners, MCP, external callers).
- **`/gui`** — GUI-only endpoints, returning htmx fragments.

## `/api` endpoints

### People

| method | path | description |
|---|---|---|
| POST | `/api/people` | create a person |
| GET | `/api/people` | list people |
| GET | `/api/people/{id}` | get one person |
| PATCH | `/api/people/{id}` | update a person |
| DELETE | `/api/people/{id}` | delete a person |
| POST | `/api/people/seed-from-notes` | create people from notes folders |

### Tasks, notes, tags

| method | path | description |
|---|---|---|
| POST | `/api/tasks` | create a task |
| GET | `/api/tasks` | list tasks (`assignee_id`, `include_closed`, tag filters) |
| GET | `/api/tasks/{id}` | get one task |
| PATCH | `/api/tasks/{id}` | update a task |
| DELETE | `/api/tasks/{id}` | delete a task |
| POST | `/api/notes` | create a note |
| GET | `/api/tags` | list tags |
| PATCH | `/api/tags/{id}` | update a tag |
| DELETE | `/api/tags/{id}` | delete a tag |
| POST | `/api/tasks/{id}/links` | add an external (Jira) link |
| DELETE | `/api/tasks/{id}/links/{link_id}` | remove a link |
| GET | `/api/tasks/{id}/nuggets` | list attached nuggets |

### Nuggets (inbox)

| method | path | description |
|---|---|---|
| GET | `/api/nuggets` | list nuggets |
| PATCH | `/api/nuggets/{id}` | update a nugget |
| POST | `/api/nuggets/{id}/attach` | attach to a task (or create one) |
| POST | `/api/nuggets/{id}/move` | move to another task |
| POST | `/api/nuggets/{id}/detach` | detach back to inbox |
| POST | `/api/nuggets/{id}/dismiss` | dismiss |
| POST | `/api/nuggets/{id}/done` | mark already done |

### 1:1s, what-next, reports, jobs

| method | path | description |
|---|---|---|
| POST | `/api/one-on-ones` | create a 1:1 |
| GET | `/api/one-on-ones` | list 1:1s |
| POST | `/api/one-on-ones/{id}/agenda` | add an agenda topic |
| PATCH | `/api/agenda-items/{id}` | update an agenda item |
| GET | `/api/one-on-ones/{id}/agenda.md` | agenda markdown |
| GET | `/api/whatnext` | what-next ranking (`use_llm` flag) |
| GET | `/api/reports` | list reports |
| GET | `/api/reports/{id}` | report detail |
| GET | `/api/jobs` | list registered jobs |
| POST | `/api/jobs/{name}/run` | run a job (`full`, `wait` query params) |
| GET | `/api/jobs/runs` | recent job runs (`log_count` on each) |
| GET | `/api/jobs/runs/{id}/log` | one run's log entries |
| DELETE | `/api/jobs/runs/{id}/log` | clear one run's log |
| DELETE | `/api/jobs/logs` | clear all job logs |

## `/gui` endpoints

Pages: `/`, `/tasks`, `/inbox`, `/people`, `/jobs`, `/labels`. Fragments are
the `_*.html` templates htmx swaps in. Notable groups:

- `/gui/tasks` CRUD, filters, focus, search, reparent, merge, links, notes,
  children, status, detach.
- `/gui/tasks/organize/*` — Organize (AI): `merge`, `split`, `create`,
  `retitle`, `apply`, `topic`, `group`, `related`, `split/propose`,
  `split/apply`.
- `/gui/inbox*`, `/gui/nuggets/{id}/*` — inbox review and nugget actions.
- `/gui/people*`, `/gui/labels*`, `/gui/one-on-ones`, `/gui/jobs/*`.
- `/gui/jobs/{id}/events` — SSE stream replaying then live-tailing a run's log
  (see [Jobs](jobs.md#live-logs)).

Behavior details live in [Architecture](architecture.md) and the per-topic
guides; this file is the route reference.