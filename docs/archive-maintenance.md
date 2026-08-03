# Archive maintenance

`tg-recall` records local SQLite migrations in `schema_migrations` with a
version, checksum and completion time. Migrations only move forward. Before an
upgrade or a broad queue repair, create an essential backup:

```powershell
tg-recall backup create --mode essential --output D:\Backups\tg-recall-essential.zip
tg-recall doctor --json
```

If `doctor` reports a schema newer than the installed application, do not
downgrade or edit SQLite manually. Keep the backup and run a compatible newer
release. A failed migration is transactional; later steps are not applied.

Inspect queue state without changing it:

```powershell
tg-recall jobs --stage transcription --status retry --retryable true --limit 50 --json
tg-recall jobs --repair --json
```

Retry only explicit eligible jobs. Jobs with a future `retry_after` remain in
backoff unless a human confirms the override:

```powershell
tg-recall jobs --retry 42 --json
tg-recall jobs --retry 42 --override-retry-after --confirm-risk "I understand this can change my local Telegram archive"
```

`jobs --repair` is dry-run. `--repair --apply` requires the same human
confirmation and audits only action counts and identifiers. Permanent failures
are not implicitly requeued.
