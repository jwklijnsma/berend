"""Prometheus metrics and liveness heartbeats."""
import time

from prometheus_client import Counter, Gauge, Histogram

QUESTIONS = Counter("berend_questions_total", "Questions answered", ["channel"])
ANSWER_SECONDS = Histogram("berend_answer_seconds", "Time to a complete answer", ["channel"],
                           buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300))
TOOL_CALLS = Counter("berend_tool_calls_total", "Tool calls made by the model", ["tool"])
ERRORS = Counter("berend_errors_total", "Errors", ["component"])
FEEDBACK = Counter("berend_feedback_total", "Feedback votes on answers", ["channel", "vote"])
ALERTS = Counter("berend_alerts_total", "Alert messages posted", ["kind", "severity"])
ARGO_APPS = Gauge("berend_argocd_apps", "Argo CD applications by health and sync", ["health", "sync"])
ARGO_POLL_FAILURES = Counter("berend_argocd_poll_failures_total", "Failed Argo CD polls")
INDEX_CHUNKS = Gauge("berend_index_chunks", "Chunks in the knowledge index")

_heartbeats: dict[str, tuple[float, float]] = {}


def heartbeat(name: str, max_age: float) -> None:
    """Mark a background loop alive; /healthz fails if it isn't refreshed within max_age."""
    _heartbeats[name] = (time.time(), max_age)


def stale() -> list[str]:
    now = time.time()
    return [n for n, (t, age) in _heartbeats.items() if now - t > age]
