## ADDED Requirements

### Requirement: Transcribable media detection
The system SHALL identify voice messages, audio files, and video files that are eligible for transcription.

#### Scenario: Voice message detected
- **WHEN** synced media is a Telegram voice message
- **THEN** the system marks it as transcribable and creates a transcription job

#### Scenario: Unsupported media detected
- **WHEN** synced media is a photo or unsupported document
- **THEN** the system does not create an audio transcription job for that media

### Requirement: Telegram transcription provider
The system SHALL attempt Telegram transcription for eligible Telegram media when the account and message type support it.

#### Scenario: Telegram transcription succeeds
- **WHEN** Telegram returns a transcript for a voice or audio message
- **THEN** the system stores the transcript with provider provenance set to Telegram

#### Scenario: Telegram transcription unavailable
- **WHEN** Telegram transcription is unavailable, rejected, or unsupported for the media
- **THEN** the system records the provider result and allows fallback transcription to run if configured

### Requirement: Fallback speech-to-text provider
The system SHALL support a fallback speech-to-text provider for audio and video content that cannot be transcribed through Telegram.

#### Scenario: Fallback transcription succeeds
- **WHEN** a configured fallback provider returns transcript text
- **THEN** the system stores the transcript with provider provenance and links it to the source media

#### Scenario: No fallback configured
- **WHEN** Telegram transcription fails and no fallback provider is configured
- **THEN** the system marks the transcription job as skipped with a clear reason

### Requirement: Video audio extraction
The system SHALL extract an audio stream from video files before sending video content to a fallback speech-to-text provider.

#### Scenario: Video contains audio
- **WHEN** a downloaded video contains an audio stream and fallback transcription is enabled
- **THEN** the system extracts audio and submits the extracted audio to the configured provider

#### Scenario: Video has no audio
- **WHEN** a downloaded video has no audio stream
- **THEN** the system marks transcription as not applicable for that video

### Requirement: Transcript caching
The system SHALL cache transcript results and avoid retranscribing unchanged media.

#### Scenario: Existing transcript
- **WHEN** a transcription job is requested for media with an existing successful transcript
- **THEN** the system reuses the stored transcript unless the user explicitly requests reprocessing

### Requirement: Transcript failure state
The system SHALL persist transcription failures with provider, error type, retryability, and timestamp.

#### Scenario: Provider error
- **WHEN** a transcription provider returns an error
- **THEN** the system records the failure and makes the job available for retry only when the error is retryable
