"""Test setup: isolated data dir, no external services, deterministic fake embeddings."""
import hashlib
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="berend-test-")
os.environ.update(
    CHROMA_DIR=f"{_tmp}/chroma", FEEDBACK_LOG=f"{_tmp}/feedback.jsonl", ARGOCD_STATE_FILE=f"{_tmp}/state.json",
    LOCAL_DATA_DIRS="", DOCS_SITE_URLS="", GITLAB_URL="", WEB_SEARCH_PROVIDER="none",
    ARGOCD_URL="https://argo.test", ARGOCD_TOKEN="t", ARGOCD_CONFIRM_POLLS="2", ARGOCD_STORM_THRESHOLD="5",
    MATTERMOST_URL="https://mm.test", MATTERMOST_TOKEN="x", MATTERMOST_CHANNEL_ID="alerts",
    SLACK_BOT_TOKEN="", SLACK_APP_TOKEN="", STREAM_UPDATE_SECONDS="0", ANONYMIZED_TELEMETRY="False",
)

import pytest  # noqa: E402
from ollama import ChatResponse, Message  # noqa: E402

from berend import agent, argocd, config, store  # noqa: E402


def fake_embed(model, input, keep_alive=None):
    """Bag-of-words hashed into 64 dims: similar words -> similar vectors, fully deterministic."""
    out = []
    for text in input:
        v = [0.0] * 64
        for w in text.lower().split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
        out.append(v)
    return {"embeddings": out}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(store._ollama, "embed", fake_embed)
    store.reset()
    if os.path.exists(config.ARGOCD_STATE_FILE):
        os.remove(config.ARGOCD_STATE_FILE)
    argocd._client = None
    yield


def tool_call(name, args):
    return Message.ToolCall(function=Message.ToolCall.Function(name=name, arguments=args))


@pytest.fixture
def fake_llm(monkeypatch):
    """Scripted model: each entry is a list of tool calls or a final answer string.
    Records the messages of every round in .rounds."""
    class LLM:
        def __init__(self):
            self.script, self.rounds = [], []

        def chat(self, messages, **kw):
            self.rounds.append(list(messages))
            step = self.script.pop(0) if self.script else "ok"
            if isinstance(step, list):
                yield ChatResponse(message=Message(role="assistant", content="", tool_calls=step))
                return
            for i in range(0, len(step), 12):           # stream in pieces
                yield ChatResponse(message=Message(role="assistant", content=step[i:i + 12]))

    llm = LLM()
    monkeypatch.setattr(agent._client, "chat", llm.chat)
    return llm
