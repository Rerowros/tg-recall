# Private AI Export Packs

**English canonical** | [Русский](ai-export-packs.ru.md)

An AI export pack is a bounded, immutable directory handoff for already
authorized evidence. It is designed for a local AI workflow or offline review,
not for automatic whole-archive export, cloud publication, or backup/restore.

## What a pack can contain

| Pack kind | Contents | How to treat it |
| --- | --- | --- |
| `raw` | Scoped `raw/evidence.jsonl` with stable `tg://` citations | Primary Telegram evidence. |
| `wiki` | Derived Markdown plus assertion/source mappings | Compact derived knowledge; inspect freshness and citations. |
| `mixed` | Both raw evidence and derived wiki files | Use wiki for navigation, raw evidence for verification. |
| backup | Database/configuration and optionally media/session under backup policy | Not an AI pack; use backup/restore commands. |

Derived Markdown is never a replacement for the cited raw record. Consumers
must check each derived file's `freshness` and `source_snapshot_ids`, then use
the declared `tg://` citations to verify material claims.

## Privacy and scope

`ExportScope` requires positive `max_records` and `token_budget`, resolved
concrete chat IDs, and at least one date boundary. A saved-scope name is only
provenance metadata in the manifest: the CLI or integration must resolve it to
those concrete limits before it invokes the core builder. This prevents an
implicit full-archive handoff.

The core builder writes by default to `default_exports_dir / name`. The CLI
supplies this as the selected profile's private exports directory. An outside
destination is allowed only through explicit human `--output`; automation is
limited to the profile exports directory. Its absolute location is not embedded
in portable pack content. Existing destinations are refused rather than
overwritten.

The manifest and content use normalized logical paths, profile aliases rather
than private filesystem paths, SHA-256 hashes, file sizes, citations, snapshot
IDs, scope metadata, and a schema version. Strict offline verification rejects
tampering, undeclared files, duplicate or traversal paths, and symbolic links.
Before creating a staging directory, the builder serializes every raw and wiki
artifact plus the manifest and applies one conservative total-byte cap against
`token_budget`; refusal therefore leaves no partial generated pack.

## Current core API

The current API is `tg_recall.export_packs`; it has no database, Telegram,
wiki-store, CLI, or MCP dependency. A caller passes explicit raw evidence and
optional `WikiPage` values that it has already read and authorized:

```python
from tg_recall.export_packs import ExportScope, WikiPage, build_pack, inspect_pack, verify_pack

scope = ExportScope(
    chat_ids=(-1001234567890,),
    since="2026-08-01T00:00:00Z",
    max_records=200,
    token_budget=12_000,
)
result = build_pack(
    name="project-a-evidence",
    scope=scope,
    raw_evidence=authorized_records,
    wiki_pages=authorized_wiki_pages,
    profile_alias="work",
    default_exports_dir=profile_private_exports_dir,
)
inspect_pack(result.path)
verify_pack(result.path, strict=True)
```

`verify_pack` is offline: it needs neither Telegram nor the source profile.
For a derived wiki age policy, pass `max_wiki_age_seconds`; stale derived files
then fail verification. A rebuild can use `previous_pack`, but only
hash-verified unchanged generated files are reused; previous packs are never
mutated.

## Not a backup and not an automatic export

An export pack does not contain the complete archive by implication, Telegram
credentials, sessions, or raw media binaries by default. It is not a recovery
format and must not be substituted for `tg-recall backup create` / `backup
restore`. Keep actual backups encrypted and outside the active profile as
described in [backup and restore](backup-restore.md).

## CLI integration

The additive CLI namespace is `tg-recall pack`; the existing `tg-recall export`
JSONL workflow remains separate and compatible:

```powershell
tg-recall --json pack create project-a --chat -1001234567890 --since 2026-01-01 --max-records 200 --token-budget 12000
tg-recall --json pack create project-a-wiki --scope project-a --max-records 200 --token-budget 12000 --wiki-revision REVISION_ID
tg-recall --json pack inspect PATH\TO\project-a
tg-recall --json pack verify PATH\TO\project-a --max-wiki-age-seconds 604800
```

`create` obtains raw evidence through bounded SQLite filters. Wiki content is
opt-in: each selected immutable revision is loaded only from the profile-local
logical wiki path, checked against its committed snapshot and revision hash, and
rendered with structured assertions/citations. `inspect` and `verify` are
offline and do not need Telegram or the source archive. In automation, creation
uses the central `ARCHIVE_EXPORT` policy and all pack paths must remain below
the selected profile's exports directory. There is no MCP pack tool, automatic
wiki compilation, or automatic whole-archive export.
