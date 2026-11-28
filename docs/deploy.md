# Deploy on OpenShift with Argo CD

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
