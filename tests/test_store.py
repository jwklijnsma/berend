from berend import store

RUNBOOK = """# Payments

Intro text about the payments platform.

## Troubleshooting

### ImagePullBackOff

Check the pull secret `registry-creds` in the namespace, then run oc rollout restart.

```bash
# this is a comment, not a heading
oc get secret registry-creds
```

## Contacts

Team Platform.
"""


def test_sections_keep_heading_path_and_ignore_code_comments():
    secs = dict(store.sections(RUNBOOK))
    assert "Payments > Troubleshooting > ImagePullBackOff" in secs
    body = secs["Payments > Troubleshooting > ImagePullBackOff"]
    assert "# this is a comment" in body          # stayed in the section, not a new heading
    assert "Payments > Contacts" in secs


def test_chunks_carry_breadcrumbs():
    chunks = store.chunk_document(RUNBOOK)
    assert any(c.startswith("[Payments > Troubleshooting > ImagePullBackOff]") for c in chunks)
    assert store.chunk_document("plain text\n\nno headings") == ["plain text\n\nno headings"]


def test_tokenize_splits_compound_names():
    toks = store.tokenize("cert-manager failed on openshift-gitops")
    assert {"cert-manager", "cert", "manager", "openshift-gitops", "gitops"} <= set(toks)


def test_hybrid_search_finds_exact_error_codes():
    store.add_document(RUNBOOK, "file:///payments.md", "payments.md", "local")
    for i in range(10):
        store.add_document(f"General notes number {i} about pods and deployments in the cluster.",
                           f"file:///noise{i}.md", f"noise{i}.md", "local")
    hits = store.search("ImagePullBackOff")
    assert hits[0]["title"] == "payments.md"
    assert hits[0]["match"] in ("keyword", "both")


def test_search_filters_by_kind_and_diversifies_sources():
    store.add_document(RUNBOOK, "file:///payments.md", "payments.md", "local")
    store.add_document("Payments wiki: ImagePullBackOff happens when the registry is down.",
                       "https://gitlab/wiki/payments", "payments wiki", "gitlab")
    assert {h["kind"] for h in store.search("ImagePullBackOff", kind="gitlab")} == {"gitlab"}
    top2 = store.search("payments ImagePullBackOff", k=2)
    assert len({h["source"] for h in top2}) == 2


def test_incremental_add_and_delete():
    assert store.add_document("hello world", "s1", "t", "local") == 1
    assert store.add_document("hello world", "s1", "t", "local") == 0          # unchanged
    assert store.add_document("hello again", "s1", "t", "local") == 1          # changed
    assert store.count() == 1
    store.delete_sources(["s1"])
    assert store.count() == 0
    assert store.search("hello") == []
