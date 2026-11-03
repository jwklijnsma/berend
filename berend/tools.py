"""Tools the model can call. Each returns (text for the model, [(title, url), ...] sources)."""
import json
import logging
from urllib.parse import quote, urlparse

import requests

from . import config, store
from .ingest import html_to_text, session

log = logging.getLogger(__name__)
MAX_TOOL_CHARS = 6000


def _clip(s: str, n: int = MAX_TOOL_CHARS) -> str:
    return s if len(s) <= n else s[:n] + "\n…[truncated]"


# --- implementations ------------------------------------------------------
def search_knowledge_base(query: str, source: str = "all"):
    hits = store.search(query, kind=None if source in ("all", "", None) else source)
    if not hits:
        return "No results in the knowledge base.", []
    text = "\n\n---\n\n".join(
        f"Document: {h['title']} ({h['kind']}, matched by {h['match']})\nURL: {h['source']}\n{h['text']}"
        for h in hits)
    relevant = [(h["title"], h["source"]) for h in hits
                if h["match"] in ("both", "keyword") or h["score"] > 0.45]
    return _clip(text), relevant


def search_gitlab(query: str, scope: str = "issues"):
    """Live GitLab search: issues, merge_requests, blobs (code), wiki_blobs, commits."""
    if not (config.GITLAB_URL and config.GITLAB_TOKEN):
        return "GitLab is not configured.", []
    from .ingest import gitlab_client
    gl = gitlab_client()
    targets = [(p, gl.projects.get(p, lazy=True)) for p in config.GITLAB_PROJECTS] or [("", gl)]
    lines, sources = [], []
    for path, t in targets:
        try:
            results = t.search(scope, query, per_page=5, get_all=False)
        except Exception as e:
            log.warning("gitlab search failed: %s", e)
            continue
        for r in results:
            if scope in ("issues", "merge_requests"):
                lines.append(f"{r.get('title')} [{r.get('state')}] {r.get('web_url')}\n"
                             f"{(r.get('description') or '')[:400]}")
                sources.append((r.get("title", "GitLab"), r.get("web_url")))
            elif scope == "commits":
                lines.append(f"{r.get('short_id')} {r.get('title')} {r.get('web_url')}")
                sources.append((f"{r.get('short_id')} {r.get('title')}", r.get("web_url")))
            else:  # blobs / wiki_blobs
                fp = r.get("path") or r.get("filename")
                url = f"{config.GITLAB_URL}/{path}/-/blob/{r.get('ref')}/{quote(fp)}" if path and fp else ""
                lines.append(f"{fp} ({path or r.get('project_id')}, ref {r.get('ref')}) {url}\n"
                             f"{(r.get('data') or '')[:500]}")
                if url:
                    sources.append((f"{path}: {fp}", url))
    if not lines:
        return "No GitLab results.", []
    return _clip("\n\n".join(lines[:12])), sources[:8]


def _get_json(url, **kw) -> dict:
    r = requests.get(url, timeout=config.HTTP_TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def web_search(query: str):
    p = config.WEB_SEARCH_PROVIDER
    try:
        if p == "searxng":
            # Self-hosted SearXNG; can use Google/Bing/DDG as upstream engines.
            d = _get_json(f"{config.SEARXNG_URL.rstrip('/')}/search", params={"q": query, "format": "json"})
            items = [(i.get("title"), i.get("url"), i.get("content")) for i in d.get("results", [])]
        elif p == "brave":
            d = _get_json("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 6},
                          headers={"X-Subscription-Token": config.BRAVE_API_KEY, "Accept": "application/json"})
            items = [(i.get("title"), i.get("url"), i.get("description"))
                     for i in d.get("web", {}).get("results", [])]
        elif p == "google":
            # Legacy Custom Search JSON API: closed to new customers, shuts down 2027-01-01.
            d = _get_json("https://www.googleapis.com/customsearch/v1",
                          params={"key": config.GOOGLE_API_KEY, "cx": config.GOOGLE_CSE_ID, "q": query, "num": 6})
            items = [(i.get("title"), i.get("link"), i.get("snippet")) for i in d.get("items", [])]
        else:
            return "Web search is disabled.", []
    except Exception as e:
        return f"Web search failed: {e}", []
    items = items[:6]
    if not items:
        return "No web results.", []
    return _clip("\n\n".join(f"{t}\n{u}\n{s}" for t, u, s in items)), [(t, u) for t, u, _ in items[:3]]


def fetch_url(url: str):
    host = urlparse(url).hostname or ""
    if config.FETCH_ALLOWED_DOMAINS and not any(
            host == d or host.endswith("." + d) for d in config.FETCH_ALLOWED_DOMAINS):
        return f"Fetching {host} is not allowed.", []
    try:
        r = session.get(url, timeout=config.HTTP_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return f"Fetch failed: {e}", []
    if "html" in r.headers.get("content-type", ""):
        title, text = html_to_text(r.text)
        return _clip(f"{title}\n{text}"), [(title or url, url)]
    return _clip(r.text), [(url, url)]


def argocd_apps(filter: str = "unhealthy"):
    from .argocd import describe_apps
    try:
        return describe_apps(filter or "unhealthy")
    except Exception as e:
        return f"Argo CD query failed: {e}", []


# --- registry + schemas for Ollama ----------------------------------------
REGISTRY = {
    "search_knowledge_base": search_knowledge_base,
    "search_gitlab": search_gitlab,
    "web_search": web_search,
    "fetch_url": fetch_url,
    "argocd_apps": argocd_apps,
}


def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


def schemas() -> list[dict]:
    s = [_fn("search_knowledge_base",
             "Semantic search over indexed internal docs: local files, the docs website and GitLab "
             "repo docs/wikis. Use this FIRST for any internal question.",
             {"query": {"type": "string", "description": "search query, rephrased as keywords"},
              "source": {"type": "string", "enum": ["all", "local", "docs", "gitlab"]}},
             ["query"])]
    if config.GITLAB_URL and config.GITLAB_TOKEN:
        s.append(_fn("search_gitlab",
                     "Live keyword search in GitLab for current issues, merge requests, code or commits.",
                     {"query": {"type": "string"},
                      "scope": {"type": "string",
                                "enum": ["issues", "merge_requests", "blobs", "wiki_blobs", "commits"]}},
                     ["query"]))
    if config.ARGOCD_ENABLED:
        s.append(_fn("argocd_apps",
                     "Live Argo CD application status (health, sync, failing resources). "
                     "filter: 'unhealthy' (default), 'all', or part of an application name.",
                     {"filter": {"type": "string"}}, []))
    if config.WEB_SEARCH_PROVIDER in ("searxng", "brave", "google"):
        s.append(_fn("web_search",
                     "Search the public internet. Only for public/general knowledge not in internal docs. "
                     "Never put internal hostnames, names or secrets in the query.",
                     {"query": {"type": "string"}}, ["query"]))
    s.append(_fn("fetch_url", "Read the full text of a web page (e.g. a search result or docs page).",
                 {"url": {"type": "string"}}, ["url"]))
    return s


def call(name: str, args) -> tuple[str, list]:
    fn = REGISTRY.get(name)
    if not fn:
        return f"Unknown tool {name}", []
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"query": args}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return f"Bad arguments for {name}: {e}", []
    except Exception as e:
        log.exception("tool %s failed", name)
        return f"Tool {name} failed: {e}", []
