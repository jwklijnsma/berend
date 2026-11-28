# Berend

**A local-LLM assistant for your platform team**, running on your own Ollama.

<div class="grid cards" markdown>

-   :material-rocket-launch: **[Quick start](quickstart.md)**

    `python -m berend.main` starts whatever you configured.

-   :material-chat-question: **[Answers](answers.md)**

    Q&A in Slack and Mattermost with sources.

-   :simple-argo: **[Argo CD alerts](alerts.md)**

    Colour-coded alerts with events, likely cause and next steps.

-   :simple-redhatopenshift: **[Deploy](deploy.md)**

    On OpenShift with Argo CD.

</div>

## What Berend does

| | |
|---|---|
| **Q&A in Slack and Mattermost** | Answers from your local files, docs website, GitLab (indexed docs plus live issues, MRs and code), live Argo CD status and the web — with sources |
| **Argo CD alerts** | Alerts in Mattermost when apps degrade, fail to sync or drift, with the Kubernetes events behind them and a likely cause from your runbooks |
| **Follow-ups** | Ask under the alert: *"@berend what changed in this revision?"* |
| **Hybrid search** | Chroma vectors plus BM25 over everything it indexed |
| **Health and metrics** | `/healthz`, `/readyz`, `/metrics` |

Every part is optional.
