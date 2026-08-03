## Context

The CI workflow runs the whole suite on Ubuntu and Windows. GitHub Actions
exports `CI=true`; this is intentionally treated as automation by the runtime
and is correct for a real CLI invocation, but most tests model a human CLI
session and do not clear that environment variable. A separate ACL mock assigns
`os.name = "nt"` on the shared `os` module, which changes `pathlib.Path`
selection on POSIX.

## Goals / Non-Goals

**Goals:**

- Keep production detection of `CI=true` as automation unchanged.
- Give the ordinary pytest invocation an explicit human test context.
- Preserve dedicated automation tests that opt in with explicit AI variables.
- Make the ACL unit test assert the delegated path without changing global path
  class behaviour.

**Non-Goals:**

- Changing CLI, MCP, profile, credential, archive, or agent-policy contracts.
- Removing CI from runtime automation detection.
- Adding a new CI platform or releasing an artifact.

## Decisions

### Scope the override to the pytest workflow step

Set `CI: "false"` only for `uv run pytest -q` in GitHub Actions. This makes
tests that represent ordinary interactive commands deterministic while leaving
runtime policy and other workflow steps untouched. Dedicated policy tests set
their own `TG_RECALL_AI_MODE`/related variables and remain meaningful.

Changing `is_automation_shell` was rejected because CI can be a real
non-interactive runtime context, where retaining the privacy guard is safer.

### Avoid mutating global `os.name` in the POSIX test assertion

Keep the existing Windows-branch mock but record the string path passed to the
ACL hardener. This validates delegation without calling `Path()` after the
global `os.name` monkeypatch selects `WindowsPath` on Linux.

## Risks / Trade-offs

- [A new human-mode test forgets to opt into automation] → policy-specific
  tests explicitly set automation variables; add a regression test that
  `CI=true` still selects automation outside the workflow override.
- [Workflow environment diverges from an interactive terminal] → the override
  applies only to pytest's synthetic temporary profiles and does not alter
  actual CLI execution or release build steps.
