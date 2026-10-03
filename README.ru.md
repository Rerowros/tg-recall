# tg-recall

[English](README.md) | [Русский](README.ru.md)

[![CI](https://github.com/Rerowros/tg-recall/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Rerowros/tg-recall/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](pyproject.toml)
[![PyPI](https://img.shields.io/badge/PyPI-coming%20soon-lightgrey.svg)](https://pypi.org/project/tg-recall/)
<!-- After the first PyPI release, replace the PyPI badge image with https://img.shields.io/pypi/v/tg-recall.svg -->

Локальный архив Telegram, к которому AI-агенты обращаются дёшево по токенам. `tg-recall` скачивает выбранные вами чаты в локальную базу SQLite и даёт Claude Code, Codex, Cursor и другим агентам несколько небольших инструментов через MCP или CLI: `search`, `read`, `chats` и, если вы разрешите, `sync`. Ответы — компактный текст, одна строка на сообщение, в пределах бюджета токенов, со ссылками `tg://chat/<id>/message/<id>` на исходные сообщения.

Telegram он только читает: никогда ничего не отправляет, не редактирует и не помечает прочитанным.

> Alpha (текущий релиз: v0.7.0). В архиве лежат личные переписки и пользовательская Telegram-сессия: храните профиль локально, используйте полное шифрование диска и проверяйте важные выводы в Telegram. Обновляетесь с v0.6 или старше? В v0.7.0 удалено много функций — прочитайте [журнал изменений](CHANGELOG.ru.md) и сначала сделайте backup.

## Установка

Нужен Python 3.13+.

```powershell
uv tool install https://github.com/Rerowros/tg-recall/releases/download/v0.7.0/tg_recall-0.7.0-py3-none-any.whl
```

Ставится универсальный wheel из [GitHub Release](https://github.com/Rerowros/tg-recall/releases) (сверьте SHA-256, который показывает GitHub). Обновление — та же команда с URL нового релиза и `--force`. `uv tool install tg-recall` / `pip install tg-recall` заработают, когда пакет появится в PyPI.

## Быстрый старт

Создайте API credentials на [my.telegram.org](https://my.telegram.org), затем в интерактивном терминале:

```powershell
tg-recall setup
tg-recall config set telegram.api_id 123456
tg-recall config set telegram.api_hash "your_api_hash"
tg-recall config set telegram.phone "+10000000000"
tg-recall telegram auth
tg-recall chats --refresh
tg-recall sync -1001234567890 --since 2026-01-01
tg-recall search "deadline"
```

`chats --refresh` загружает список чатов из Telegram; просто `chats` показывает чаты из архива с темами форума (`--all` — ещё и чаты без сообщений).

`sync` принимает одну или несколько целей: id чата, фрагмент названия, ссылку t.me (`t.me/<username>/<topic>`, `t.me/c/<id>/<topic>`) или `<chat>/<topic>`. Все цели идут через одно подключение к Telegram:

- Чат, который ещё ни разу не синхронизировался, начинается с 30 дней назад. `--since` (ISO-дата или `30d`) докачивает и более старую историю; следующие запуски получают только новые сообщения.
- Тема форума скачивается отдельно, а не вся группа.
- `--max-seconds` (по умолчанию `3600`) ограничивает один запуск; прерывание безопасно — просто запустите снова. Лимит Telegram (FloodWait) сохраняется и учитывается при следующем запуске.
- Lock-файл не пускает второй процесс в ту же Telegram-сессию; он завершается с `busy`.
- Без целей `sync` обновляет чаты из `ai_access.allowed_chat_ids` или все чаты, в которых уже есть сообщения.
- `--media voice,audio` (или `all`) ещё и ставит медиа в очередь; скачайте их через `media download` и расшифруйте через `transcribe run`.

`search`, `read`, `chats` и `sync` выводят тот же компактный текст, что агенты получают через MCP:

```text
2 hits · 2 chats · tz +04 · archive synced 5m ago · ~120 tok
## Work (-1001234567890)
-- 09-30 --
>1 05:19 Mark: deadline is friday
 2 05:24 я: ok, noted
## Partners (-1009876543210)
-- 10-03 --
>7 04:19 Ann: deadline moved to Monday
cite: tg://chat/-1001234567890/message/1 tg://chat/-1009876543210/message/7
```

`>` отмечает совпадение, `↩N` — ответ на сообщение N, `…[+N]` — сокращённое сообщение (`read REF --full` покажет целиком), строка `cite:` перечисляет ссылки. `read` без аргументов показывает новое с вашего прошлого `read` (первый вызов — последние 24 часа); `read --chat T --since 7d` читает период; `read REF` — сообщения вокруг ссылки. С `--json` эти команды возвращают `{"text", "count", "chat_ids"}`.

## Подключение к Claude Code / Codex / Cursor

`tg-recall-mcp` — stdio MCP-сервер поверх локального архива. Пока вы не включите [доступ для AI](#доступ-для-ai), он ничего не возвращает.

Claude Code:

```powershell
claude mcp add --scope user tg-recall -- tg-recall-mcp
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.tg-recall]
command = "tg-recall-mcp"
args = []
```

Cursor (`~/.cursor/mcp.json` или проектный `.cursor/mcp.json`) и другие клиенты с JSON-форматом `mcpServers`:

```json
{
  "mcpServers": {
    "tg-recall": { "command": "tg-recall-mcp", "args": [] }
  }
}
```

Чтобы использовать не default-профиль, задайте `TG_RECALL_PROFILE` в окружении сервера. После изменения MCP-конфига перезапустите клиент; правки `ai_access` запущенный сервер подхватывает без перезапуска.

`tg-recall-mcp` завершается по EOF на stdin, когда умирает родительский процесс, через `TG_RECALL_MCP_UNUSED_TIMEOUT_SEC` секунд (по умолчанию `600`) без `tools/call` или через `TG_RECALL_MCP_IDLE_TIMEOUT_SEC` секунд (по умолчанию `1800`) без запросов. Таймаут `0` отключает соответствующую проверку, `TG_RECALL_MCP_PARENT_WATCHDOG=0` — слежение за родительским процессом. Клиент может перезапустить сервер при следующем вызове.

## Доступ для AI

Агенты ничего не видят, пока вы, владелец, не разрешите конкретные чаты:

```powershell
tg-recall config set ai_access.enabled true
tg-recall config set ai_access.allowed_chat_ids "-1001234567890,-1009876543210"
```

Чтобы вместо этого разрешить все чаты архива, задайте `ai_access.allow_all_chats` равным `true`. Чтобы агенты сами докачивали из Telegram недостающие чаты, темы или периоды:

```powershell
tg-recall config set ai_access.allow_sync true
```

Инструменты MCP:

- `search(query)` — совпадения с автором, временем, соседним контекстом и ссылками. Необязательно: `chats`, `since`, `until`, `from`, `media`, `context`, `limit`, `budget`.
- `read()` — новое с прошлого чтения этим клиентом (первый вызов — последние 24 часа), поровну на каждый чат. `read(chats)` — последние сообщения, `read(chats, since, until)` — период, `read(refs)` — окна вокруг ссылок (`full=true` — без обрезки текста).
- `chats()` — разрешённые чаты с числом сообщений, последней активностью, возрастом синхронизации и темами форума.
- `sync(chats, since)` — только при `allow_sync`. Обновляет чаты или темы (без `chats` — все разрешённые), только читает Telegram, ограничен `sync_max_seconds` на вызов; частичный результат продолжается при следующем вызове.

`chats` принимает id, фрагменты названий, ссылки t.me и `<chat>/<topic>`; `chat_id` работает как алиас. На неизвестный аргумент возвращается ошибка с подсказкой «did you mean». После `tg-recall telegram check` собственные сообщения владельца показываются как `я`. При `allow_sync` инструменты `search` и `read` сначала подтягивают новые сообщения для чатов, синхронизированных больше `auto_refresh_minutes` назад; если это не удалось (сессия занята, лимит Telegram), ответ строится по архиву с пометкой.

CLI работает по тем же правилам. Вы в своём терминале видите все чаты архива. Внутри оболочки AI-агента (`CLAUDECODE`, `AI_AGENT` или `CODEX_*` без TTY, `TG_RECALL_AI_MODE=1`) команды `search`, `read`, `chats` и `sync` видят только чаты из `ai_access`.

Что агентам можно: искать, читать и просматривать список разрешённых чатов; синхронизировать их, если включён `allow_sync`; из CLI — ещё `export` одного разрешённого чата в каталог `exports` профиля и `media materialize` или `transcribe run --citation` для одной разрешённой ссылки `tg://`.

Что агентам нельзя: менять конфигурацию или credentials, входить в Telegram, обновлять список чатов из Telegram, удалять данные, делать backup или восстановление, обслуживать очередь или индекс и что-либо отправлять в Telegram. Текст сообщений доходит до них как недоверенные данные, а не инструкции.

| Ключ `ai_access` | По умолчанию | Что делает |
| --- | --- | --- |
| `enabled` | `false` | Главный выключатель |
| `allowed_chat_ids` | пусто | Чаты, доступные агентам |
| `allow_all_chats` | `false` | Разрешены все чаты архива (список игнорируется) |
| `max_results` | `20` | Максимум совпадений в `search` |
| `max_read_messages` | `200` | Максимум сообщений за один `read` |
| `allowed_since`, `allowed_until` | нет | Границы дат для агентов |
| `allowed_media_types` | `all` | Типы медиа, доступные агентам |
| `instructions_list_chats` | `false` | Названия разрешённых чатов в инструкциях MCP (тратит токены в каждой сессии) |
| `allow_sync` | `false` | Включает `sync` и автообновление |
| `sync_max_seconds` | `50` | Лимит времени одного вызова MCP `sync` |
| `auto_refresh_minutes` | `10` | Обновлять чаты старше этого перед `search` и `read` (`0` — выключить) |

`config set` принимает списки как `1,2`, `[1, 2]` или `1 2`.

## Команды

| Команда | Что делает |
| --- | --- |
| `setup` | Создаёт конфиг профиля и базу данных |
| `doctor` | Проверяет архив, схему, Telegram-сессию и инструменты транскрипции |
| `config show`, `config set KEY VALUE` | Показывает конфиг без секретов или меняет значение |
| `telegram auth`, `telegram check` | Вход (интерактивно) или проверка сохранённой сессии |
| `chats [QUERY] [--all] [--refresh]` | Список чатов и тем форума |
| `sync [TARGET...] [--since] [--max-seconds] [--media]` | Скачивает новые сообщения, а с `--since` и более старую историю |
| `search QUERY [--chat T]... [--since] [--until] [--from] [--media] [--context] [--limit] [--budget]` | Ищет сообщения с контекстом и ссылками |
| `read [REF...] [--chat T]... [--since] [--until] [--before] [--after] [--full] [--limit]` | Новые сообщения, период или окна вокруг ссылок |
| `export --chat ID` | Записывает один чат в JSONL в каталог `exports` профиля |
| `media usage`, `media download`, `media materialize --citation REF` | Место на диске под медиа, скачивание очереди медиа, медиа одного сообщения |
| `transcribe run` | Расшифровка голосовых, аудио и видео (см. [локальную транскрипцию](docs/local-transcription.ru.md)) |
| `jobs` | Просмотр, повтор и ремонт очереди медиа и транскрипции |
| `index rebuild` | Пересборка полнотекстового индекса |
| `security check [--fix]` | Проверка приватных прав на файлы |
| `backup create`, `backup restore` | Согласованный ZIP-backup или восстановление в профиль |
| `purge --chat-id ID`, `purge --all` | Удаление локальных данных архива |

Глобальные опции пишутся перед командой: `--json`, `--profile NAME`, `--home PATH`, `--config PATH`. Например, `tg-recall --json doctor`.

## Локальное хранилище

`tg-recall` по умолчанию никогда не записывает архив в репозиторий или текущий каталог.

| Система | Конфигурация | Постоянные данные | Состояние | Кэш |
| --- | --- | --- | --- | --- |
| Windows | `%LOCALAPPDATA%\tg-recall\config` | `%LOCALAPPDATA%\tg-recall\data` | `%LOCALAPPDATA%\tg-recall\state` | `%LOCALAPPDATA%\tg-recall\cache` |
| Linux | `~/.config/tg-recall` | `~/.local/share/tg-recall` | `~/.local/state/tg-recall` | `~/.cache/tg-recall` |

Каждый Telegram-аккаунт — это профиль. Его SQLite-архив, сессия, медиа и exports лежат в `data/profiles/<profile>/`. Медиа хранится по SHA-256 с относительными ключами, поэтому профиль можно восстановить на другой ОС.

Для зашифрованного внешнего диска или portable-установки используйте самодостаточный root:

```powershell
tg-recall --home D:\Private\tg-recall --profile work setup
```

Приоритет: `--home`, `TG_RECALL_HOME`, затем системные значения по умолчанию. Приоритет профиля: `--profile`, `TG_RECALL_PROFILE`, настроенный активный профиль, затем `default`.

## Приватность и безопасность

- Хранятся только чаты, которые вы синхронизировали, и только на вашем компьютере. Поиск — локальный полнотекстовый (SQLite FTS5); сам `tg-recall` не отправляет текст ни одному AI-провайдеру.
- Для credentials и сессии выставляются приватные права на файлы по мере возможности (`security check --fix`). Для шифрования данных на диске используйте BitLocker на Windows или LUKS на Linux.
- Решения политики доступа агентов попадают в audit только с операцией и идентификаторами scope — без текста запроса, содержимого сообщений, credentials и сессии.
- Локальный Whisper не входит в пакет; провайдер транскрипции `telegram` просит расшифровать сам Telegram. См. [локальную транскрипцию](docs/local-transcription.ru.md).
- Если сессия могла утечь, отзовите её в Telegram Settings -> Devices и снова выполните `telegram auth`. Об уязвимостях сообщайте так, как описано в [SECURITY.ru.md](SECURITY.ru.md).

Делайте backup встроенными командами, а не копированием работающей базы:

```powershell
tg-recall backup create --mode essential --output D:\Backups\tg-recall-essential.zip
tg-recall backup create --mode full --include-session --output D:\Backups\tg-recall-full.zip
tg-recall backup restore D:\Backups\tg-recall-essential.zip --profile restored
```

`essential` содержит базу данных и конфигурацию профиля, `full` — ещё и медиа. Сессия и credentials попадают в backup только с `--include-session`.

## Документация

- [Журнал изменений](CHANGELOG.ru.md)
- [Локальная транскрипция](docs/local-transcription.ru.md)
- [Backup и восстановление](docs/backup-restore.ru.md)
- [Обслуживание архива](docs/archive-maintenance.ru.md)
- [Политика безопасности](SECURITY.ru.md)
- [Участие в разработке](CONTRIBUTING.ru.md)
- [Roadmap](ROADMAP.ru.md)

## Разработка

```powershell
git clone https://github.com/Rerowros/tg-recall.git
cd tg-recall
uv sync --extra dev
uv run pytest -q
uv build
```
