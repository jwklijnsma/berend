# Sources

| Source | Setting | Notes |
|---|---|---|
| Local files | `LOCAL_DATA_DIRS` | md, txt, rst, adoc, html, pdf, yaml |
| Docs website | `DOCS_SITE_URLS` | crawls same host + path prefix, up to `DOCS_MAX_PAGES` |
| GitLab | `GITLAB_URL`, `GITLAB_TOKEN`, `GITLAB_PROJECTS` | doc files and wikis indexed; live search for issues, MRs, code and commits. `read_api` token |
| Argo CD | `ARGOCD_URL`, `ARGOCD_TOKEN` | live app status, resource tree and events |
| Web | `WEB_SEARCH_PROVIDER` | `searxng` / `brave` / `google` / `none` |

Google's Custom Search JSON API is closed to new customers and stops on 1 January 2027, so
`google` only works with existing keys. Self-hosted SearXNG can still use Google as an upstream
engine. For sensitive environments, use `none` or restrict `FETCH_ALLOWED_DOMAINS`.

Everyone who can talk to Berend can read everything in the index, so only index what all of
them may see. `SLACK_ALLOWED_CHANNELS` and `MATTERMOST_CHAT_CHANNELS` limit where he answers.

The name comes from `AGENT_NAME` (default `Berend`). It sets how the agent introduces himself,
the chat footers and the Mattermost poster name.
