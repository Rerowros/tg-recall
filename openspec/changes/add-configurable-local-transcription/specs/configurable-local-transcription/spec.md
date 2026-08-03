## ADDED Requirements

### Requirement: Typed profile-local transcription settings
The system SHALL support profile-local settings for local backend, executable,
model, model directory, language, device, compute type, VAD, and a positive
bounded timeout, while missing legacy settings SHALL retain the current
`whisper` CLI behavior.

#### Scenario: Existing profile is loaded after upgrade
- **WHEN** profile JSON has no transcription section
- **THEN** the application selects `whisper-cli`, discovers `whisper`, and
  preserves the existing local transcription command contract

#### Scenario: Human configures Faster-Whisper-XXL
- **WHEN** a human sets supported transcription keys through `config set`
- **THEN** values round-trip in private profile configuration without entering
  credentials or permitting an automation config write

### Requirement: Safe local executable invocation
The system MUST resolve the configured executable without a shell, validate
typed options, pass each value as a separate argv element, apply the configured
timeout, and SHALL NOT accept arbitrary extra arguments or command templates.

#### Scenario: Executable path contains spaces
- **WHEN** Faster-Whisper-XXL is configured by an absolute Windows path
- **THEN** the exact path is executed as one argv element and no config value is
  interpreted by a shell

#### Scenario: Unsupported option is configured
- **WHEN** backend, compute type, device, model name, or timeout is invalid
- **THEN** provider construction fails before a transcription subprocess starts

### Requirement: Faster-Whisper-XXL uses only an existing local model
The Faster-Whisper-XXL adapter SHALL require a safe model name and an existing
`faster-whisper-<model>` directory below the selected local model directory
before executing the backend.

#### Scenario: Configured model is absent
- **WHEN** the model directory does not contain the exact configured model
- **THEN** transcription fails before subprocess execution and does not trigger
  a model or package download

### Requirement: Strict transcript output contract
Local adapters SHALL request JSON and accept only a JSON object with non-empty
string `text`, optional string `language`, and list `segments`, then SHALL store
the result through the existing transcript and FTS insertion path.

#### Scenario: Faster backend returns compatible JSON
- **WHEN** the backend writes a valid transcript JSON file
- **THEN** provider, text, language, and segments are returned through
  `TranscriptResult` and the transcript becomes keyword-searchable

#### Scenario: Backend returns malformed JSON
- **WHEN** output is missing, invalid, empty, or has incompatible field types
- **THEN** no transcript is inserted and the existing transcription job becomes
  retryable with a bounded error

### Requirement: Existing provider policy remains compatible
`transcribe run --provider local` SHALL use the configured local adapter,
`auto` SHALL remain Telegram-first with local fallback for unfinished jobs, and
`sidecar` SHALL remain the command default.

#### Scenario: Telegram completes a job in auto mode
- **WHEN** Telegram transcription succeeds
- **THEN** the local adapter does not process that completed job

#### Scenario: Agent transcribes media
- **WHEN** an AI shell requests transcription
- **THEN** it still requires one permitted explicit `tg://` citation and cannot
  change transcription configuration or process unscoped pending jobs

### Requirement: Sanitized transcription diagnostics
`doctor` SHALL preserve the existing provider fields and add stable diagnostics
for selected backend, executable resolution, model configuration/local
availability, and option validity without transcript text, credentials, session
material, or full host paths.

#### Scenario: Faster backend is configured correctly
- **WHEN** doctor inspects an existing executable and local model directory
- **THEN** diagnostics report the backend and readiness using basenames and
  booleans only

### Requirement: Local transcription guidance
English and Russian documentation SHALL explain provider selection, current
`sidecar` default, Telegram-first `auto`, local-only privacy, short-voice versus
long-audio VAD presets, model/VRAM fallback, and the prohibition on implicit
model downloads.

#### Scenario: User follows the short-voice preset
- **WHEN** the user configures an already-local Faster-Whisper-XXL model for
  short Telegram voice messages
- **THEN** documentation provides copy-ready human commands with VAD disabled
  and a bounded citation-scoped verification workflow
