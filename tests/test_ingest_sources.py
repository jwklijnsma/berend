"""Docs-site crawler and GitLab ingest against fakes; run() error isolation; file reading."""
from types import SimpleNamespace

import pytest

from berend import config, ingest, store

LONG = "Real documentation text. " * 20


class Page:
    def __init__(self, html, status=200, ctype="text/html; charset=utf-8"):
        self.text, self.status_code, self.headers = html, status, {"content-type": ctype}


def site(pages):
    def get(url, timeout=None):
        return pages.get(url, Page("", 404))
    return get


def test_docs_crawler_stays_under_the_start_path(monkeypatch):
    base = "https://docs.test/guide/"
    pages = {
        base: Page(f"<title>Home</title><main>{LONG}<a href='a.html'>a</a><a href='/other/'>out</a>"
                   f"<a href='https://elsewhere.test/guide/x'>ext</a><a href='img.png'>img</a>"
                   f"<a href='a.html#sec'>same</a></main>"),
        base + "a.html": Page(f"<title>A</title><main>{LONG}<a href='{base}'>home</a><a href='b.html'>b</a></main>"),
        base + "b.html": Page("<main>too short</main>"),
        "https://docs.test/other/": Page(f"<main>{LONG}</main>"),
    }
    fetched = []
    monkeypatch.setattr(ingest.session, "get", lambda url, timeout=None: fetched.append(url) or site(pages)(url))
    monkeypatch.setattr(config, "DOCS_SITE_URLS", [base])
    out = ingest.run(only=["docs"])["docs"]
    assert sorted(fetched) == [base, base + "a.html", base + "b.html"]          # no /other/, no ext, no png
    assert out["indexed"] == 2 and out["failed"] == 0
    assert store.sources_of_kind("docs") == {base, base + "a.html"}


def test_docs_crawler_respects_max_pages_and_skips_non_html(monkeypatch):
    base = "https://docs.test/"
    links = "".join(f"<a href='p{i}'>x</a>" for i in range(10))
    pages = {base: Page(f"<main>{LONG}{links}</main>")}
    pages.update({f"{base}p{i}": Page(f"<main>{LONG}</main>") for i in range(10)})
    pages[base + "p0"] = Page("%PDF", ctype="application/pdf")
    monkeypatch.setattr(ingest.session, "get", site(pages))
    monkeypatch.setattr(config, "DOCS_SITE_URLS", [base])
    monkeypatch.setattr(config, "DOCS_MAX_PAGES", 4)
    ingest.run(only=["docs"])
    indexed = store.sources_of_kind("docs")
    assert len(indexed) == 3 and base + "p0" not in indexed


class FakeFiles:
    def __init__(self, blobs):
        self.blobs, self.downloads = blobs, []

    def raw(self, file_path, ref):
        self.downloads.append(file_path)
        return self.blobs[file_path].encode()


def fake_project(blobs, shas, wikis=()):
    tree = [{"type": "blob", "path": p, "id": shas[p]} for p in blobs] + [{"type": "tree", "path": "docs", "id": "t"}]
    wiki_pages = [SimpleNamespace(slug=s, title=t, content=c) for s, t, c in wikis]
    return SimpleNamespace(
        default_branch="main", web_url="https://gl.test/team/app",
        repository_tree=lambda **kw: tree, files=FakeFiles(blobs),
        wikis=SimpleNamespace(list=lambda **kw: wiki_pages, get=lambda slug: None))


@pytest.fixture
def gitlab(monkeypatch):
    monkeypatch.setattr(config, "GITLAB_URL", "https://gl.test")
    monkeypatch.setattr(config, "GITLAB_TOKEN", "t")
    monkeypatch.setattr(config, "GITLAB_PROJECTS", ["team/app"])
    monkeypatch.setattr(config, "GITLAB_EXTENSIONS", [".md"])

    def install(project):
        monkeypatch.setattr(ingest, "gitlab_client", lambda: SimpleNamespace(
            projects=SimpleNamespace(get=lambda name: project)))
        return project
    return install


def test_gitlab_ingest_uses_blob_sha_to_skip_downloads(gitlab, monkeypatch):
    monkeypatch.setattr(config, "GITLAB_INDEX_WIKIS", True)
    blobs = {"README.md": "# App\n" + LONG, "docs/run.md": "# Run\n" + LONG, "main.py": "print()"}
    shas = {"README.md": "s1", "docs/run.md": "s2", "main.py": "s3"}
    p = gitlab(fake_project(blobs, shas, wikis=[("home", "Home", "Wiki " + LONG)]))
    first = ingest.run(only=["gitlab"])["gitlab"]
    assert first["indexed"] == 3 and sorted(p.files.downloads) == ["README.md", "docs/run.md"]   # .py filtered
    assert "https://gl.test/team/app/-/wikis/home" in store.sources_of_kind("gitlab")
    p.files.downloads.clear()
    second = ingest.run(only=["gitlab"])["gitlab"]
    assert p.files.downloads == [] and second["unchanged"] >= 2


def test_gitlab_not_configured_is_a_noop(monkeypatch):
    monkeypatch.setattr(config, "GITLAB_URL", "")
    assert ingest.ingest_gitlab(ingest.Stats()) == set()


def test_a_failing_source_does_not_stop_the_others(monkeypatch, tmp_path):
    (tmp_path / "a.md").write_text("# a\n" + LONG)
    monkeypatch.setattr(config, "LOCAL_DATA_DIRS", [str(tmp_path)])
    monkeypatch.setattr(config, "LOCAL_EXTENSIONS", [".md"])

    def down(stats):
        raise ConnectionError("docs site down")

    monkeypatch.setitem(ingest.SOURCES, "docs", down)
    out = ingest.run()
    assert out["docs"] == "error: docs site down"
    assert out["local"]["indexed"] == 1


def test_reset_drops_the_index(monkeypatch):
    store.add_document("x " * 50, "u", "t", "local")
    monkeypatch.setattr(config, "LOCAL_DATA_DIRS", [])
    ingest.run(only=["local"], reset=True)
    assert store.count() == 0


def test_read_file_html_and_text(tmp_path):
    (tmp_path / "p.html").write_text("<title>T</title><nav>menu</nav><main>Hello</main>")
    (tmp_path / "n.txt").write_bytes(b"caf\xc3\xa9 \xff ok")
    assert ingest.read_file(tmp_path / "p.html") == "Hello"
    assert ingest.read_file(tmp_path / "n.txt").startswith("café")


def test_stats_and_safe():
    s = ingest.Stats()
    for n in (None, 0, 3, 2):
        s.record(n)
    assert s.as_dict() == {"indexed": 2, "unchanged": 1, "failed": 1, "chunks": 5}

    def bad(x):
        raise ValueError(x)

    assert ingest._safe(bad, "a") is None
