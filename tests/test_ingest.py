import os

from berend import config, ingest, store


def test_local_ingest_is_incremental_and_prunes(tmp_path, monkeypatch):
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text(f"# {name}\n\nContent of {name} " * 20)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "x.md").write_text("ignored")
    monkeypatch.setattr(config, "LOCAL_DATA_DIRS", [str(tmp_path)])
    monkeypatch.setattr(config, "LOCAL_EXTENSIONS", [".md"])

    first = ingest.run(only=["local"])["local"]
    assert (first["indexed"], first["unchanged"]) == (2, 0)
    second = ingest.run(only=["local"])["local"]
    assert (second["indexed"], second["unchanged"]) == (0, 2)

    (tmp_path / "b.md").write_text("# b\n\nnew content")
    os.utime(tmp_path / "b.md", None)
    (tmp_path / "c.md").write_text("# c\n\nthird")
    third = ingest.run(only=["local"])["local"]
    assert (third["indexed"], third["unchanged"]) == (2, 1)

    (tmp_path / "a.md").unlink()
    assert ingest.run(only=["local"])["local"]["removed"] == 1
    assert {h["title"] for h in store.search("content", k=10)} == {"b.md", "c.md"}


def test_prune_guard_protects_index_when_source_is_down():
    for i in range(4):
        store.add_document(f"doc {i}", f"https://docs/{i}", f"d{i}", "docs")
    assert ingest._prune("docs", {"https://docs/0"}) == 0          # 1 of 4 seen: assume outage
    assert ingest._prune("docs", {"https://docs/0", "https://docs/1", "https://docs/2"}) == 1


def test_parse_html_extracts_main_text_and_links():
    title, text, links = ingest.parse_html(
        "<html><head><title>T</title></head><body><nav>menu</nav><main><h1>Hi</h1>"
        "<a href='/x#frag'>x</a></main></body></html>", "https://d.test/guide/")
    assert title == "T" and "menu" not in text and "Hi" in text
    assert links == ["https://d.test/x"]
