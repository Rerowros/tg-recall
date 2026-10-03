# Backup And Restore

**English canonical** | [Русский](backup-restore.ru.md)

Use the built-in backup commands rather than copying a live SQLite database and its WAL files manually.

```powershell
tg-recall backup create --mode essential --output D:\Backups\tg-recall-essential.zip
tg-recall backup create --mode full --output D:\Backups\tg-recall-full.zip
tg-recall backup create --mode full --include-session --output D:\Backups\tg-recall-full-session.zip
```

`essential` contains a consistent SQLite snapshot and the profile configuration. `full` additionally includes the deduplicated media object store. Session and credentials are intentionally absent unless `--include-session` is supplied.

Restore into a new profile by default:

```powershell
tg-recall backup restore D:\Backups\tg-recall-essential.zip --profile restored
tg-recall --json --profile restored doctor
```

Replacing an existing profile needs `--replace` and an interactive confirmation. Keep backups outside the active profile directory, encrypted and private. If a session may have leaked, revoke it in Telegram Settings -> Devices, then reauthorize locally.
