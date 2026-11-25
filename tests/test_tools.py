"""The model's tools: registry/dispatch, schemas, and each tool against faked services."""
import json

import pytest
import requests

from berend import config, store, tools


class Resp:
    def __init__(self, data=None, status=200, text="", ctype="application/json"):
        self.data, self.status_code, self.text = data, status, text
        self.headers = {"content-type": ctype}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self.data


def test_clip():
    assert tools._clip("abc", 5) == "abc"
    assert tools._clip("abcdefgh", 5) == "abcde\n…[truncated]"


def test_knowledge_base_cites_only_relevant_hits():
    store.add_document("Keycloak signing keys rotate every 90 days.", "file:///kc.md", "kc.md", "local")
    store.add_document("Unrelated text about lunch menus.", "https://docs/lunch", "lunch", "docs")
    text, sources = tools.search_knowledge_base("keycloak rotate keys")
    assert "Document: kc.md (local" in text and "URL: file:///kc.md" in text
    assert ("kc.md", "file:///kc.md") in sources
    text, _ = tools.search_knowledge_base("keycloak", source="docs")
    assert "kc.md" not in text


def test_knowledge_base_empty():
    assert tools.search_knowledge_base("anything") == ("No results in the knowledge base.", [])


def test_call_dispatch_and_argument_handling(monkeypatch):
    monkeypatch.setitem(tools.REGISTRY, "echo", lambda query, n=1: (f"{query}x{n}", []))
    assert tools.call("echo", {"query": "a", "n": 2}) == ("ax2", [])
    assert tools.call("echo", '{"query": "b"}') == ("bx1", [])
    assert tools.call("echo", "not json") == ("not jsonx1", [])        # bare string -> query
    assert tools.call("nope", {}) == ("Unknown tool nope", [])
    msg, _ = tools.call("echo", {"wrong": 1})
    assert msg.startswith("Bad arguments for echo")

    def boom(query):
        raise RuntimeError("kaput")

    monkeypatch.setitem(tools.REGISTRY, "boom", boom)
    assert tools.call("boom", {"query": "x"}) == ("Tool boom failed: kaput", [])


def test_schemas_follow_configuration(monkeypatch):
    names = lambda: [s["function"]["name"] for s in tools.schemas()]  # noqa: E731
    monkeypatch.setattr(config, "GITLAB_URL", "")
    monkeypatch.setattr(config, "ARGOCD_ENABLED", False)
    monkeypatch.setattr(config, "WEB_SEARCH_PROVIDER", "none")
    assert names() == ["search_knowledge_base", "fetch_url"]
    monkeypatch.setattr(config, "GITLAB_URL", "https://gl")
    monkeypatch.setattr(config, "GITLAB_TOKEN", "t")
    monkeypatch.setattr(config, "ARGOCD_ENABLED", True)
    monkeypatch.setattr(config, "WEB_SEARCH_PROVIDER", "brave")
    assert names() == ["search_knowledge_base", "search_gitlab", "argocd_apps", "web_search", "fetch_url"]
    for s in tools.schemas():
        assert s["type"] == "function" and s["function"]["name"] in tools.REGISTRY
        json.dumps(s)


@pytest.mark.parametrize("provider,payload,expect_url", [
    ("searxng", {"results": [{"title": "T", "url": "https://a", "content": "C"}]}, "http://searx.test/search"),
    ("brave", {"web": {"results": [{"title": "T", "url": "https://a", "description": "C"}]}},
     "https://api.search.brave.com/res/v1/web/search"),
    ("google", {"items": [{"title": "T", "link": "https://a", "snippet": "C"}]},
     "https://www.googleapis.com/customsearch/v1"),
])
def test_web_search_providers(monkeypatch, provider, payload, expect_url):
    calls = []
    monkeypatch.setattr(config, "WEB_SEARCH_PROVIDER", provider)
    monkeypatch.setattr(config, "SEARXNG_URL", "http://searx.test/")
    monkeypatch.setattr(tools.requests, "get", lambda url, **kw: calls.append((url, kw)) or Resp(payload))
    text, sources = tools.web_search("wireguard 2fa")
    assert text == "T\nhttps://a\nC" and sources == [("T", "https://a")]
    assert calls[0][0] == expect_url and calls[0][1]["params"]["q"] == "wireguard 2fa"


def test_web_search_disabled_empty_and_failing(monkeypatch):
    monkeypatch.setattr(config, "WEB_SEARCH_PROVIDER", "none")
    assert tools.web_search("x") == ("Web search is disabled.", [])
    monkeypatch.setattr(config, "WEB_SEARCH_PROVIDER", "searxng")
    monkeypatch.setattr(tools.requests, "get", lambda url, **kw: Resp({"results": []}))
    assert tools.web_search("x") == ("No web results.", [])
    monkeypatch.setattr(tools.requests, "get", lambda url, **kw: Resp(status=500))
    assert tools.web_search("x")[0].startswith("Web search failed: 500")


