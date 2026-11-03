# Berend

Berend is a local-LLM (Ollama) assistant for a platform team:

1. **Q&A in Slack and Mattermost.** He answers from your local files, docs website, GitLab
   (indexed docs plus live issues, MRs and code), live Argo CD status and the web, with sources.
2. **Argo CD alerts in Mattermost.** Colour-coded alerts when apps degrade, fail to sync or
   drift. Each alert shows the Kubernetes events behind it, and Berend writes the likely cause
   and next steps from your runbooks. You can ask follow-ups right under the alert:
   *"@berend what changed in this revision?"*

```
                ┌──────────────────────── berend.main ─────────────────────────┐
Slack      ◄───►│ slack_bot ─┐                                                 │
Mattermost ◄───►│ mattermost_bot ─► chat ─► agent (Ollama: streaming, tools)    │
                │                            ├ search_knowledge_base ◄── hybrid index (Chroma vectors + BM25)
                │                            │                            ◄── ingest: local · docs site · GitLab
                │                            ├ search_gitlab   (live)        │
                │                            ├ argocd_apps     (live, events)│
                │                            ├ web_search      (SearXNG/Brave)
                │                            └ fetch_url                     │
Argo CD API ───►│ argocd monitor ─► alert + events ─► Mattermost alerts channel │
                │ /healthz /readyz /metrics                                    │
                └──────────────────────────────────────────────────────────────┘
```

Every part is optional. `python -m berend.main` starts whatever is configured.

## Quick start

```bash
ollama pull qwen2.5:14b && ollama pull nomic-embed-text
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                     # fill in what you use

python -m berend.ingest                  # build the index (incremental; --reset to rebuild)
python -m berend.agent "Which Argo CD apps are broken and why?"   # test without chat
python -m berend.argocd --once           # test the Argo CD connection
python -m berend.main                    # run it all
pip install -r requirements-dev.txt && pytest    # 29 tests, no external services needed
```

## Answers

- **Hybrid search.** Vector search finds the same thing phrased differently. A BM25 keyword
  index finds exact tokens that embeddings blur: `ImagePullBackOff`, `cert-manager`, hostnames,
  ticket numbers. The two rankings are merged with reciprocal rank fusion.
- **Heading-aware chunks.** Markdown is split per section, and each chunk starts with its path,
  e.g. `[Keycloak > Operations > Key rotation]`, so a lone paragraph keeps its context.
- **Only cited sources.** The links under an answer are the documents Berend actually named.
- **Threads.** Follow-ups in a thread Berend answered need no new @mention, in both Slack and
  Mattermost. The whole thread is context, and in an alert thread that includes the alert.
- **Feedback.** Slack has 👍/👎 buttons; in Mattermost you react 👍/👎. Votes go to
  `FEEDBACK_LOG` (JSONL) and the `berend_feedback_total` metric, so you can see where the docs fall short.

## Argo CD alerts

| Situation | What Berend posts |
|---|---|
| App breaks | 🔴/🟠 alert: failing resources and pods, top **Kubernetes warning events** (e.g. `Failed: ... unauthorized`), link to Argo CD. In the thread: 🤖 likely cause and next steps from your runbooks |
| It changes | ↪️ in the thread; the alert updates |
| Still broken after `ALERT_REMIND_HOURS` | ⏰ reminder in the thread |
| It recovers | ✅ in the thread; the alert turns green |
| ≥ `ARGOCD_STORM_THRESHOLD` apps break in one poll | **one** grouped alert. The AI looks for the common cause (cluster, registry, operator, cert). Recoveries tick off in its thread |
| Berend restarts | nothing (state is kept); a fresh install posts one "watching N apps" summary |

**Muting from Git.** Put `berend.io/mute: "true"` on an Application, or an end time such as
`"2026-11-01T08:00:00+01:00"` for a maintenance window. Use `ARGOCD_IGNORE_APPS` (regexes)
for sandboxes and previews.

