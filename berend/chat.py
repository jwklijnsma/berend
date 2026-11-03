"""Shared chat behaviour for Slack and Mattermost: live status, throttled streaming,
metrics and feedback logging."""
import datetime
import json
import logging
import os
import threading
import time

from . import agent, config
from .metrics import ANSWER_SECONDS, ERRORS, FEEDBACK, QUESTIONS

log = logging.getLogger(__name__)
_feedback_lock = threading.Lock()

TOOL_LABELS = {
    "search_knowledge_base": ("📚", "docs"),
    "search_gitlab": ("🦊", "GitLab"),
    "argocd_apps": ("🐙", "Argo CD"),
    "web_search": ("🌐", "web"),
    "fetch_url": ("📄", "page"),
}


def tools_summary(ans: agent.Answer) -> str:
    out = []
    for name in dict.fromkeys(ans.tools_used):
        emoji, label = TOOL_LABELS.get(name, ("🔧", name))
        out.append(f"{emoji} {label}")
    return "  ".join(out)


def ask(question: str, history: list[dict], channel: str, set_text, render=lambda t: t) -> agent.Answer:
    """Run the agent, keeping one placeholder message up to date via set_text(str)."""
    last = {"t": 0.0}

    def on_tool(name, args):
        emoji, label = TOOL_LABELS.get(name, ("🔧", name))
        args = args or {}
        q = args.get("query") or args.get("url") or args.get("filter") or ""
        set_text(f"_{emoji} searching {label}… {str(q)[:80]}_")
        last["t"] = time.time()

    def on_text(text):                       # throttled: chat APIs rate-limit edits
        if time.time() - last["t"] >= config.STREAM_UPDATE_SECONDS:
            last["t"] = time.time()
            set_text(render(text)[:3500] + " ▍")

    QUESTIONS.labels(channel).inc()
    try:
        ans = agent.answer(question, history, on_tool=on_tool, on_text=on_text)
    except Exception:
        ERRORS.labels(f"chat_{channel}").inc()
        raise
    ANSWER_SECONDS.labels(channel).observe(ans.seconds)
    return ans


def log_feedback(channel: str, user: str, vote: str, question: str, answer: str = "") -> None:
    FEEDBACK.labels(channel, vote).inc()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(config.FEEDBACK_LOG)), exist_ok=True)
        with _feedback_lock, open(config.FEEDBACK_LOG, "a") as f:
            f.write(json.dumps({"time": datetime.datetime.now().isoformat(timespec="seconds"),
                                "channel": channel, "user": user, "vote": vote,
                                "question": question, "answer": answer[:2000]}) + "\n")
    except Exception as e:
        log.warning("feedback log failed: %s", e)
