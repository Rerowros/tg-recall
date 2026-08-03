# Local transcription

**English canonical** | [Русский](local-transcription.ru.md)

`tg-recall` keeps media and transcripts in the selected local profile. This
guide describes how a human chooses the transcription provider and configures
an already-installed local backend. It does not download a model or a package.

## Provider behavior

`transcribe run` defaults to `sidecar`. It reads a human-provided text sidecar:
for media `voice.ogg`, the sidecar is `voice.ogg.txt`. This is useful when a
transcript was prepared elsewhere and no speech engine should run.

`telegram` asks Telegram to transcribe eligible media. `local` uses the
profile's selected local adapter. `auto` is Telegram-first: it asks Telegram
first and passes only still-pending or retryable jobs to the configured local
adapter. It does not run the local adapter for a job that Telegram completed.

Local transcription runs the chosen executable on this machine. In contrast,
the `telegram` provider involves Telegram. Choose `local` when the media must
remain on the machine after it has been downloaded into the profile.

## Human-only configuration

Only a human may change this profile-local configuration with `config set`.
Automation, MCP, and an AI agent cannot alter it. The supported fields are:

| Field | Purpose |
| --- | --- |
| `transcription.backend` | `whisper-cli` (the compatibility default) or `faster-whisper-xxl`. |
| `transcription.executable` | Optional explicit executable; otherwise the selected backend is discovered. |
| `transcription.model` | Local model name; required by `faster-whisper-xxl`. |
| `transcription.model_dir` | Optional directory containing the local Faster-Whisper-XXL models. |
| `transcription.language` | Optional language hint, for example `ru`. |
| `transcription.device` | Optional local device: `auto`, `cpu`, `cuda`, or `cuda:N`, where N is the CUDA device index. |
| `transcription.compute_type` | Optional validated Faster-Whisper compute type: `default`, `auto`, `int8`, `int8_float16`, `int8_float32`, `int8_bfloat16`, `int16`, `float16`, `float32`, or `bfloat16`. |
| `transcription.vad_filter` | Optional voice-activity detection switch; omit it to keep the backend default. |
| `transcription.timeout_seconds` | Positive bounded local-process timeout. |

There is no `extra_args` setting, command template, or shell string. Each
supported value is validated and passed as a separate argument. `tg-recall`
does not fetch models: Faster-Whisper-XXL requires the exact existing
`faster-whisper-<model>` directory below `model_dir` (or `_models` beside an
absolute executable) before it starts.

With no `transcription` section, the compatibility behavior remains
`whisper-cli`: `whisper` is discovered on `PATH` and its existing JSON command
contract is preserved.

## Faster-Whisper-XXL presets

The following is an example for an RTX 4050 Laptop with 6 GB VRAM, not a
hardware requirement. It uses an already-local `large-v3-turbo` model and
`int8_float16`. If it exhausts VRAM or is unsuitable for the machine, use the
already-local `medium` model instead.

### Short Telegram voice messages

Set VAD to `false`: very short replies can otherwise be removed as silence.

```powershell
tg-recall config set transcription.backend faster-whisper-xxl
tg-recall config set transcription.executable "C:\Tools\faster-whisper-xxl\faster-whisper-xxl.exe"
tg-recall config set transcription.model large-v3-turbo
tg-recall config set transcription.model_dir "C:\Tools\faster-whisper-xxl\_models"
tg-recall config set transcription.language ru
tg-recall config set transcription.device cuda
tg-recall config set transcription.compute_type int8_float16
tg-recall config set transcription.vad_filter false
tg-recall config set transcription.timeout_seconds 180
```

### Longer or noisy audio

Keep the rest of the selected backend configuration and set VAD to `true` to
skip long stretches without speech:

```powershell
tg-recall config set transcription.vad_filter true
```

To fall back to the lighter local model, change only the model name after
confirming the matching directory already exists:

```powershell
tg-recall config set transcription.model medium
```

For the legacy OpenAI-compatible CLI, select `whisper-cli`; no
Faster-Whisper-XXL model download is implied:

```powershell
tg-recall config set transcription.backend whisper-cli
```

## Verify one cited item

First run diagnostics. `doctor` reports sanitized readiness: backend, an
executable basename and resolution status, configured model, local-model
availability, and option validity. It does not reveal the full host path,
transcript text, credentials, or session material.

```powershell
tg-recall --json doctor
```

Then transcribe exactly one archived media message by its `tg://` citation.
The citation must already refer to archived media; download it through an
explicit scope/workflow first when necessary.

```powershell
tg-recall --json transcribe run --provider local --citation tg://chat/-1001234567890/message/42 --limit 1
```

The transcript is stored locally and becomes available to keyword search with
its source citation. A failed local process leaves the job retryable; correct
the human configuration or local installation, re-check `doctor`, then retry
the same cited item.

An AI agent may request transcription only for one explicit, permitted `tg://`
citation. It cannot edit transcription settings, use an unscoped pending-job
run, download a model, or invoke arbitrary arguments. The stable agent Fetch
instruction remains unchanged; it is not a way to configure transcription.
