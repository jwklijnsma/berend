# Speed

- **Model stays warm.** It is pre-loaded at startup and kept in memory (`OLLAMA_KEEP_ALIVE`).
  `OLLAMA_THINK=false` skips reasoning on qwen3-style models.
- **Streaming.** Answers stream into chat; independent tool calls run in parallel, and query
  embeddings are cached.
- **Incremental re-index.** It skips unchanged GitLab blobs without downloading them, and
  unchanged files and pages too. Downloads (`INGEST_WORKERS`) and embeddings (`EMBED_WORKERS`)
  run in parallel. Deleted docs are pruned, unless the source looks down.
