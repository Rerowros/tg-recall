# Contributing

## Local Setup

```powershell
uv sync --extra dev
uv run tg-recall --help
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

## Security

Report sensitive vulnerabilities through the process in [SECURITY.md](SECURITY.md), not public issues.
