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
- **Theme token format mismatch (hex vs OKLCH)** — `theme.css` recolours
  daisyUI's theme tokens with hex values, but daisyUI's prebuilt `full.min.css`
  derives some colours as `oklch(var(--token)/α)` (checkbox/radio/toggle/range
  fills, focus outlines). On oklch-capable browsers that expression is invalid,
  so those components silently lose their colour. Checkboxes are already
  worked around with explicit `:checked`/`:indeterminate` rules in `theme.css`;
  radio, toggle, range and focused outlines are still affected. Longer-term fix
  is to express the theme tokens in OKLCH, or define daisyUI's `--fallback-*`
  variables, rather than adding more component-level overrides.