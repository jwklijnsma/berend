"""Index local files, a docs website and GitLab into the vector store.

Incremental: unchanged documents are skipped (GitLab files by blob sha without downloading,
local files by mtime+size, web pages by content hash). Documents that disappeared at the
source are removed from the index.

    python -m berend.ingest              # all configured sources
    python -m berend.ingest --only local --reset
"""
import argparse
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config, store
from .metrics import ERRORS, INDEX_CHUNKS

log = logging.getLogger(__name__)
session = requests.Session()
session.headers["User-Agent"] = config.USER_AGENT
session.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=config.INGEST_WORKERS * 2))
session.mount("http://", requests.adapters.HTTPAdapter(pool_maxsize=config.INGEST_WORKERS * 2))

SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip", ".pdf", ".gz", ".tar", ".css", ".js", ".ico")


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.indexed = self.unchanged = self.failed = self.chunks = 0

    def record(self, n: int | None):
        with self.lock:
            if n is None:
                self.failed += 1
            elif n == 0:
                self.unchanged += 1
            else:
                self.indexed += 1
                self.chunks += n

    def as_dict(self):
        return {"indexed": self.indexed, "unchanged": self.unchanged,
                "failed": self.failed, "chunks": self.chunks}


# --- helpers --------------------------------------------------------------
def parse_html(html: str, base_url: str = "") -> tuple[str, str, list[str]]:
    """Return (title, main text, absolute links). Links are taken before nav is stripped."""
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    links = [urldefrag(urljoin(base_url, a["href"]))[0] for a in soup.find_all("a", href=True)] \
        if base_url else []
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return title, main.get_text("\n", strip=True), links


def html_to_text(html: str) -> tuple[str, str]:
    title, text, _ = parse_html(html)
    return title, text


def read_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if ext in (".html", ".htm"):
        return html_to_text(raw)[1]
    return raw


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        log.warning("%s%s failed: %s", fn.__name__, args[:1], e)
        return None


# --- local files ----------------------------------------------------------
def ingest_local(stats: Stats) -> set[str]:
    files = []
    for root in config.LOCAL_DATA_DIRS:
        for dirpath, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            files += [Path(dirpath) / n for n in names
                      if Path(n).suffix.lower() in config.LOCAL_EXTENSIONS]

    def one(path: Path):
        st = path.stat()
        source, version = f"file://{path.resolve()}", f"{st.st_mtime_ns}-{st.st_size}"
        if store.is_current(source, version):
            return 0
        return store.add_document(read_file(path), source, path.name, "local", version)

    with ThreadPoolExecutor(config.EMBED_WORKERS) as pool:
        for n in pool.map(lambda p: _safe(one, p), files):
            stats.record(n)
    return {f"file://{p.resolve()}" for p in files}


# --- docs website ---------------------------------------------------------
def _fetch(url: str):
    r = session.get(url, timeout=config.HTTP_TIMEOUT)
    if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
        return None
    return r.text


def ingest_docs_site(stats: Stats) -> set[str]:
    seen_ok: set[str] = set()
    fetch_pool = ThreadPoolExecutor(config.INGEST_WORKERS)
    embed_pool = ThreadPoolExecutor(config.EMBED_WORKERS)
    jobs = []
    for start in config.DOCS_SITE_URLS:
        base = urlparse(start)
        prefix = base.path if base.path.endswith("/") else base.path.rsplit("/", 1)[0] + "/"
        frontier, seen, pages = [start], {start}, 0
        while frontier and pages < config.DOCS_MAX_PAGES:
            batch = frontier[:config.DOCS_MAX_PAGES - pages]
            frontier = []
            for url, html in zip(batch, fetch_pool.map(lambda u: _safe(_fetch, u), batch)):
                pages += 1
                if html is None:
                    continue
                seen_ok.add(url)
                title, text, links = parse_html(html, url)
                if len(text) > 200:
                    jobs.append(embed_pool.submit(_safe, store.add_document, text, url, title or url, "docs"))
                for link in links:
                    p = urlparse(link)
                    if (p.scheme in ("http", "https") and p.netloc == base.netloc
                            and p.path.startswith(prefix) and link not in seen
                            and not p.path.lower().endswith(SKIP_EXT)):
                        seen.add(link)
                        frontier.append(link)
    for j in jobs:
        stats.record(j.result())
    fetch_pool.shutdown()
    embed_pool.shutdown()
    return seen_ok


