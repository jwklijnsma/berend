"""Tool-calling loop against the local Ollama model.

Fast path: model kept loaded (keep_alive), answers streamed token by token, tool calls
from one round executed in parallel, query embeddings cached.
"""
import datetime
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import ollama

from . import config, tools
from .metrics import TOOL_CALLS

log = logging.getLogger(__name__)
_client = ollama.Client(host=config.OLLAMA_HOST)
_pool = ThreadPoolExecutor(8)

SYSTEM_PROMPT = """You are {name}, the internal knowledge assistant for our platform team, answering questions in chat.
If people ask who you are, introduce yourself as {name}.
Today is {today}.

How to work:
1. For anything about our systems, processes, projects or docs, call search_knowledge_base first.
   Use the exact terms from the question (error messages, hostnames, resource names) in the query.
   If the first results are weak, try again with different keywords.
2. Use search_gitlab for current issues, merge requests, code or recent changes.
3. Use argocd_apps for the live state of Argo CD applications (what is broken, out of sync, why).
4. Use web_search only for public, general knowledge (vendor docs, error messages, versions).
   Never put internal hostnames, customer names, IPs or secrets in a web search.
5. Use fetch_url to read a promising page in full.
You may call several tools at once when they are independent.

Answering:
- Answer only from what the tools returned. If you could not find it, say so plainly and
  suggest where to look or whom to ask. Never invent commands, hostnames or config values.
- Lead with the answer. Short paragraphs or bullets, code and commands in ``` blocks.
- Name the documents you used (e.g. "according to keycloak-runbook.md"); links are added
  under your answer automatically, so don't write a separate sources list.
- Reply in the language the question was asked in (Dutch, Frisian or English)."""


@dataclass
class Answer:
    text: str
    sources: list = field(default_factory=list)      # [(title, url)]
    tools_used: list = field(default_factory=list)   # tool names in call order
    seconds: float = 0.0


def _chat_kwargs(with_tools):
    kw = dict(model=config.CHAT_MODEL, stream=True, keep_alive=config.OLLAMA_KEEP_ALIVE,
              options={"num_ctx": config.NUM_CTX, "temperature": 0.2},
              tools=with_tools or None)
    if config.OLLAMA_THINK:
        kw["think"] = config.OLLAMA_THINK.lower() in ("1", "true", "yes", "on")
    return kw


def warmup():
    """Load chat + embedding models into memory so the first question isn't slow."""
    try:
        _client.generate(model=config.CHAT_MODEL, prompt="", keep_alive=config.OLLAMA_KEEP_ALIVE)
        _client.embed(model=config.EMBED_MODEL, input=["warmup"], keep_alive=config.OLLAMA_KEEP_ALIVE)
        log.info("models warm: %s, %s", config.CHAT_MODEL, config.EMBED_MODEL)
    except Exception as e:
        log.warning("warmup failed: %s", e)


def _names(title: str, url: str) -> list[str]:
    """Ways the model may refer to a source: full title, file name, or the URL itself."""
    out = [title, url]
    for part in re.split(r"[:/]\s*", title):
        part = part.strip()
        if len(part) >= 5:
            out.append(part)
    return [n.lower() for n in out if n]


def cited_sources(sources: list, text: str) -> list:
    """Keep sources the answer refers to; if it names none, keep the strongest few."""
    uniq, seen = [], set()
    for t, u in sources:
        if u and u not in seen:
            seen.add(u)
            uniq.append((t or u, u))
    low = text.lower()
    cited = [s for s in uniq if any(n in low for n in _names(*s))]
    return cited or uniq[:4]


def answer(question: str, history: list[dict] | None = None, on_tool=None, on_text=None) -> Answer:
    t0 = time.time()
    messages = [{"role": "system",
                 "content": SYSTEM_PROMPT.format(name=config.AGENT_NAME, today=datetime.date.today().isoformat())}]
    messages += (history or [])[-config.HISTORY_MESSAGES:]
    messages.append({"role": "user", "content": question})
    tool_schemas = tools.schemas()
    sources, used = [], []

    def done(text):
        text = text.strip() or "Sorry, I couldn't produce an answer."
        return Answer(text, cited_sources(sources, text), used, time.time() - t0)

    for round_ in range(config.MAX_TOOL_ROUNDS + 1):
        last = round_ == config.MAX_TOOL_ROUNDS              # force a final answer on the last round
        content, calls = "", []
        for part in _client.chat(messages=messages, **_chat_kwargs(None if last else tool_schemas)):
            m = part.message
            if m.tool_calls:
                calls.extend(m.tool_calls)
            if m.content:
                content += m.content
                if on_text and not calls:
                    on_text(content)
        if not calls:
            return done(content)

        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        for tc in calls:
            log.info("tool %s %s", tc.function.name, tc.function.arguments)
            used.append(tc.function.name)
            TOOL_CALLS.labels(tc.function.name).inc()
            if on_tool:
                on_tool(tc.function.name, tc.function.arguments)
        results = list(_pool.map(lambda tc: tools.call(tc.function.name, tc.function.arguments), calls))
        for tc, (text, srcs) in zip(calls, results):
            sources.extend(srcs)
            messages.append({"role": "tool", "tool_name": tc.function.name, "content": text})
    return done("Sorry, I couldn't finish that one.")


if __name__ == "__main__":  # quick test without chat:  python -m berend.agent "question"
    import sys
    logging.basicConfig(level=logging.INFO)
    a = answer(" ".join(sys.argv[1:]) or "What documentation do we have?")
    print(a.text, "\n\nSources:", *[f"- {t}: {u}" for t, u in a.sources], sep="\n")
    print(f"\n{a.seconds:.1f}s, tools: {a.tools_used}")