def test_web_search_caps_results(monkeypatch):
    monkeypatch.setattr(config, "WEB_SEARCH_PROVIDER", "searxng")
    many = {"results": [{"title": f"T{i}", "url": f"https://{i}", "content": ""} for i in range(10)]}
    monkeypatch.setattr(tools.requests, "get", lambda url, **kw: Resp(many))
    text, sources = tools.web_search("x")
    assert text.count("https://") == 6 and len(sources) == 3


def test_fetch_url_allow_list(monkeypatch):
    monkeypatch.setattr(config, "FETCH_ALLOWED_DOMAINS", ["docs.example.com"])
    assert tools.fetch_url("https://evil.test/x") == ("Fetching evil.test is not allowed.", [])
    assert tools.fetch_url("https://docs.example.com.evil.test/")[0].startswith("Fetching")   # no suffix trick
    monkeypatch.setattr(tools.session, "get",
                        lambda url, timeout: Resp(text="<title>Guide</title><main>Body text</main>", ctype="text/html"))
    text, sources = tools.fetch_url("https://sub.docs.example.com/guide")
    assert text == "Guide\nBody text" and sources == [("Guide", "https://sub.docs.example.com/guide")]


def test_fetch_url_plain_text_and_errors(monkeypatch):
    monkeypatch.setattr(config, "FETCH_ALLOWED_DOMAINS", [])
    monkeypatch.setattr(tools.session, "get", lambda url, timeout: Resp(text="raw", ctype="text/plain"))
    assert tools.fetch_url("https://x.test/a.txt") == ("raw", [("https://x.test/a.txt", "https://x.test/a.txt")])
    monkeypatch.setattr(tools.session, "get", lambda url, timeout: Resp(status=404))
    assert tools.fetch_url("https://x.test/gone")[0] == "Fetch failed: 404 error"


class FakeProject:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, scope, query, per_page, get_all):
        self.calls.append((scope, query))
        if isinstance(self.results, Exception):
            raise self.results
        return self.results


class FakeGitlab:
    def __init__(self, projects):
        self.projects = self
        self._p = projects

    def get(self, path, lazy):
        return self._p[path]


@pytest.fixture
def gitlab(monkeypatch):
    monkeypatch.setattr(config, "GITLAB_URL", "https://gl.test")
    monkeypatch.setattr(config, "GITLAB_TOKEN", "t")

    def install(projects, paths):
        monkeypatch.setattr(config, "GITLAB_PROJECTS", paths)
        fake = FakeGitlab(projects)
        monkeypatch.setattr("berend.ingest.gitlab_client", lambda: fake)
        return fake
    return install


def test_gitlab_not_configured(monkeypatch):
    monkeypatch.setattr(config, "GITLAB_URL", "")
    assert tools.search_gitlab("x") == ("GitLab is not configured.", [])


def test_gitlab_issues_and_commits(gitlab):
    issue = {"title": "Login broken", "state": "opened", "web_url": "https://gl.test/i/1", "description": "d" * 999}
    gitlab({"team/app": FakeProject([issue])}, ["team/app"])
    text, sources = tools.search_gitlab("login")
    assert text.startswith("Login broken [opened] https://gl.test/i/1\n") and len(text) < 500
    assert sources == [("Login broken", "https://gl.test/i/1")]
    commit = {"short_id": "abc123", "title": "fix", "web_url": "https://gl.test/c/abc123"}
    gitlab({"team/app": FakeProject([commit])}, ["team/app"])
    assert tools.search_gitlab("fix", scope="commits")[1] == [("abc123 fix", "https://gl.test/c/abc123")]


def test_gitlab_blobs_build_links_and_quote_paths(gitlab):
    blob = {"path": "docs/run book.md", "ref": "main", "data": "how to restart"}
    gitlab({"team/app": FakeProject([blob])}, ["team/app"])
    text, sources = tools.search_gitlab("restart", scope="blobs")
    assert sources == [("team/app: docs/run book.md", "https://gl.test/team/app/-/blob/main/docs/run%20book.md")]
    assert "how to restart" in text


def test_gitlab_failing_project_is_skipped(gitlab):
    gitlab({"a": FakeProject(RuntimeError("403")), "b": FakeProject([])}, ["a", "b"])
    assert tools.search_gitlab("x") == ("No GitLab results.", [])


def test_argocd_tool_wraps_errors(monkeypatch):
    monkeypatch.setattr("berend.argocd.describe_apps", lambda f: (f"apps:{f}", []))
    assert tools.argocd_apps("") == ("apps:unhealthy", [])

    def boom(f):
        raise ConnectionError("argo down")

    monkeypatch.setattr("berend.argocd.describe_apps", boom)
    assert tools.argocd_apps("all") == ("Argo CD query failed: argo down", [])
