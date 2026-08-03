# Обновления и подключение AI-harness

`update` и изменяющие `integrate` операции — явные операции человека. Они не
запускаются после обычной команды, после установки пакета или по умолчанию в
фоне. В AI, CI и automation-shell разрешены только data-blind diagnostics
`integrate list`, `integrate preview` и `integrate status`; они не читают
Telegram profile, credentials, session, SQLite, media, wiki или exports и не
пишут harness files. `integrate install`, `refresh`, `uninstall` и все `update`
операции отклоняются до конфигурации, harness mutation, сети или subprocess.
MCP остаётся только для чтения и регистрирует лишь `tg-recall-mcp`.

## Стабильный bootstrap для AI

Скопируйте в Codex, Claude Code, Cursor или другой AI-harness ровно эту
инструкцию:

```text
Fetch https://raw.githubusercontent.com/Rerowros/tg-recall/main/docs/agent-setup/prompt.md and follow it.
```

URL постоянный и не содержит номера Release. Prompt сам получает metadata
последнего stable GitHub Release, выбирает точный versioned universal wheel и
GitHub SHA-256 digest, но никогда не запускает installer и не устанавливает
из `main`. До install/refresh/uninstall агент выдаёт copy-ready HUMAN COMMAND
и останавливается. Он требует явный `user`/`project` scope (и явный project
root для project), сохраняет `manual_action`/partial result и после команды
человека просит перезапустить harness перед `integrate status`.

Если latest Release не предоставляет `integrate list`, это capability gap:
bootstrap останавливается, а не использует checkout или source из `main`.
Поддержка несимметрична: Cursor User Rule и Claude Code user MCP могут
оставаться manual action; generic требует явных destinations. Фактическая
matrix всегда берётся из `integrate list --json` установленной версии.

## Release wheel и обновление

Для обычной установки используйте URL универсального wheel из GitHub Release:

```powershell
uv tool install https://github.com/Rerowros/tg-recall/releases/download/v0.6.0/tg_recall-0.6.0-py3-none-any.whl
```

Перед установкой при необходимости сверяйте опубликованный GitHub SHA-256
asset digest. Это проверка целостности скачанного wheel, а не криптографическая
подпись релиза. `uv tool install --editable .` остаётся режимом разработки:
самообновление не изменяет checkout и вместо этого возвращает
`manual_required` с указанием переустановить исходный editable checkout.

```powershell
tg-recall update check
tg-recall update status
tg-recall update apply
```

`check` получает только последнюю stable GitHub Release по фиксированному
HTTPS endpoint с коротким timeout и ограниченным размером ответа. Результат
может быть `current`, `update_available`, `unreachable` или `unsupported`.
Перед `apply` проверяются происхождение установки, точное имя и версия wheel,
размер и SHA-256. Поддерживается только не-editable `uv tool` установка;
`apply` вызывает `uv tool install --force <проверенный-local-wheel>` без shell
и считает обновление успешным только после успешного installer и проверки
установленной версии. После успеха выполните `tg-recall integrate refresh`.

Периодическая проверка выключена. Включить её может только человек через
`tg-recall update configure --interval-hours N`; отключённый режим не делает
network-запросов и не выводит уведомлений. После explicit opt-in она выполняется
только best-effort после eligible interactive non-JSON CLI command; help, MCP,
automation, lifecycle commands и JSON commands исключены. Неудачная проверка
не меняет exit status исходной команды. Метаданные Release, ETag и
проверенный wheel живут вне profiles в global cache (или portable `--home`),
а не в archive, session, credentials, media, wiki или exports.

## Подключение harness

Сначала посмотрите фактическую capability matrix установленной версии:

```powershell
tg-recall --json integrate list
tg-recall --json integrate preview --target codex --scope project --project-root C:\code\my-project
tg-recall integrate install --target all --scope project --project-root C:\code\my-project
tg-recall --json integrate status --target all --scope project --project-root C:\code\my-project
```

`preview` никогда не пишет файлы. `install` добавляет только управляемый
`tg-recall` fragment; повторный запуск с тем же guide возвращает `unchanged`.
`refresh` изменяет только валидный ранее созданный fragment. `uninstall`
удаляет только валидный owned fragment и не удаляет файл с чужим содержимым.
Для project scope `--project-root` обязателен. Generic target требует явные
MCP и instruction destinations внутри явного output root — пути не
угадываются.

| Harness | Project instructions | User instructions | Project MCP | User MCP |
| --- | --- | --- | --- | --- |
| Codex | managed block в `AGENTS.md` | managed block в `~/.codex/AGENTS.md` | managed documented project config, когда поддержан | managed block в `~/.codex/config.toml` |
| Claude Code | managed block в `CLAUDE.md` | managed `~/.claude/CLAUDE.md` | strict merge в `.mcp.json` | documented Claude CLI, иначе copy-ready manual step |
| Cursor | owned `.cursor/rules/tg-recall.mdc` | manual User Rule step | strict merge в `.cursor/mcp.json` | strict merge в `~/.cursor/mcp.json` |
| Generic | явный instruction destination | явный instruction destination | явный MCP destination | явный MCP destination |

Статусы компонентов — `installed`, `updated`, `unchanged`, `partial`,
`manual_required`, `conflict` или `unsupported`. Неподдерживаемая часть не
считается успешной установкой: результат содержит copy-ready manual action.
JSON-ответ содержит schema version, action, scope, компоненты, changed/backup
paths, conflicts, warnings и next actions, но не credentials, Telegram archive
paths или exception traceback.

## Сохранность существующей конфигурации

Интеграция меняет только `mcpServers["tg-recall"]` и явно отмеченный
instruction block. Она отказывается писать при malformed JSON/TOML, duplicate
или unclosed markers, изменённом owned block, неуправляемом конфликтующем
`tg-recall` entry, symlink/junction/reparse point или выходе за выбранный root.
Чужие MCP server entries и текст остаются без изменений.

Перед настоящим изменением уже существующего harness файла создаётся private
timestamped sibling backup вида
`.имя-файла.tg-recall-backup-<timestamp>-<hash>.bak`, затем private temporary
file атомарно заменяет target. Backup не создаётся для `preview` и no-op. Он
содержит прежний harness file (включая возможные чужие настройки), поэтому
храните его приватно; он не копируется в Telegram profile/archive и не
включается в JSON body. Для отката восстановите именно явно показанный backup
после проверки его содержимого.
