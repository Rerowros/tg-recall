## Why

GitHub Actions sets `CI=true`, which the runtime currently treats as an AI
automation shell. The ordinary CLI tests therefore exercise agent policy
denials instead of their intended interactive behaviour and fail on every
workflow run.

## What Changes

- Run the ordinary test suite with an explicit non-automation CI test context
  while preserving runtime safety defaults and dedicated automation tests.
- Make the Windows ACL unit test platform-neutral so its mock does not mutate
  global path semantics on Linux.
- Add regression coverage for the CI environment boundary and retain the
  Ubuntu/Windows matrix.

## Capabilities

### New Capabilities

- `ci-test-environment-isolation`: Covers deterministic test execution in
  GitHub Actions without changing agent-policy behaviour in production.

### Modified Capabilities

<!-- No archived base specification exists for the CI workflow. -->

## Impact

Changes `.github/workflows/ci.yml`, one cross-platform unit test, and focused
regression tests. No CLI JSON contract, archive data, credentials, profile,
or production automation policy is weakened.
