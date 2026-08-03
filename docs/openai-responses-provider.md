# OpenAI Responses provider: data boundary

`tg-recall` remains local and extractive by default. The optional `openai-responses` adapter is used only after the existing synthesis policy accepts an explicit provider, model, credentials, allowed scope, data classes, evidence-item limit, and token budget.

## What crosses the boundary

Only the already bounded provider request is sent: the question and selected evidence records (`evidence_id`, `tg://` citation, timestamp, chat title, and text). The adapter has no database handle, archive/profile path, Telegram session, media, credential, function, MCP, web-search, file-search, or other tool capability. It always uses the Responses API with `store=false`, supplies a strict JSON schema for `answer` and `evidence_ids`, and does not pass `tools`, `functions`, or `tool_choice`.

The full final serialized request — question, answer-contract envelope, and
selected evidence — must fit the positive local `token_budget` before an
adapter is called. The core uses its canonical UTF-8 JSON bytes as a
conservative cap and denies an oversized request without sending it or placing
prompt text in the audit. The request also has an independent, positive
`max_output_tokens` cap (default: 800), so a bounded input cannot produce an
unbounded provider response or output-token cost.

The adapter validates every returned evidence ID locally against the submitted set. Unknown, missing, malformed, or uncited results, an unavailable SDK, timeout, network failure, authentication failure, or rate limit all return the normal cited local extractive fallback through the synthesis core.

## Retention and eligibility caveat

`store=false` prevents this adapter from requesting stored Responses application state; it does **not** itself enable Zero Data Retention or remove all retention. OpenAI documents that API data is not used for training by default, while abuse-monitoring logs can contain prompts/responses and are retained up to 30 days by default. Zero Data Retention and Modified Abuse Monitoring require OpenAI approval and eligible organization/project configuration. Verify the organization's current controls and endpoint/model eligibility before allowing Telegram evidence outside the machine.

- [OpenAI data controls and Responses retention](https://platform.openai.com/docs/guides/your-data)
- [Structured Outputs for the Responses API](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Responses API reference](https://developers.openai.com/api/docs/guides/responses)

## Credentials and installation

Install only when this provider is intentionally enabled:

```powershell
uv sync --extra openai-responses
$env:OPENAI_API_KEY = "..."
```

The optional dependency is locked as a universal Python wheel while this
project's resolver constraint is Python `>=3.13`; keep the adapter's mock
tests in the release gate whenever the SDK is upgraded.

Resolve `OPENAI_API_KEY` at the host boundary and pass it to `OpenAIResponsesProvider`. Do not put the key in an evidence item, export, archive, wiki, audit record, prompt, test fixture, or Git-tracked config. The adapter does not serialize or audit the key; audits contain only provider/model, policy outcome, evidence IDs/count, usage, latency, and sanitized failure class.

## Profile configuration contract

`LLMConfig` defaults to `provider="extractive"`, `model=null`, and no API key,
so upgrading an existing v0.2 profile does not enable an external provider or
change `ask` behavior. For the ordinary profile-aware `save_config(...,
home=...)` path, `provider` and `model` are written to
`config/profiles/<profile>.json`; `llm.api_key` is written only to the private
`config/credentials/<profile>.json` file. Config display uses redaction and
never prints the key or common provider-secret fields.

The older explicit `save_config(..., path=...)` form remains a compatibility
contract for callers that deliberately manage one monolithic legacy config
file. It can contain `llm.api_key`; protect that explicitly chosen file as a
credential, do not commit it, and migrate normal runtime configuration to the
profile-aware save path. No automatic migration is performed and no provider
configuration changes the default extractive mode.

External synthesis additionally requires the profile setting
`provider_policy.external_llm_data_classes` to be exactly
`["message_text", "metadata"]`. Configure it explicitly, for example:

```powershell
tg-recall config set provider_policy.external_llm_data_classes message_text,metadata
```

Any missing, duplicated, or extra data class is rejected before a provider
adapter is constructed. This list is an allowlist for the fixed provider
request schema, not permission to upload media, sessions, paths, exports, or
other archive records.

For both interactive `tg-recall ask` and automation, the provider path first
intersects the requested `--chat-id`, optional `--since`/`--until`, and
optional `--media-type` with `ai_access.allowed_chat_ids`, date boundaries, and
media policy. An empty intersection performs no archive retrieval and no
provider construction. The extractive default remains unchanged.
