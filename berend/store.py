"""Knowledge index: ChromaDB on disk + Ollama embeddings + an in-memory BM25 keyword index.

Search is hybrid: vector search finds things phrased differently, keyword search finds exact
tokens (hostnames, error codes, CRD names like `ImagePullBackOff` or `cert-manager`) that
embeddings blur. Both rankings are merged with reciprocal rank fusion.

Every document carries a `version` (blob sha, mtime+size or content hash), so re-indexing
skips anything unchanged without downloading or embedding it again.
"""
import hashlib
import logging
import math
import re
import threading
import time
from collections import Counter
from functools import lru_cache

import chromadb
import ollama

from . import config

log = logging.getLogger(__name__)
_ollama = ollama.Client(host=config.OLLAMA_HOST)
_collection = None
_write_lock = threading.Lock()
_generation = 0                    # bumped on every write; invalidates the keyword index


def collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=config.CHROMA_DIR)
        _collection = client.get_or_create_collection(
            config.COLLECTION, metadata={"hnsw:space": "cosine"}
        )
    return _collection


def _bump():
    global _generation
    _generation += 1


def reset():
    global _collection
    client = chromadb.PersistentClient(path=config.CHROMA_DIR)
    try:
        client.delete_collection(config.COLLECTION)
    except Exception:
        pass
    _collection = None
    _embed_query.cache_clear()
    _kw["index"] = None
    _bump()


def embed(texts: list[str]) -> list[list[float]]:
    return _ollama.embed(model=config.EMBED_MODEL, input=texts,
                         keep_alive=config.OLLAMA_KEEP_ALIVE)["embeddings"]


@lru_cache(maxsize=1024)
def _embed_query(query: str) -> tuple:
    return tuple(embed([query])[0])


def content_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


# --- chunking -------------------------------------------------------------
def chunk(text: str, size: int = config.CHUNK_SIZE, overlap: int = config.CHUNK_OVERLAP) -> list[str]:
    """Split on paragraphs, pack into ~size-char chunks, carry a tail as overlap."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > size:                       # very long paragraph: hard split
            head, p = p[:size], p[size - overlap:]
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(head)
        if len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}" if cur else p
        else:
            if cur:
                chunks.append(cur)
            tail = cur[-overlap:] if cur and overlap else ""
            cur = f"{tail}\n\n{p}".strip() if tail else p
    if cur:
        chunks.append(cur)
    return chunks


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (breadcrumb, body) by headings, ignoring '#' inside code fences."""
    out, stack, body, in_code = [], [], [], False

    def flush():
        if "".join(body).strip():
            out.append((" > ".join(t for _, t in stack), "\n".join(body)))
        body.clear()

    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            stack = [(lvl, t) for lvl, t in stack if lvl < level] + [(level, m.group(2))]
        else:
            body.append(line)
    flush()
    return out


def chunk_document(text: str) -> list[str]:
    """Heading-aware chunks: each chunk starts with its section path, e.g. 'Keycloak > Key rotation'."""
    secs = sections(text)
    if len(secs) <= 1 and not (secs and secs[0][0]):
        return chunk(text)
    out = []
    for crumb, body in secs:
        for piece in chunk(body):
            out.append(f"[{crumb}]\n{piece}" if crumb else piece)
    return out


# --- keyword index (BM25) -------------------------------------------------
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.\-/:]*[a-z0-9]|[a-z0-9]")


def tokenize(text: str) -> list[str]:
    toks = []
    for t in _TOKEN.findall(text.lower()):
        toks.append(t)
        parts = re.split(r"[_.\-/:]", t)       # 'cert-manager' -> also 'cert', 'manager'
        if len(parts) > 1:
            toks += [p for p in parts if len(p) > 1]
    return toks


class BM25:
    k1, b = 1.4, 0.75

    def __init__(self, ids, docs, metas):
        self.ids, self.docs, self.metas = ids, docs, metas
        self.tf = [Counter(tokenize(f"{m.get('title', '')} {d}")) for d, m in zip(docs, metas)]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1
        df = Counter(t for c in self.tf for t in c)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int, kind: str | None = None) -> list[int]:
        q = set(tokenize(query))
        scores = []
        for i, tf in enumerate(self.tf):
            if kind and self.metas[i].get("kind") != kind:
                continue
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [i for _, i in scores[:k]]


