## ADDED Requirements

### Requirement: Russian project entry point
The project SHALL provide a complete `README.ru.md` with reciprocal language
navigation from `README.md`, and both entry points SHALL expose the same stable
agent-setup URL and current privacy boundaries.

#### Scenario: Russian reader opens the repository
- **WHEN** a reader follows the Russian link at the top of `README.md`
- **THEN** `README.ru.md` provides installation, storage, Telegram, archive,
  agent, MCP, migration, backup, development, and supporting-document guidance
  without changing commands or URLs

### Requirement: Supported Russian public-document set
The repository SHALL maintain Russian counterparts for SECURITY, CONTRIBUTING,
ROADMAP, CHANGELOG, backup/restore, archive maintenance, AI export packs, wiki
memory, OpenAI provider policy, and the human-readable agent setup reference.

#### Scenario: Documentation locale contract is tested
- **WHEN** repository tests enumerate the required Russian documentation set
- **THEN** every required file exists and every repository-local Markdown link
  resolves to a tracked public path

### Requirement: Technical contract fidelity
Russian documents MUST preserve CLI commands, option and JSON names, model
identifiers, `tg://` citations, URLs, path examples, and local-first privacy
semantics from their canonical source documents.

#### Scenario: Reader follows a translated command
- **WHEN** a Russian document presents a command or machine identifier
- **THEN** the executable text remains in its original form and only the human
  explanation is translated

### Requirement: Single authoritative agent setup prompt
`docs/agent-setup/prompt.md` SHALL remain the only documented machine-executed
stable Fetch contract, while `prompt.ru.md` SHALL be labelled as a
non-authoritative human translation that points back to the canonical file.

#### Scenario: Agent setup links are inspected
- **WHEN** locale tests inspect README and setup documentation
- **THEN** the versionless Fetch instruction targets only canonical
  `docs/agent-setup/prompt.md` and no instruction tells an agent to follow the
  Russian translation

### Requirement: Russian documentation is public but data-blind
Russian documentation SHALL be included in the source distribution and SHALL
NOT add private Telegram data, generated harness configuration, integration
state, backups, package caches, profiles, sessions, SQLite, media, wiki, or
exports to wheel, sdist, or release artifacts.

#### Scenario: Release distributions are built
- **WHEN** distribution privacy tests inspect wheel and sdist members
- **THEN** required RU Markdown files are present only where public docs are
  expected and all existing private-path exclusions remain enforced
