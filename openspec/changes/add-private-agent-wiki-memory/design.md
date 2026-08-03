## Context

The archive already stores normalized messages, transcripts, citations, and sync timestamps. Agents need durable context about recurring people and work, but raw-chat retrieval is expensive and prone to repeating the same discovery work.

## Goals / Non-Goals

**Goals:** maintain local Markdown pages from archive deltas; make compact evidence-backed lookup the first agent path; preserve provenance and revision history; keep all derived content private.

**Non-Goals:** diagnose people, replace the raw archive, publish a personal wiki, or make an external LLM/Wiki dependency mandatory.

## Design

Store immutable per-chat source snapshots under `.tg-ecosystem/wiki/raw/` and synthesized pages under `.tg-ecosystem/wiki/pages/`. Pages use YAML frontmatter with `kind`, stable subject identifiers, `snapshot_id`, `updated_at`, and source citations. Page bodies separate observed facts from hypotheses and assign a confidence value to each assertion.

SQLite records snapshot ranges, page revisions, and source citation mappings. A compiler selects only messages newer than the last successful snapshot, writes a new immutable snapshot, and creates a new page revision rather than modifying history in place.

Agents call a compact wiki query first. The response returns a short page excerpt, freshness metadata, and citation IDs. A separate source-expansion operation reads the original message windows when the wiki page is stale, incomplete, or needs verification.

## Privacy And Compatibility

All wiki files stay below `.tg-ecosystem/`, are ignored by Git and excluded from wheels/sdists. Markdown with frontmatter makes the layer readable by Codex and compatible with LLM-Wiki-style workflows without coupling the application to a third-party runtime.

## Validation

Tests will cover delta selection, citation preservation, versioned page updates, stale-page detection, Git/package exclusion, and compact lookup followed by raw-source expansion.
