# Codex agent optimization

This guide keeps Telegram retrieval local, cited, and small enough for repeated Codex work. It does not change Codex configuration or guarantee that a model is available to a particular account.

## Model routing

- Prefer `gpt-5.3-codex-spark` for near-instant narrow read-only retrieval when the current Codex surface exposes it and its separate usage limit is available. Spark is a fast, less-capable research-preview model for Pro subscribers and does not consume the ordinary Codex allowance in the same way.
- Use `gpt-5.6-luna` with low or medium reasoning as the stable fallback for narrow search, extraction, classification, and source expansion. Current Codex guidance positions Luna as the lowest-cost GPT-5.6 tier for high-volume work.
- Keep one direct lookup in the parent when a subagent would add more work than it removes. Use one bounded search subagent by default; use several only for genuinely independent scopes.
- Escalate to the parent, Terra, or a stronger reviewer only for conflicting evidence, ambiguous multi-source synthesis, security-sensitive review, migration/data-loss risk, or a measured failure of the narrow route.
- If the preferred model or subagent surface is unavailable, continue with the current model under the same scope and context budget.

Model availability depends on the Codex surface, sign-in method, account, and workspace. Re-check the current [Codex model guide](https://learn.chatgpt.com/docs/models) and [subagent guide](https://learn.chatgpt.com/docs/agent-configuration/subagents) before making a model a hard requirement.

## Context and turn budget

Use progressive retrieval instead of sending a full chat to the model:

1. Reuse evidence already returned in the current task; do not repeat the same search on a stronger model.
2. When implemented and reported by `agent guide`, inspect a saved research session or compact wiki/catalog result first.
3. Start raw retrieval with a narrow query:

   ```powershell
   tg-recall retrieve --chat-id CHAT_ID --query "QUERY" --limit 8 --context 2 --token-budget 4000 --json
   ```

4. If material evidence is missing, widen once:

   ```powershell
   tg-recall retrieve --chat-id CHAT_ID --query "QUERY" --limit 20 --context 5 --token-budget 8000 --json
   ```

5. Use `export` only for explicitly requested long sequential analysis. Materialize or transcribe only cited media needed for the task.
6. Stop when the evidence is sufficient. If the budget is exhausted, report what is missing instead of silently reading the full archive.

These are initial defaults, not universal limits. Measure answer quality, citation coverage, calls/turns, estimated or actual tokens, latency, and cost per successful task before changing them.

`--token-budget` currently uses a rough `4 characters ~= 1 token` heuristic. The OpenSpec change replaces it with accounting over the final serialized payload, a verified tokenizer when available, a calibrated conservative multilingual fallback, and a 15% initial safety margin. It will report the counter/version and keep host-reported actual Codex tokens separate from estimates.

## Copy-ready Codex prompt

```text
For Telegram tasks, use the local `tg-recall` archive only for chats and date
ranges explicitly requested by the user. Start with `tg-recall agent guide
--json` and follow the capabilities reported by the installed version.

Current v0.2-compatible operations are the capabilities printed by that guide:
scoped search/retrieve/export/sync, selected cited media download and
transcription, and the read-only MCP query surface. Do not treat the planned
knowledge catalog, synthesized wiki, saved evidence sets, or research-session
checkpoints as installed CLI or MCP commands.

Keep retrieval progressive. Reuse evidence already returned in this task. When
a future installed guide reports saved research sessions or wiki/catalog lookup
as available, check their compact current results first and expand original
sources only when the claim needs verification. In v0.2, start with:

tg-recall retrieve --chat-id CHAT_ID --query "QUERY" --limit 8 --context 2
  --token-budget 4000 --json

If material evidence is still missing, widen once to --limit 20 --context 5
--token-budget 8000. Do not read or export the whole archive by default. Use
export only for an explicitly requested long analysis. Download or transcribe
only cited media needed for the task.

For one simple lookup, query directly without spawning a subagent. For bounded
read-only search, extraction, classification, or source expansion that benefits
from delegation, prefer one `gpt-5.3-codex-spark` subagent when the current
Codex runtime exposes it, its separate usage limit is available, and the narrow
task tolerates a fast research-preview model. Otherwise use `gpt-5.6-luna` at
low or medium reasoning. If neither model is available, use the current model
and keep the same scope and budgets.

Do not rerun sufficient Luna/Spark retrieval on a stronger model. Escalate only
for conflicting evidence, ambiguous multi-source synthesis, security-critical
review, migration/data-loss risk, or an explicitly incomplete low-cost result.

Agents may read, run scoped sync, materialize selected media, and transcribe
within user-approved scope. They must not authorize Telegram, change
credentials/config, purge or restore data, migrate archives, send Telegram
messages, or widen scope implicitly. MCP is read-only. Cite conclusions with
`tg://` links and distinguish raw evidence from wiki/session summaries.
```

## Planned knowledge and session boundary (not v0.2 CLI/MCP)

The planned knowledge interface is layered rather than a second opaque archive:

```text
raw Telegram messages and transcripts (source of truth)
  -> cited, versioned wiki knowledge
  -> task-specific saved evidence sets and research checkpoints
```

Codex local memories can help the agent remember workflows across chats, but they are advisory and generated separately. Telegram-specific facts must be verified through `tg-recall` citations. The project does not write into `~/.codex/memories`, store hidden model reasoning, or copy full Codex transcripts into the archive.

Official background: [Codex memories](https://learn.chatgpt.com/docs/customization/memories), [Codex pricing and usage optimization](https://learn.chatgpt.com/docs/pricing).
