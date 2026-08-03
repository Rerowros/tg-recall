# Private Wiki Memory: Query First, Verify Raw Evidence

Wiki memory is a local derived layer for repeated Telegram research. It is not
an external knowledge service, does not publish content, and does not replace
the raw archive. Telegram messages and transcripts remain authoritative.

## Agent workflow

Use this order for a research task:

1. Query a compact wiki page for the requested person, relationship, project,
   decision, or communication style.
2. Check the returned `freshness`, `snapshot_id`, `updated_at`, and
   `tg://` citations before using it as context.
3. Treat an `observed` assertion as a cited summary of its source. Treat a
   `hypothesis` as an explicitly uncertain inference; its `confidence` is not
   proof.
4. Expand the cited assertion to bounded original source records when the page
   is stale, incomplete, contested, or material to the answer.
5. Present Telegram-specific conclusions with the raw `tg://` citations, not
   only a page title or an uncited wiki statement.

A current page is a compact starting point, not a permission to skip source
verification when correctness matters. A stale page means a newer committed raw
snapshot exists for the same authorized scope; use its citations and then
refresh the derived page through the future integration layer.

## Current core API

The repository currently provides a pure local core in
`tg_recall.wiki_memory`. It has no SQLite handle, Telegram client, network
provider, CLI command, or MCP tool. The caller supplies all authorized input:

```python
from tg_recall.wiki_memory import (
    AuthorizedWikiScope,
    RawSourceRecord,
    WikiAssertion,
    WikiMemoryStore,
    WikiPageDraft,
)

scope = AuthorizedWikiScope("work", "project-a", (-1001234567890,))
store = WikiMemoryStore(profile_local_wiki_root)

# Records and drafts must already be authorized by the caller.
result = store.compile(scope, records, page_drafts, created_at="2026-08-03T12:00:00Z")
hits = store.lookup(scope, "decision", limit=8)
raw_records = store.expand_assertion(scope, hits[0].assertions[0], read_only_resolver, limit=8)
```

`compile` writes immutable JSONL snapshots and versioned Markdown revisions
below the caller-owned wiki root. It accepts only records inside the explicit
profile/scope/chat boundary. `lookup` returns a compact excerpt plus freshness,
revision, snapshot, assertion, confidence, and citation metadata.
`expand_assertion` calls an injected read-only resolver with only the selected
assertion citations and bound; it cannot query Telegram itself.

The core keeps profile and scope identities in both artifact layout and page
metadata. It rejects path traversal, symlink escapes, malformed citations,
cross-chat record identities, conflicting artifacts, and uncommitted partial
revisions. The final manifest is the commit marker, so an interrupted compile
does not become visible to lookup.

Each `compile` input is the complete current source set for its authorized
scope, not merely an append-only batch. A message edit with the same cursor but
a changed `source_version` is a delta and makes earlier derived revisions
stale. If a previously committed source is absent, compilation stops with an
explicit deletion/invalidation requirement rather than silently treating old
wiki content as current.

## Privacy boundary

Pass a private, profile-local wiki root owned by the caller. Do not point the
core at a repository directory, a shared cloud folder, or a public export
location. The module does not persist Codex transcripts, hidden reasoning,
credentials, Telegram sessions, or provider prompts. It stores only supplied
raw evidence, derived page assertions, deterministic metadata, and citations.

## Integration status

There is still no `tg-recall wiki ...` CLI command, no MCP wiki tool, no SQLite
migration/catalog repository, and no automatic compiler run after sync. The
private pack adapter may load only explicit immutable revision IDs through
`WikiMemoryStore.load_revisions`; it does not invent or compile wiki content.
Adapters must obtain an explicit policy-authorized scope before constructing
`AuthorizedWikiScope`, keep data below the selected profile, and preserve the
query-first/raw-verification workflow described above.
