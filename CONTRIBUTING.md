# Contributing

[English](CONTRIBUTING.md) | [Русский](CONTRIBUTING.ru.md)

## Local Setup

```powershell
uv sync --extra dev
uv run tg-recall --help
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
```

Use synthetic fixtures in `tests/fixtures/`; never add exported chats, real media, credentials, session files, or personal reports.

## Changes

- Keep the CLI and MCP on one code path: `search`, `read`, `stats`, `export`, `chats` and `sync` in both go through `AgentTools`.
- Preserve `tg://chat/<chat_id>/message/<message_id>` citations for archive evidence.
- Add focused tests for changed behavior.
- Update the English and Russian docs together; tests check that commands, code identifiers, links and headings match.
- Run the distribution-content test before requesting review; source archives must contain only public code and documentation.
- Before review, run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest -q`.
- Formatting adoption is incremental: the repository-wide format check reports legacy files until a dedicated mechanical sweep is scheduled. Format every Python file you touch with `uv run ruff format <paths>` and do not hide remaining files with Ruff exclusions.

## Compatibility

[docs/compatibility.md](docs/compatibility.md) lists what 1.x keeps. If `tests/test_public_contract.py` reports a breaking change, keep the old form working instead; if it reports an addition, record it with `TG_RECALL_UPDATE_CONTRACT=1 uv run pytest tests/test_public_contract.py` and mention it in the changelog. A release that changes the schema, the profile configuration or the backup format also adds an upgrade fixture: `uv run python scripts/make_upgrade_fixture.py <tag>`.

## Agent Benchmark

`scripts/agent_bench.py` measures what a change does to real agents: put 10-15 of your own questions into `<profile data dir>/bench/questions.json` (format: `scripts/bench_questions.example.json`), run `uv run python scripts/agent_bench.py` before and after the change, then `--compare` the two result files. It needs a logged-in `claude` CLI and costs real tokens. Never commit questions or results: they name your chats.

## Security

Report sensitive vulnerabilities through the process in [SECURITY.md](SECURITY.md), not public issues.
