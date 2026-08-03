## Why

Local transcription currently assumes an executable named `whisper` and does
not let a user select the installed backend, model, language, device,
quantization, or VAD behavior. This prevents `tg-recall` from safely using an
already-local Faster-Whisper-XXL installation and makes short Telegram voice
messages depend on unsuitable backend defaults.

## What Changes

- Add profile-local, typed transcription settings for backend, executable,
  model directory/name, language, device, compute type, VAD, and timeout.
- Preserve the current OpenAI-compatible `whisper` CLI behavior as the default
  when no transcription settings are present.
- Add a native Faster-Whisper-XXL CLI adapter that invokes an explicit argv
  without a shell, requires an already-local executable/model, and normalizes
  JSON output into the existing transcript contract.
- Make `local` and the local fallback of `auto` use the configured adapter while
  keeping Telegram-first behavior and existing CLI/JSON commands compatible.
- Extend `doctor` with sanitized local-transcription diagnostics and document
  recommended short-voice and long-audio settings in English and Russian.
- Add configuration, command-construction, output-contract, fallback,
  diagnostics, privacy, and distribution tests without reading real media.

## Capabilities

### New Capabilities

- `configurable-local-transcription`: Covers safe typed local transcription
  configuration, backend selection, local model enforcement, execution/output
  contracts, diagnostics, and compatible Telegram/local policy behavior.

### Modified Capabilities

<!-- No archived base spec changes. Existing CLI and JSON commands remain
     compatible; the new behavior is an additive local-provider capability. -->

## Impact

The change affects profile configuration, transcription providers, CLI provider
construction, doctor output, tests, and public EN/RU documentation. It adds no
cloud provider, dependency, model downloader, automatic archive-wide media
processing, Telegram write operation, MCP mutation, credential field, or
release artifact containing local executable/model paths.