# --- GitLab ---------------------------------------------------------------
_gl = None
_gl_lock = threading.Lock()


def gitlab_client():
    global _gl
    with _gl_lock:
        if _gl is None:
            import gitlab
            _gl = gitlab.Gitlab(config.GITLAB_URL, private_token=config.GITLAB_TOKEN,
                                ssl_verify=os.getenv("REQUESTS_CA_BUNDLE", True),
                                timeout=config.HTTP_TIMEOUT)
            _gl.auth()
    return _gl


def ingest_gitlab(stats: Stats) -> set[str]:
    if not (config.GITLAB_URL and config.GITLAB_TOKEN and config.GITLAB_PROJECTS):
        return set()
    gl, seen = gitlab_client(), set()
    for name in config.GITLAB_PROJECTS:
        project = gl.projects.get(name)
        ref = project.default_branch
        if ref:
            blobs = [i for i in project.repository_tree(recursive=True, ref=ref, iterator=True, per_page=100)
                     if i["type"] == "blob" and Path(i["path"]).suffix.lower() in config.GITLAB_EXTENSIONS]

            def one(item, project=project, ref=ref, name=name):
                url = f"{project.web_url}/-/blob/{ref}/{item['path']}"
                if store.is_current(url, item["id"]):       # blob sha unchanged: skip download
                    return 0
                text = project.files.raw(file_path=item["path"], ref=ref).decode("utf-8", "ignore")
                return store.add_document(text, url, f"{name}: {item['path']}", "gitlab", item["id"])

            seen |= {f"{project.web_url}/-/blob/{ref}/{i['path']}" for i in blobs}
            with ThreadPoolExecutor(config.INGEST_WORKERS) as pool:
                for n in pool.map(lambda i: _safe(one, i), blobs):
                    stats.record(n)
        if config.GITLAB_INDEX_WIKIS:
            try:
                for page in project.wikis.list(get_all=True, with_content=1):
                    content = getattr(page, "content", None) or project.wikis.get(page.slug).content
                    url = f"{project.web_url}/-/wikis/{page.slug}"
                    seen.add(url)
                    stats.record(_safe(store.add_document, content, url, f"{name} wiki: {page.title}", "gitlab"))
            except Exception as e:
                log.warning("wiki %s failed: %s", name, e)
    return seen


SOURCES = {"local": ingest_local, "docs": ingest_docs_site, "gitlab": ingest_gitlab}


def _prune(kind: str, seen: set[str]) -> int:
    """Remove documents no longer at the source. Guarded against outages wiping the index."""
    indexed = store.sources_of_kind(kind)
    stale = indexed - seen
    if indexed and len(seen) < 0.5 * len(indexed):
        log.warning("%s: only %d of %d sources seen, skipping cleanup (source down?)",
                    kind, len(seen), len(indexed))
        return 0
    if stale:
        store.delete_sources(stale)
    return len(stale)


def run(only: list[str] | None = None, reset: bool = False) -> dict:
    if reset:
        store.reset()
    out = {}
    for name, fn in SOURCES.items():
        if only and name not in only:
            continue
        t0, stats = time.time(), Stats()
        try:
            seen = fn(stats)
            out[name] = {**stats.as_dict(), "removed": _prune(name, seen),
                         "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            log.exception("source %s failed", name)
            ERRORS.labels(f"ingest_{name}").inc()
            out[name] = f"error: {e}"
    INDEX_CHUNKS.set(store.count())
    log.info("ingest done: %s", out)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", choices=list(SOURCES))
    ap.add_argument("--reset", action="store_true", help="drop the index first")
    args = ap.parse_args()
    print(run(args.only, args.reset))
