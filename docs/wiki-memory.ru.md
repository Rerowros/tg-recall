# Приватная Wiki Memory: сначала query, затем проверка raw evidence

[English canonical](wiki-memory.md) | **Русский**

Wiki memory — локальный derived layer для повторяющихся Telegram research. Это
не external knowledge service, она не публикует content и не заменяет raw
archive. Authoritative остаются Telegram messages и transcripts.

## Agent workflow

Для research task используйте этот порядок:

1. Сделайте query компактной wiki page для запрошенного person, relationship,
   project, decision или communication style.
2. Проверьте возвращённые `freshness`, `snapshot_id`, `updated_at` и citations
   `tg://`, прежде чем использовать page как context.
3. Считайте assertion `observed` процитированным summary своего source.
   Считайте `hypothesis` явно uncertain inference; его `confidence` не является
   proof.
4. Раскройте процитированную assertion до bounded original source records,
   если page stale, incomplete, contested или существенна для ответа.
5. Представляйте Telegram-specific conclusions с raw citations `tg://`, а не
   только с page title или непроцитированным wiki statement.

Current page — компактная отправная точка, но не разрешение пропускать source
verification, когда важна correctness. Stale page означает, что для того же
authorized scope существует более новый committed raw snapshot; используйте его
citations, затем обновите derived page через будущий integration layer.

## Текущий core API

Сейчас repository предоставляет pure local core в `tg_recall.wiki_memory`. У
него нет SQLite handle, Telegram client, network provider, CLI command или MCP
tool. Caller передаёт весь authorized input:

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

`compile` пишет immutable JSONL snapshots и versioned Markdown revisions ниже
caller-owned wiki root. Он принимает только records внутри explicit
profile/scope/chat boundary. `lookup` возвращает compact excerpt, а также
metadata freshness, revision, snapshot, assertion, confidence и citation.
`expand_assertion` вызывает injected read-only resolver только с citations
selected assertion и bound; самостоятельно он не может запрашивать Telegram.

Core хранит profile и scope identities и в artifact layout, и в page metadata.
Он отклоняет path traversal, symlink escapes, malformed citations, cross-chat
record identities, conflicting artifacts и uncommitted partial revisions. Final
manifest — commit marker, поэтому interrupted compile не становится видимым
для lookup.

Каждый input `compile` — complete current source set для его authorized scope,
а не лишь append-only batch. Message edit с тем же cursor, но изменённым
`source_version`, является delta и делает earlier derived revisions stale. Если
ранее committed source отсутствует, compilation останавливается с explicit
deletion/invalidation requirement вместо тихой трактовки old wiki content как
current.

## Privacy boundary

Передавайте private, profile-local wiki root, принадлежащий caller. Не указывайте
для core repository directory, shared cloud folder или public export location.
Module не сохраняет Codex transcripts, hidden reasoning, credentials, Telegram
sessions или provider prompts. Он хранит только supplied raw evidence, derived
page assertions, deterministic metadata и citations.

## Статус integration

По-прежнему нет команды CLI `tg-recall wiki ...`, MCP wiki tool, SQLite
migration/catalog repository или automatic compiler run после sync. Private
pack adapter может загружать только explicit immutable revision IDs через
`WikiMemoryStore.load_revisions`; он не изобретает и не компилирует wiki
content. Adapters должны получить explicit policy-authorized scope до создания
`AuthorizedWikiScope`, хранить data ниже selected profile и сохранять
описанный выше query-first/raw-verification workflow.
