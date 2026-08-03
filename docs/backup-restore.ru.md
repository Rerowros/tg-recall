# Резервное копирование и восстановление

[English canonical](backup-restore.md) | **Русский**

Используйте встроенные команды резервного копирования вместо ручного копирования
работающей базы SQLite и её WAL-файлов.

```powershell
tg-recall backup create --mode essential --output D:\Backups\tg-recall-essential.zip
tg-recall backup create --mode full --output D:\Backups\tg-recall-full.zip
tg-recall backup create --mode full --include-session --output D:\Backups\tg-recall-full-session.zip
```

`essential` содержит согласованный снимок SQLite, конфигурацию profile и wiki.
`full` дополнительно включает дедуплицированное объектное хранилище media. Session
и credentials намеренно отсутствуют, если не передан `--include-session`.

По умолчанию восстанавливайте в новый profile:

```powershell
tg-recall backup restore D:\Backups\tg-recall-essential.zip --profile restored
tg-recall --json --profile restored doctor
```

Для замены существующего profile необходимы `--replace` и интерактивное
подтверждение. Храните backups вне active profile directory, в зашифрованном
виде и приватно. Если session мог быть скомпрометирован, отзовите его в
Telegram Settings -> Devices, затем заново авторизуйтесь локально.
