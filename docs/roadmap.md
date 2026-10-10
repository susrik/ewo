# Roadmap

Known gaps and future work. Listed here so they don't get re-discovered.

- **Jira Server/DC support** — `HttpJiraClient` speaks the Jira **Cloud** v3
  API (`/rest/api/3/search/jql`, `nextPageToken`, `accountId`). The configured
  instance is Jira **Server/DC** 9.x, which needs `/rest/api/2/search?startAt=`,
  Bearer PAT auth, and `assignee.name` — so every scheduled `jira_sync`
  currently fails with 403.
- **Docker read-only + tmpfs** — enable `read_only: true` + `tmpfs` in
  docker-compose (verify no overlay writes).
- **Slack read-only listener**.
- **Meeting-minutes archive integration**.
- **Overview document** — the dated `overviews/*.md` snapshot generated from
  the inbox + tasks and published to the reports repo.