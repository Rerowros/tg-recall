## 1. Configuration Contract

- [x] 1.1 Add backward-compatible typed `TranscriptionConfig` defaults, profile persistence, coercion, and validation helpers.
- [x] 1.2 Add config round-trip and human/automation boundary tests for every supported transcription setting.

## 2. Local Provider Adapters

- [x] 2.1 Preserve the default `WhisperCLIProvider` behavior while adding explicit executable, common options, and bounded timeout support.
- [x] 2.2 Add a shell-free Faster-Whisper-XXL provider with strict typed argv and already-local executable/model validation.
- [x] 2.3 Add strict JSON normalization plus tests for success, missing/empty/malformed output, timeout, and paths containing spaces.

## 3. CLI And Diagnostics

- [x] 3.1 Route `local` and the local part of `auto` through a config-driven provider factory without changing sidecar or Telegram-first behavior.
- [x] 3.2 Extend doctor with compatible sanitized local-transcription diagnostics and test that host paths/private data are absent.
- [x] 3.3 Prove agent transcription still requires an explicit permitted citation and all config/provider maintenance remains human-only.

## 4. Documentation

- [x] 4.1 Add complete English and Russian local-transcription guidance with provider semantics, typed settings, safe presets, and citation-scoped verification.
- [x] 4.2 Link the guide from both READMEs and preserve localized-link, command-block, and distribution privacy contracts.

## 5. Verification

- [x] 5.1 Run focused config/transcription/queue/agent/doctor/documentation/distribution tests.
- [x] 5.2 Run Ruff/format, full pytest, package build, CLI help/config smoke, `git diff --check`, and strict OpenSpec validation.
- [x] 5.3 Review the full diff for executable injection, implicit downloads, privacy leakage, retry behavior, and CLI/JSON compatibility.
