# Приватные AI export packs

[English canonical](ai-export-packs.md) | **Русский**

AI export pack — ограниченная неизменяемая передача каталога с уже
авторизованными evidence. Он предназначен для local AI workflow или offline
review, а не для automatic whole-archive export, cloud publication или
backup/restore.

## Что может содержать pack

| Pack kind | Содержимое | Как использовать |
| --- | --- | --- |
| `raw` | Ограниченный `raw/evidence.jsonl` со стабильными citations `tg://` | Первичное Telegram evidence. |
| `wiki` | Derived Markdown плюс assertion/source mappings | Компактное derived knowledge; проверяйте freshness и citations. |
| `mixed` | И raw evidence, и derived wiki files | Используйте wiki для навигации, raw evidence — для проверки. |
| backup | Database/configuration и опционально media/session по backup policy | Это не AI pack; используйте backup/restore commands. |

Derived Markdown никогда не заменяет процитированную raw record. Consumers
обязаны проверить `freshness` и `source_snapshot_ids` каждого derived file,
затем использовать объявленные citations `tg://` для проверки существенных
утверждений.

## Privacy и scope

`ExportScope` требует положительные `max_records` и `token_budget`, resolved
concrete chat IDs и как минимум одну date boundary. Имя saved-scope — только
provenance metadata в manifest: CLI или integration должны разрешить его в эти
конкретные limits до вызова core builder. Это предотвращает implicit
whole-archive handoff.

По умолчанию core builder пишет в `default_exports_dir / name`. CLI передаёт
сюда private exports directory выбранного profile. Destination вне него
разрешён только через явный человеческий `--output`; automation ограничена
exports directory profile. Его absolute location не встраивается в portable
pack content. Existing destinations отклоняются, а не перезаписываются.

Manifest и content используют normalized logical paths, profile aliases вместо
private filesystem paths, SHA-256 hashes, file sizes, citations, snapshot IDs,
scope metadata и schema version. Strict offline verification отклоняет
tampering, undeclared files, duplicate или traversal paths и symbolic links.
До создания staging directory builder сериализует каждый raw и wiki artifact,
а также manifest, и применяет один conservative total-byte cap к
`token_budget`; поэтому отказ не оставляет partial generated pack.

## Текущий core API

Текущий API — `tg_recall.export_packs`; он не зависит от database, Telegram,
wiki-store, CLI или MCP. Caller передаёт explicit raw evidence и optional
значения `WikiPage`, которые уже прочитал и авторизовал:

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

`verify_pack` работает offline: ему не нужны Telegram и source profile. Для
age policy derived wiki передайте `max_wiki_age_seconds`; тогда stale derived
files не пройдут verification. Rebuild может использовать `previous_pack`, но
повторно применяются только hash-verified unchanged generated files; previous
packs никогда не изменяются.

## Не backup и не automatic export

Export pack по умолчанию не содержит complete archive, Telegram credentials,
sessions или raw media binaries. Это не recovery format; он не заменяет
`tg-recall backup create` / `backup restore`. Храните actual backups
зашифрованными и вне active profile, как описано в [резервном копировании и
восстановлении](backup-restore.ru.md).

## CLI integration

Additive CLI namespace — `tg-recall pack`; существующий JSONL workflow
`tg-recall export` остаётся отдельным и совместимым:

```powershell
tg-recall --json pack create project-a --chat -1001234567890 --since 2026-01-01 --max-records 200 --token-budget 12000
tg-recall --json pack create project-a-wiki --scope project-a --max-records 200 --token-budget 12000 --wiki-revision REVISION_ID
tg-recall --json pack inspect PATH\TO\project-a
tg-recall --json pack verify PATH\TO\project-a --max-wiki-age-seconds 604800
```

`create` получает raw evidence через bounded SQLite filters. Wiki content
opt-in: каждая selected immutable revision загружается только из
profile-local logical wiki path, проверяется по committed snapshot и revision
hash, затем рендерится со structured assertions/citations. `inspect` и
`verify` работают offline и не нуждаются в Telegram или source archive. В
automation creation использует central `ARCHIVE_EXPORT` policy, а все pack
paths должны оставаться ниже exports directory выбранного profile. Нет MCP
pack tool, automatic wiki compilation или automatic whole-archive export.
