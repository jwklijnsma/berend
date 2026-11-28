# Argo CD alerts

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

## Access

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
