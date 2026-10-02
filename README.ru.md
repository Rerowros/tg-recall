# tg-recall

[English](README.md) | [Русский](README.ru.md)

[![CI](https://github.com/Rerowros/tg-recall/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Rerowros/tg-recall/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](pyproject.toml)
[![PyPI](https://img.shields.io/badge/PyPI-coming%20soon-lightgrey.svg)](https://pypi.org/project/tg-recall/)
<!-- After the first PyPI release, replace the PyPI badge image with https://img.shields.io/pypi/v/tg-recall.svg -->

Local-first архив Telegram для людей и AI-агентов. `tg-recall` хранит только явно выбранные чаты в локальном профиле, индексирует текст сообщений и транскрипты и возвращает ссылки на источники вида `tg://chat/.../message/...`.

> Ранняя alpha (текущий релиз: v0.6.0). Архив включает приватные переписки и пользовательскую Telegram-сессию. Храните профиль локально, используйте полное шифрование диска и проверяйте важные выводы в Telegram.

## Установка

Требуются Python 3.13+ и [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/Rerowros/tg-recall.git
cd tg-recall
uv sync --extra dev
uv run tg-recall setup
```

Для обычного использования установите опубликованный универсальный wheel из
соответствующего GitHub Release. GitHub показывает SHA-256 digest ассета;
сверяйте его перед установкой, если ваш процесс релиза требует независимой
проверки целостности. GitHub asset digest — это метаданные целостности, а не
подпись пакета.

```powershell
uv tool install https://github.com/Rerowros/tg-recall/releases/download/v0.6.0/tg_recall-0.6.0-py3-none-any.whl
tg-recall --json doctor
```

`uv tool install --editable .` предназначен только для разработки. Он намеренно
оставляет checkout источником команды, поэтому `tg-recall update apply` сообщит
`manual_required`, а не перезапишет этот checkout. Обновляйте editable-установку
для разработки из её исходного checkout (`git pull`, `uv sync` или
документированный workflow проекта) либо переустановите проверенный release wheel.

Самообновление и установка harness — явные lifecycle-операции только для
человека; они никогда не выполняются автоматически. Проверки релизов по
умолчанию выключены. Только после явной периодической настройки подходящая
интерактивная не-JSON CLI-команда может выполнить best-effort проверку; help,
MCP, automation, lifecycle-команды и JSON-команды этого никогда не делают. См.
[интеграцию harness](docs/harness-integration.md) для использования `update` /
`integrate`, поддержки scope, backup и ручных fallback.

## Использование с Claude Code / Codex / Cursor

`tg-recall-mcp` — read-only stdio MCP-сервер. Пока вы явно не разрешите агентам конкретные чаты, он ничего не возвращает:

```powershell
tg-recall config set ai_access.enabled true
tg-recall config set ai_access.allowed_chat_ids "-1001234567890,-1009876543210"
```

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

Чтобы использовать не default-профиль, задайте `TG_RECALL_PROFILE` в окружении сервера. `tg-recall integrate install --target claude-code --scope user` (или `codex` / `cursor`) записывает ту же запись плюс инструкции агенту, с backup; см. [интеграцию harness](docs/harness-integration.md). После изменения MCP-конфига перезапустите клиент.

## Постоянный bootstrap для AI-harness

Чтобы Codex, Claude Code, Cursor или другой агент подготовил безопасный план
настройки harness, скопируйте эту точную инструкцию:

```text
Fetch https://raw.githubusercontent.com/Rerowros/tg-recall/main/docs/agent-setup/prompt.md and follow it.
```

Ссылка постоянная и намеренно не содержит версии релиза. Полученный контракт
находит последний стабильный GitHub Release и точный универсальный wheel с его
GitHub SHA-256 digest; он никогда не устанавливает из `main`. Агент может
использовать только `integrate list`, `integrate preview` и `integrate status`
как data-blind диагностику. Установка пакета и любые операции `integrate install`,
`refresh` или `uninstall` остаются явной командой человека: агент печатает её и
останавливается. Она требует явный scope `user` или `project` (и явный project
root для последнего), сохраняет manual/partial действия и сообщает пользователю
перезапустить затронутый harness перед проверкой через `status`.

Текущий опубликованный релиз может предшествовать этому контракту. Если его
команда `integrate list` недоступна, настройка останавливается с сообщением о
capability gap; она не должна использовать checkout или source из `main` как
fallback.

`tg-ecosystem` и `tg-ecosystem-mcp` — устаревшие compatibility aliases. В новых
скриптах используйте `tg-recall` и `tg-recall-mcp`; aliases могут быть удалены в
будущем breaking release.

## Локальное хранилище

`tg-recall` по умолчанию никогда не записывает архив в репозиторий.

| Система | Конфигурация | Постоянные данные | Состояние | Кэш |
| --- | --- | --- | --- | --- |
| Windows | `%LOCALAPPDATA%\tg-recall\config` | `%LOCALAPPDATA%\tg-recall\data` | `%LOCALAPPDATA%\tg-recall\state` | `%LOCALAPPDATA%\tg-recall\cache` |
| Linux | `~/.config/tg-recall` | `~/.local/share/tg-recall` | `~/.local/state/tg-recall` | `~/.cache/tg-recall` |

Каждый Telegram-аккаунт — это профиль. Его SQLite-архив, сессия, media objects,
wiki и exports остаются изолированы в `data/profiles/<profile>/`. Медиа
адресуется содержимым по SHA-256, а SQLite хранит относительный ключ, поэтому
профиль можно восстановить на другой ОС.

Используйте самодостаточный root для зашифрованного внешнего диска или portable
настройки:

```powershell
tg-recall --home D:\Private\tg-recall --profile work setup
```

Приоритет: `--home`, `TG_RECALL_HOME`, затем системные значения по умолчанию.
Приоритет профиля: `--profile`, `TG_RECALL_PROFILE`, настроенный активный профиль,
затем `default`.

## Настройка Telegram

Создайте API credentials на [my.telegram.org](https://my.telegram.org), затем
авторизуйтесь из интерактивного терминала:

```powershell
tg-recall config set telegram.api_id 123456
tg-recall config set telegram.api_hash "your_api_hash"
tg-recall config set telegram.phone "+10000000000"
tg-recall telegram auth
tg-recall telegram check
```

Для credentials и сессии применяются best-effort приватные права. Используйте
BitLocker на Windows или LUKS на Linux для шифрования данных при хранении.

## Workflow архива

Выведите список чатов, затем создайте scope с учётом policy:

```powershell
tg-recall chats list
tg-recall scopes create work --chat -1001234567890 --since 2026-01-01 --media voice,photo --transcribe auto
tg-recall sync run work --limit 500
tg-recall sync run work --backfill --limit 500
```

`media` принимает `none`, `voice`, `audio`, `photo`, `video`, `document`, `all`
или сочетание через запятую. Текст и метаданные медиа всегда индексируются;
выбранные исходные медиа сохраняются локально. Обычные запуски получают только
сообщения новее сохранённого watermark; `--backfill` явно продолжает в более
старую историю.

Для одного идемпотентного workflow:

```powershell
tg-recall --json sync ensure work --chat -1001234567890 --since 2026-01-01 --media voice,photo --transcribe auto --limit 500
```

`transcribe auto` сначала пытается использовать транскрипцию Telegram, затем
переходит на локальный Whisper. `tg-recall --json doctor` проверяет текущий
архив, Telegram-сессию, `ffmpeg` и исполняемый файл `whisper`.

## Локальная транскрипция

`transcribe run` по умолчанию использует `sidecar`; `telegram` использует
Telegram, `local` — выбранный локальный адаптер, а `auto` сначала обращается
к Telegram. О настройках локального движка, предустановках VAD для коротких
голосовых сообщений и длинных аудиозаписей, а также проверке одной ссылки на
источник см. [локальную транскрипцию](docs/local-transcription.ru.md).

## Workflow агента

Попросите агента начать отсюда:

```powershell
tg-recall agent guide
```

Для ограниченных доказательств со ссылками:

```powershell
tg-recall --json retrieve --chat-id -1001234567890 --query "deadline" --context 8 --token-budget 12000
```

Для анализа длинного чата запишите приватный JSONL export внутри профиля вместо
вывода всего архива:

```powershell
tg-recall export --chat -1001234567890 --since 2026-01-01 --include transcripts,media-metadata --format jsonl
```

CLI может синхронизировать, скачивать и транскрибировать локальные архивы. Он
не предоставляет agent-команды для аутентификации, изменения credentials, purge
или Telegram write-операций. MCP остаётся read-only.

Для ограниченной проверяемой офлайн-передачи в локальный AI-workflow создайте
отдельный pack; существующая JSONL-команда `export` не меняется:

```powershell
tg-recall --json pack create project-a --chat -1001234567890 --since 2026-01-01 --max-records 200 --token-budget 12000
tg-recall --json pack inspect PATH\TO\project-a
tg-recall --json pack verify PATH\TO\project-a
```

`pack create` требует конкретный чат и границу даты либо сохранённый scope, а
также всегда требует положительные budgets записей и токенов. По умолчанию он
записывает в приватную директорию `exports` выбранного профиля. Человек может
явно передать `--output`; automation не может. Чтобы включить синтезированное
локальное knowledge, передавайте только явные неизменяемые ID `--wiki-revision`
(и `--wiki-scope`, если он отличается от сохранённого scope). Packs содержат
выбранные доказательства и структурированные assertions, но никогда не содержат
сессии, credentials, медиа-бинарники или абсолютные пути хоста.

Установите managed Codex guidance вместо опоры на Custom Instructions:

```powershell
tg-recall integrate install --target codex --scope user
```

Используйте этот компактный текст только как generic/manual fallback, когда
управляемая интеграция harness недоступна:

```text
Если пользователь просит посмотреть Telegram-чат, используй локальный `tg-recall`: сначала выполни `tg-recall agent guide` и следуй его workflow только для запрошенных чатов. Разрешены sync, media download и transcription; запрещены auth, purge и изменение config. Выводы подтверждай ссылками `tg://`.
```

Для экономичного роутинга моделей Codex, progressive context budgets и полного
готового к копированию prompt см. [docs/codex-agent-optimization.md](docs/codex-agent-optimization.md). Руководство предпочитает доступную `gpt-5.3-codex-spark` для почти мгновенного ограниченного поиска с её отдельным лимитом Codex, а `gpt-5.6-luna` использует как стабильный недорогой fallback.

Репозиторий также содержит локальные [private wiki memory](docs/wiki-memory.ru.md)
и [private AI export packs](docs/ai-export-packs.ru.md). Создание pack — это
profile-local CLI-интеграция; нет ни MCP-инструмента для pack, ни автоматического
компилятора wiki, ни автоматического экспорта всего архива.

## MCP

Запустите локальный stdio server командой:

```powershell
tg-recall-mcp
```

MCP намеренно read-only и требует явной настройки `ai_access`. Он может выводить
разрешённые кэшированные чаты и scopes, искать локальные сообщения, возвращать
близкий контекст и предоставлять extractive retrieval со ссылками.

`tg-recall-mcp` — это stdio-процесс: он завершается по EOF на stdin, когда
умирает supervising parent process, через `TG_RECALL_MCP_UNUSED_TIMEOUT_SEC`
секунд (по умолчанию `600`) без `tools/call`, или через
`TG_RECALL_MCP_IDLE_TIMEOUT_SEC` секунд (по умолчанию `1800`) без запроса.
Таймаут `0` отключает соответствующую проверку; `TG_RECALL_MCP_PARENT_WATCHDOG=0`
отключает parent reaping. Hosts могут перезапустить server на следующем вызове.

Для необязательного настоящего local-vector retrieval сначала настройте в
выбранном профиле уже скачанную директорию модели (`semantic.enabled=true`,
`semantic.provider=sentence-transformers-local`, `semantic.model_path=PATH`) и
установите необязательный runtime:

```powershell
uv sync --extra local-embeddings
tg-recall --json index embeddings build --chat-id -1001234567890 --max-batches 1
tg-recall --json index embeddings status --chat-id -1001234567890
tg-recall --json retrieve "deadline" --chat-id -1001234567890 --retrieval-mode auto --token-budget 8000
```

Путь к модели уже должен существовать локально; tg-recall никогда не скачивает
модель. `auto` сообщает о keyword fallback, когда vectors недоступны или
устарели. `semantic` строгий и возвращает `semantic_unavailable`, а не называет
token overlap vectors. `index embeddings rebuild` и `remove` — явные
maintenance-команды только для человека; MCP предоставляет лишь ограниченный
`retrieve_evidence` и никогда не строит, не перестраивает и не удаляет индекс.

### Удалённые OpenRouter embeddings (явный opt-in)

```powershell
$env:OPENROUTER_API_KEY = "..." # keep this outside tg-recall config
tg-recall --json config embeddings choices
tg-recall config embeddings setup --provider openrouter --model perplexity/pplx-embed-v1-0.6b --allow-remote-text
tg-recall --json index embeddings build --chat-id -1001234567890 --max-batches 1
```

`--allow-remote-text` подтверждает, что при индексации в API уходят только выбранные batch сообщений/транскриптов, а при поиске — только запрос. Vectors, checkpoints, FTS ranking и архив остаются локальными; sync не запускает фоновую переиндексацию. Поддерживаются `pplx-embed-v1-0.6b`, `pplx-embed-v1-4b` и `voyage-4-lite`; price units выводит `choices`. При отсутствии ключа, policy или API режим `auto` вернётся к FTS.

## Текущие ограничения

- `ask` по умолчанию использует extractive retrieval со ссылками. Необязательный OpenAI Responses provider требует свой extra, а также явные provider-policy и scope approval; отключённые или недоступные providers возвращают cited local fallback.
- Устаревший `--semantic` использует локальное token overlap. Для необязательного local embedding index используйте `--retrieval-mode auto|hybrid|semantic`.
- Local Whisper запускается через установленный исполняемый файл `whisper`; он не входит в пакет.
- MCP не может синхронизировать, скачивать, транскрибировать, менять конфигурацию или очищать данные.

## Миграция и backup

Скопируйте текущую директорию `.tg-ecosystem`, не меняя её источник:

```powershell
tg-recall migrate legacy --from C:\code\tg-ecosystem\.tg-ecosystem --dry-run
tg-recall migrate legacy --from C:\code\tg-ecosystem\.tg-ecosystem
```

Миграция валидирует SQLite, копирует медиа, преобразует абсолютные пути к медиа
в относительные object keys и никогда не удаляет legacy archive.

```powershell
tg-recall backup create --mode essential --output D:\Backups\tg-recall-essential.zip
tg-recall backup create --mode full --include-session --output D:\Backups\tg-recall-full.zip
tg-recall backup restore D:\Backups\tg-recall-essential.zip --profile restored
```

`essential` включает базу данных, конфигурацию профиля и wiki. `full` также
включает медиа. Сессия и credentials исключаются, если `--include-session` не
указан явно.

## Разработка

```powershell
uv run pytest -q
uv build
```

См. [CONTRIBUTING.ru.md](CONTRIBUTING.ru.md), [SECURITY.ru.md](SECURITY.ru.md),
[ROADMAP.ru.md](ROADMAP.ru.md), [CHANGELOG.ru.md](CHANGELOG.ru.md),
[документацию backup и restore](docs/backup-restore.ru.md),
[обслуживание архива](docs/archive-maintenance.ru.md),
[AI export packs](docs/ai-export-packs.ru.md),
[wiki memory](docs/wiki-memory.ru.md),
[OpenAI Responses provider](docs/openai-responses-provider.ru.md) и
[человеческий перевод setup prompt — не для Fetch](docs/agent-setup/prompt.ru.md).
