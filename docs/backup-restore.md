# Backup And Restore

The local archive is intentionally file-based so it can be backed up without a service.

## What To Back Up

- `.tg-ecosystem/config.json`: local configuration. Contains sensitive Telegram and provider settings.
- `.tg-ecosystem/telegram.session`: Telegram MTProto session. Treat as account access.
- `.tg-ecosystem/archive.sqlite3`: local archive database.
- `.tg-ecosystem/archive.sqlite3-wal` and `.tg-ecosystem/archive.sqlite3-shm`: SQLite WAL files if present while the app is running.
- `.tg-ecosystem/media/`: downloaded media and derived audio/transcript artifacts.

## Safe Backup Procedure

1. Stop sync, media download, transcription, MCP, and CLI jobs.
2. Run:

   ```powershell
   uv run tg-ecosystem security check --fix
   ```

3. Copy the `.tg-ecosystem` directory to an encrypted local backup location.
4. Do not upload the Telegram session file to shared cloud storage unless that storage is encrypted and private.

## Restore Procedure

1. Install dependencies with `uv sync --extra dev`.
2. Restore `.tg-ecosystem` into the project directory, or set `TG_ECOSYSTEM_HOME` to the restored path.
3. Run:

   ```powershell
   uv run tg-ecosystem security check --fix
   uv run tg-ecosystem telegram check
   uv run tg-ecosystem index rebuild
   ```

4. If Telegram session validation fails, delete only the session file and rerun `uv run tg-ecosystem telegram auth`.

## Emergency Revoke

If the session file may have leaked:

1. Open Telegram on a trusted device.
2. Go to Settings -> Devices.
3. Terminate the suspicious session.
4. Delete the local `.session` file and reauthorize.
