## ADDED Requirements

### Requirement: Ordinary CI tests use an explicit human test context
The GitHub Actions pytest step SHALL set `CI` to a false value so ordinary CLI
tests execute their intended interactive policy path, while the runtime's
production automation detection remains unchanged.

#### Scenario: GitHub Actions runs the full suite
- **WHEN** the CI workflow executes `uv run pytest -q`
- **THEN** regular CLI tests are not denied solely because GitHub Actions set
  the ambient `CI=true` variable

#### Scenario: Runtime sees an automation marker
- **WHEN** `CI=true` is present outside the pytest step override
- **THEN** runtime automation detection continues to classify the shell as
  automation

### Requirement: ACL delegation test is platform-neutral
The Windows ACL delegation unit test SHALL validate the path forwarded to the
hardening function without constructing a platform-dependent `Path` after it
mocks the Windows branch on POSIX.

#### Scenario: Test runs on Ubuntu
- **WHEN** the ACL test simulates the Windows branch on a POSIX runner
- **THEN** it asserts the same textual target path and does not instantiate a
  `WindowsPath` from a POSIX temporary path
