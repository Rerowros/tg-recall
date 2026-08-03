## Context

The current `WhisperCLIProvider` discovers only `whisper` on `PATH` and invokes
the OpenAI-compatible JSON flags. The user's installed backend is a standalone
`faster-whisper-xxl.exe` outside `PATH`, with already-local models and a CUDA
GPU. Faster-Whisper-XXL exposes compatible JSON/output flags but has additional
model-directory, quantization, and VAD controls that matter for short voice
messages. Configuration is profile-local JSON and automation cannot change it.

## Goals / Non-Goals

**Goals:**

- Select a supported local CLI backend and explicit executable without wrapper
  scripts or shell invocation.
- Configure model, local model directory, language, device, compute type, VAD,
  and timeout through existing human-only `config set` behavior.
- Preserve the legacy `whisper` discovery and arguments when settings are
  absent.
- Require Faster-Whisper-XXL model files to exist locally before execution and
  return safe diagnostics when configuration is incomplete.
- Store the resulting text/language/segments through the unchanged transcript
  and FTS contracts.

**Non-Goals:**

- Download, update, benchmark, or execute model repository code.
- Add cloud speech providers, background services, automatic whole-archive
  transcription, speaker identity resolution, or photo OCR.
- Expose transcription maintenance through MCP or let AI change settings.
- Add arbitrary extra CLI arguments or shell command templates.

## Decisions

### Typed profile configuration

Add `TranscriptionConfig` to `AppConfig` with:

- `backend`: `whisper-cli` (default) or `faster-whisper-xxl`;
- `executable`: optional command/path, defaulting to backend discovery;
- `model`, `model_dir`, `language`, and `device`: optional strings;
- `compute_type`: optional validated Faster-Whisper quantization value;
- `vad_filter`: optional boolean so the backend default remains compatible;
- `timeout_seconds`: positive bounded integer.

The section is saved in profile configuration, not credentials. Missing legacy
configuration deep-merges defaults. Arbitrary `extra_args` are rejected to keep
the subprocess surface reviewable.

### Separate providers behind the existing protocol

Keep `WhisperCLIProvider` compatible and add `FasterWhisperXXLProvider`. A small
factory resolves the selected executable and constructs an argv list. It uses
`subprocess.run(..., shell=False)`, an explicit timeout, isolated output
discovery, and strict JSON type validation. No config value becomes executable
syntax.

### Local model gate for Faster-Whisper-XXL

The Faster adapter requires `model` and resolves `model_dir` either explicitly
or as `_models` beside an absolute executable. It accepts only an existing
`faster-whisper-<model>` directory. Failure occurs before subprocess execution,
preventing the standalone tool from implicitly acquiring a model. The legacy
OpenAI-compatible provider retains its historic behavior for compatibility.

### Additive diagnostics

Keep `doctor.providers.whisper` for JSON compatibility and add a
`local_transcription` object containing backend, executable name, resolved
status, configured model name, local-model status, and option validity. Full
host paths and transcript content are omitted from diagnostics.

### Policy behavior remains explicit

`--provider local` uses the selected local provider. `auto` remains
Telegram-first and then processes only still-pending/retry jobs locally.
`sidecar` remains the CLI default. AI may transcribe only an explicit permitted
`tg://` citation and still cannot change configuration or run unscoped jobs.

## Risks / Trade-offs

- **[Different JSON dialect]** → Normalize only a strict object containing
  string `text`, optional string `language`, and list `segments`; fail closed on
  incompatible output.
- **[Short voice lost by VAD]** → Expose explicit `vad_filter`; document `false`
  as the short-voice preset and `true` for longer noisy audio.
- **[GPU OOM or hung process]** → Expose compute type and bounded timeout; mark
  the job retryable without corrupting prior transcripts.
- **[Host paths leak]** → Keep config profile-private and return only basenames
  and booleans in doctor/JSON diagnostics.
- **[Partial config while using `config set`]** → Save typed fields
  independently; provider construction and doctor report missing/invalid local
  prerequisites deterministically.

## Migration Plan

Ship additive config defaults and providers without a database migration.
Existing profiles continue resolving `whisper`. Users explicitly configure the
Faster backend and already-local paths, verify through `doctor`, then run one
citation-scoped transcription. Rollback ignores/removes the additive config
section and restores the legacy provider factory.

## Open Questions

None.