**Severity.** 🔴 Degraded, Missing, or a failed sync. 🟠 Unknown, OutOfSync, Progressing longer
than `ARGOCD_PROGRESSING_MINUTES`, or `*Error` conditions. A state must hold for
`ARGOCD_CONFIRM_POLLS` polls before it is posted.

**Pod logs** (`ARGOCD_FETCH_LOGS=true`) give the AI the last log lines of failing pods. This is
off by default: it needs `logs, get` in Argo CD RBAC, and log lines can contain data you don't
want repeated in a channel.

### Access

- **Argo CD:** create a read-only account (`accounts.berend: apiKey`, RBAC
  `g, berend, role:readonly`), then run `argocd account generate-token --account berend`.
  For pod logs, add `p, role:berend-logs, logs, get, */*, allow` and bind that role too.
- **Mattermost:** *Integrations → Bot Accounts*. Add the bot to the alerts channel and set
  `MATTERMOST_URL`, `MATTERMOST_TOKEN` and `MATTERMOST_CHANNEL_ID`. The same bot answers
  @berend and DMs (`MATTERMOST_CHAT`) over an outbound websocket, so no ingress is needed.
  An incoming webhook (`MATTERMOST_WEBHOOK_URL`) works for alerts only, without threads.
- **Slack:** create the app from `slack-app-manifest.yaml`. You need an app-level token
  (`connections:write`) as `SLACK_APP_TOKEN` and the bot token as `SLACK_BOT_TOKEN`.
  Re-install the app after updating the manifest, which adds the `message.channels` /
  `message.groups` events for thread follow-ups.

`deploy/argocd-notifications-mattermost.yaml` adds Argo CD's own 🚀 deployed / sync-failed
messages to the same channel, if you want instant deploy messages too.

## Speed

- **Model stays warm.** It is pre-loaded at startup and kept in memory (`OLLAMA_KEEP_ALIVE`).
  `OLLAMA_THINK=false` skips reasoning on qwen3-style models.
- **Streaming.** Answers stream into chat; independent tool calls run in parallel, and query
  embeddings are cached.
- **Incremental re-index.** It skips unchanged GitLab blobs without downloading them, and
  unchanged files and pages too. Downloads (`INGEST_WORKERS`) and embeddings (`EMBED_WORKERS`)
  run in parallel. Deleted docs are pruned, unless the source looks down.

## Deploy on OpenShift with Argo CD

The Helm chart lives in `chart/berend`. It provides a Deployment (1 replica, restricted
security context, probes on `/healthz` and `/readyz`), a PVC, a Service and an optional
ServiceMonitor.

```bash
# image: built by .gitlab-ci.yml (pytest + ruff, then rootless Buildah push to the GitLab registry)
oc new-project berend
oc create secret generic berend --from-env-file=.env      # or a SealedSecret / ExternalSecret
helm upgrade --install berend chart/berend -n berend \
  --set image.repository=registry.gitlab.example.nl/platform/berend \
  --set env.OLLAMA_HOST=http://ollama.ollama.svc:11434
```

For GitOps, use `deploy/argocd-application.yaml`. Set `caBundleConfigMap` for an internal CA
(e.g. a ConfigMap with `config.openshift.io/inject-trusted-cabundle: "true"`), and
`serviceMonitor.enabled=true` with user-workload monitoring.

**Metrics** (`/metrics`): `berend_questions_total`, `berend_answer_seconds`,
`berend_tool_calls_total{tool}`, `berend_feedback_total{vote}`, `berend_alerts_total{kind,severity}`,
`berend_argocd_apps{health,sync}`, `berend_argocd_poll_failures_total`, `berend_index_chunks`,
`berend_errors_total{component}`.

Liveness only checks Berend's own loops, so an outage of Argo CD, Mattermost or Ollama doesn't
cause restart loops; those show up in the metrics and the alerts channel instead.

## Sources

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
