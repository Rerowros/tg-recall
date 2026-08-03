# OpenAI Responses provider: граница данных

[English canonical](openai-responses-provider.md) | **Русский**

`tg-recall` по умолчанию остаётся local и extractive. Optional adapter
`openai-responses` используется только после того, как существующая synthesis
policy принимает explicit provider, model, credentials, allowed scope, data
classes, evidence-item limit и token budget.

## Что пересекает границу

Передаётся только уже bounded provider request: question и selected evidence
records (`evidence_id`, citation `tg://`, timestamp, chat title и text). У
adapter нет database handle, archive/profile path, Telegram session, media,
credential, function, MCP, web-search, file-search или других tool
capabilities. Он всегда использует Responses API с `store=false`, передаёт
strict JSON schema для `answer` и `evidence_ids` и не передаёт `tools`,
`functions` или `tool_choice`.

Полный final serialized request — question, answer-contract envelope и
selected evidence — должен помещаться в positive local `token_budget` до
вызова adapter. Core использует его canonical UTF-8 JSON bytes как
conservative cap и отклоняет oversized request, не отправляя его и не помещая
prompt text в audit. У request также independent positive cap
`max_output_tokens` (default: 800), поэтому bounded input не может создать
unbounded provider response или output-token cost.

Adapter локально проверяет каждый returned evidence ID по submitted set.
Unknown, missing, malformed или uncited results, unavailable SDK, timeout,
network failure, authentication failure или rate limit возвращают normal cited
local extractive fallback через synthesis core.

## Caveat о retention и eligibility

`store=false` не даёт этому adapter запрашивать stored Responses application
state; сам по себе он **не** включает Zero Data Retention и не устраняет всю
retention. OpenAI документирует, что API data по умолчанию не используется для
training, однако abuse-monitoring logs могут содержать prompts/responses и по
умолчанию хранятся до 30 дней. Zero Data Retention и Modified Abuse Monitoring
требуют OpenAI approval и eligible organization/project configuration. До
разрешения Telegram evidence покинуть машину проверьте current controls и
eligibility endpoint/model организации.

- [OpenAI data controls and Responses retention](https://platform.openai.com/docs/guides/your-data)
- [Structured Outputs for the Responses API](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Responses API reference](https://developers.openai.com/api/docs/guides/responses)

## Credentials и installation

Устанавливайте, только если этот provider намеренно включён:

```powershell
uv sync --extra openai-responses
$env:OPENAI_API_KEY = "..."
```

Optional dependency locked как universal Python wheel, пока resolver constraint
этого project — Python `>=3.13`; при обновлении SDK сохраняйте mock tests
adapter в release gate.

Разрешайте `OPENAI_API_KEY` на host boundary и передавайте его в
`OpenAIResponsesProvider`. Не помещайте key в evidence item, export, archive,
wiki, audit record, prompt, test fixture или Git-tracked config. Adapter не
serializes и не audits key; audits содержат только provider/model, policy
outcome, evidence IDs/count, usage, latency и sanitized failure class.

## Контракт profile configuration

`LLMConfig` по умолчанию использует `provider="extractive"`, `model=null` и
никакой API key, поэтому обновление существующего v0.2 profile не включает
external provider и не изменяет behaviour `ask`. Для обычного
profile-aware пути `save_config(..., home=...)` `provider` и `model`
записываются в `config/profiles/<profile>.json`; `llm.api_key` записывается
только в private файл `config/credentials/<profile>.json`. Config display
использует redaction и никогда не показывает key или common provider-secret
fields.

Старая explicit форма `save_config(..., path=...)` остаётся compatibility
contract для callers, которые намеренно управляют одним monolithic legacy
config file. Она может содержать `llm.api_key`; явно защитите этот chosen file
как credential, не commit его и мигрируйте normal runtime configuration на
profile-aware save path. Automatic migration не выполняется, и никакая provider
configuration не меняет default extractive mode.

External synthesis дополнительно требует, чтобы profile setting
`provider_policy.external_llm_data_classes` было ровно
`["message_text", "metadata"]`. Настройте его явно, например:

```powershell
tg-recall config set provider_policy.external_llm_data_classes message_text,metadata
```

Любой missing, duplicated или extra data class отклоняется до создания provider
adapter. Этот list — allowlist для fixed provider request schema, а не
разрешение загрузить media, sessions, paths, exports или другие archive records.

И для interactive `tg-recall ask`, и для automation provider path сначала
пересекает запрошенный `--chat-id`, optional `--since`/`--until` и optional
`--media-type` с `ai_access.allowed_chat_ids`, date boundaries и media policy.
Пустое пересечение не выполняет archive retrieval и не создаёт provider
adapter. Extractive default остаётся без изменений.
