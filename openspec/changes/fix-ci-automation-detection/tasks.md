## 1. CI Environment Isolation

- [x] 1.1 Set the pytest workflow step's `CI` variable to a false value without changing runtime automation detection.
- [x] 1.2 Add a regression test proving `CI=true` remains an automation marker outside the workflow override.

## 2. Cross-Platform ACL Test

- [x] 2.1 Make the Windows ACL delegation assertion independent of `Path()` after the Windows-branch mock.

## 3. Verification

- [x] 3.1 Reproduce the historical `CI=true` failure locally, then verify the scoped false-value context passes focused tests.
- [x] 3.2 Run Ruff, the full test suite, `git diff --check`, and strict OpenSpec validation.
