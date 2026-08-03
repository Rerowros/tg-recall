# Codex agent optimization

Эта инструкция держит Telegram retrieval локальным, cited и достаточно малым
для повторной работы Codex. Она не меняет конфигурацию Codex и не гарантирует
доступность конкретной модели в аккаунте или runtime.

## Маршрутизация моделей

- Для узкого read-only поиска, extraction, classification и расширения cited
  sources предпочитайте один `gpt-5.3-codex-spark`, только если текущий Codex
  surface его показывает, отдельный лимит доступен и fast preview качества
  достаточно. Spark быстрый и дешёвый, но менее сильный в synthesis.
- Иначе используйте `gpt-5.6-luna` с `low` или `medium` reasoning для той же
  ограниченной работы. Если обе модели недоступны, продолжайте текущей моделью
  с теми же scope и budget.
- Не создавайте subagent для простой прямой проверки. Для независимых scope,
  где параллельность действительно экономит время, используйте минимальное
  число ограниченных агентов. Не повторяйте достаточный Spark/Luna retrieval
  на более сильной модели.
- Эскалируйте к Terra/parent только при конфликтующих источниках, неоднозначном
  multi-source synthesis, security review, риске migration/data-loss или
  повторно недостаточном bounded retrieval.

Доступность моделей зависит от Codex surface, входа, аккаунта и workspace.
Перед тем как делать модель обязательной, проверяйте актуальные
[model guide](https://learn.chatgpt.com/docs/models) и
[subagent guide](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Бюджет контекста и turns

Не отправляйте чат целиком. Начинайте с уже возвращённого evidence и текущего
`tg-recall --json agent guide`: в v0.5.0 доступны profile-local knowledge
catalog и research-session inspect/resume; cited synthesized wiki lookup пока
не доступен. Summary/catalog нужны для навигации, а существенные Telegram
claims всё равно подтверждаются `tg://` источником.

1. Выполните один узкий retrieval.

   ```powershell
   tg-recall --json retrieve --chat-id CHAT_ID --query "QUERY" --limit 8 --context 2 --token-budget 4000
   ```

2. Если доказательств не хватает, расширьте запрос не более одного раза.

   ```powershell
   tg-recall --json retrieve --chat-id CHAT_ID --query "QUERY" --limit 16 --context 5 --token-budget 8000
   ```

3. Для явно разрешённого, но отсутствующего source используйте `tg-recall sync
   ensure` только для сохранённого scope. Не расширяйте scope неявно и не
   запускайте массовое скачивание.
4. `export` применяйте только для явно запрошенного длинного последовательного
   анализа. Materialize/download/transcribe — только для cited media, нужного
   задаче. Остановитесь, когда evidence достаточно; при исчерпании budget
   сообщите, чего не хватает.

`--token-budget` учитывает итоговый canonical serialized payload. Стандартный
fallback намеренно консервативен для смешанного русского/английского текста и
JSON; его counter/version и 15% safety margin выдаются в accounting. Если host
сообщает фактические token usage, они хранятся отдельно от local estimate.
Измеряйте quality, citation coverage, calls/turns, estimated/actual tokens,
latency и cost на успешную задачу, прежде чем менять defaults.

## Copy-ready Codex prompt

```text
For Telegram tasks, use the local tg-recall archive only for chats and date
ranges explicitly requested by the user. Start with `tg-recall agent guide
--json` and follow the capabilities reported by the installed version.

Keep retrieval progressive. Reuse evidence already returned in this task. In
v0.5.0, use the profile-local knowledge catalog or an inspect/resume research
session only as compact navigation, then verify material claims against cited
raw evidence. Cited synthesized wiki lookup is not currently available.

Start with:
tg-recall retrieve --chat-id CHAT_ID --query "QUERY" --limit 8 --context 2
  --token-budget 4000 --json

If material evidence is still missing, widen once only to --limit 16 --context
5 --token-budget 8000. Do not read or export the whole archive by default.
Use export only for explicitly requested long analysis. Download or transcribe
only cited media needed for the task.

For one simple lookup, query directly without a subagent. For a bounded
read-only search, extraction, classification, or source expansion, prefer one
gpt-5.3-codex-spark subagent only when the current runtime exposes it, its
separate limit is available, and preview quality is sufficient. Otherwise use
gpt-5.6-luna at low or medium reasoning. If neither is available, use the
current model and retain the same scope and budgets.

Do not rerun sufficient Luna/Spark retrieval on a stronger model. Escalate
only for conflicting evidence, ambiguous multi-source synthesis,
security-critical review, migration/data-loss risk, or a repeatedly incomplete
bounded result.

Agents may read, run scoped sync, materialize selected media, and transcribe
within user-approved scope. They must not authorize Telegram, change
credentials/config, purge or restore data, migrate archives, send Telegram
messages, run update/integrate lifecycle commands, or widen scope implicitly.
MCP is read-only. Cite conclusions with tg:// links and distinguish raw
evidence from catalog/session summaries.
```
