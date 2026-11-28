# Answers

- **Hybrid search.** Vector search finds the same thing phrased differently. A BM25 keyword
  index finds exact tokens that embeddings blur: `ImagePullBackOff`, `cert-manager`, hostnames,
  ticket numbers. The two rankings are merged with reciprocal rank fusion.
- **Heading-aware chunks.** Markdown is split per section, and each chunk starts with its path,
  e.g. `[Keycloak > Operations > Key rotation]`, so a lone paragraph keeps its context.
- **Only cited sources.** The links under an answer are the documents Berend actually named.
- **Threads.** Follow-ups in a thread Berend answered need no new @mention, in both Slack and
  Mattermost. The whole thread is context, and in an alert thread that includes the alert.
- **Feedback.** Slack has 👍/👎 buttons; in Mattermost you react 👍/👎. Votes go to
  `FEEDBACK_LOG` (JSONL) and the `berend_feedback_total` metric, so you can see where the docs fall short.
