# Roadmap

## Near Term

- Make message sync truly incremental and support explicit historical backfill.
- Deduplicate active media-download jobs and expose repair/retry controls.
- Add a first-class local Whisper provider with timestamped segments.
- Replace token-overlap semantic search with an optional local embedding backend.
- Add agent-oriented batch retrieval that returns compact, deduplicated evidence windows.

## Private Agent Wiki Memory

`add-private-agent-wiki-memory` is the next OpenSpec change. It will keep a private Markdown knowledge layer under the selected profile's `data/profiles/<profile>/wiki/` above the raw archive:

```text
raw Telegram snapshots -> cited Markdown wiki -> compact agent queries
```

The wiki will contain evidence-backed pages for people, relationships, projects, decisions, and communication styles. It will remain local, ignored by Git, and omitted from package artifacts. Every statement must retain Telegram citations, a timestamp, confidence, and the source snapshot that produced it.

The design is intentionally compatible with LLM-Wiki-style Markdown workflows, but `tg-recall` will not require an external wiki dependency.
