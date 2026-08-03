# Contributing

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

- Keep CLI and MCP behavior on the same storage and retrieval primitives.
- Preserve `tg://chat/<chat_id>/message/<message_id>` citations for archive evidence.
- Add focused tests for changed behavior.
- Update README and public limitations when a feature is incomplete, gated, or provider-dependent.
- Run the distribution-content test before requesting review; source archives must contain only public code and documentation.
- Before review, run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest -q`.
- Formatting adoption is incremental: the repository-wide format check reports legacy files until a dedicated mechanical sweep is scheduled. Format every Python file you touch with `uv run ruff format <paths>` and do not hide remaining files with Ruff exclusions.

## Security

Report sensitive vulnerabilities through the process in [SECURITY.md](SECURITY.md), not public issues.
