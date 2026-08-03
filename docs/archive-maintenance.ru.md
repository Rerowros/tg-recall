# Обслуживание архива

[English canonical](archive-maintenance.md) | **Русский**

`tg-recall` записывает локальные SQLite migrations в `schema_migrations` с
version, checksum и completion time. Migrations движутся только вперёд. Перед
обновлением или широким ремонтом queue создайте essential backup:

```powershell
tg-recall backup create --mode essential --output D:\Backups\tg-recall-essential.zip
tg-recall --json doctor
```

Если `doctor` сообщает, что schema новее установленного приложения, не
понижайте версию и не редактируйте SQLite вручную. Сохраните backup и
запустите совместимый более новый release. Failed migration транзакционна;
последующие steps не применяются.

Проверьте состояние queue без изменений:

```powershell
tg-recall --json jobs --stage transcription --status retry --retryable true --limit 50
tg-recall --json jobs --repair
```

Повторяйте только явно eligible jobs. Jobs с будущим `retry_after` остаются в
backoff, пока человек не подтвердит override:

```powershell
tg-recall --json jobs --retry 42
tg-recall jobs --retry 42 --override-retry-after --confirm-risk "I understand this can change my local Telegram archive"
```

`jobs --repair` — dry-run. Для `--repair --apply` требуется то же человеческое
подтверждение; audit содержит только action counts и identifiers. Permanent
failures не возвращаются в queue неявно.