_kw = {"index": None, "gen": -1, "count": -1, "built": 0.0}
_kw_lock = threading.Lock()


def keyword_index() -> BM25:
    """Rebuilt lazily when this process wrote, or another process (ingest job) changed the count."""
    col = collection()
    count = col.count()
    with _kw_lock:
        age = time.time() - _kw["built"]
        changed = _kw["gen"] != _generation or _kw["count"] != count
        # While a re-index is writing, rebuild at most every 30s instead of on every question.
        stale = (changed and (age > 30 or _kw["count"] == 0)) or age > 900
        if _kw["index"] is None or stale:
            data = col.get(include=["documents", "metadatas"])
            _kw.update(index=BM25(data["ids"], data["documents"], data["metadatas"]),
                       gen=_generation, count=count, built=time.time())
        return _kw["index"]


# --- writes ---------------------------------------------------------------
def is_current(source: str, version: str) -> bool:
    res = collection().get(where={"source": source}, limit=1, include=["metadatas"])
    return bool(res["metadatas"]) and res["metadatas"][0].get("version") == version


def add_document(text: str, source: str, title: str, kind: str, version: str | None = None) -> int:
    """(Re)index one document. Returns chunks written, 0 if it was unchanged."""
    version = version or content_hash(text)
    if is_current(source, version):
        return 0
    pieces = chunk_document(text)
    vectors = []
    for i in range(0, len(pieces), config.EMBED_BATCH):   # embed outside the lock (slow part)
        # Prefix the title so short chunks keep their context in the embedding.
        vectors += embed([f"{title}\n{p}" for p in pieces[i:i + config.EMBED_BATCH]])
    with _write_lock:
        col = collection()
        col.delete(where={"source": source})
        if pieces:
            col.add(
                ids=[hashlib.sha1(f"{source}#{i}".encode()).hexdigest() for i in range(len(pieces))],
                documents=pieces,
                embeddings=vectors,
                metadatas=[{"source": source, "title": title, "kind": kind, "chunk": i,
                            "version": version} for i in range(len(pieces))],
            )
        _bump()
    return max(len(pieces), 1)


def sources_of_kind(kind: str) -> set[str]:
    res = collection().get(where={"kind": kind}, include=["metadatas"])
    return {m["source"] for m in res["metadatas"]}


def delete_sources(sources) -> None:
    sources = list(sources)
    with _write_lock:
        for i in range(0, len(sources), 100):
            collection().delete(where={"source": {"$in": sources[i:i + 100]}})
        _bump()


def count() -> int:
    return collection().count()


# --- search ---------------------------------------------------------------
def search(query: str, k: int = config.TOP_K, kind: str | None = None) -> list[dict]:
    """Hybrid search. Each hit: text, source, title, kind, score (cosine, vector hits) and
    match ('vector', 'keyword' or 'both')."""
    col = collection()
    if col.count() == 0:
        return []
    n = k * 4
    res = col.query(query_embeddings=[list(_embed_query(query.strip().lower()))],
                    n_results=min(n, col.count()), where={"kind": kind} if kind else None)
    hits: dict[str, dict] = {}
    rrf: Counter = Counter()
    for rank, (cid, d, m, dist) in enumerate(zip(res["ids"][0], res["documents"][0],
                                                 res["metadatas"][0], res["distances"][0])):
        hits[cid] = {"text": d, "source": m["source"], "title": m["title"], "kind": m["kind"],
                     "score": round(1 - dist, 3), "match": "vector"}
        rrf[cid] += 1 / (60 + rank)

    kw = keyword_index()
    for rank, i in enumerate(kw.search(query, n, kind)):
        cid, m = kw.ids[i], kw.metas[i]
        if cid in hits:
            hits[cid]["match"] = "both"
        else:
            hits[cid] = {"text": kw.docs[i], "source": m["source"], "title": m["title"],
                         "kind": m["kind"], "score": 0.0, "match": "keyword"}
        rrf[cid] += 1 / (60 + rank)

    ranked = [hits[cid] for cid, _ in rrf.most_common()]
    # Diversify: best chunk per source first, then fill with remaining chunks.
    seen, first, rest = set(), [], []
    for h in ranked:
        (rest if h["source"] in seen else first).append(h)
        seen.add(h["source"])
    return (first + rest)[:k]
