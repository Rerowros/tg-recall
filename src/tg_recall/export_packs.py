"""Private, portable and offline-verifiable AI export packs.

This module deliberately has no database, Telegram, CLI, or wiki dependency.
Callers must provide the already-authorized raw evidence and derived wiki pages.
That keeps pack generation bounded and makes an exported pack independently
verifiable on a machine that has neither a profile nor Telegram credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


PACK_SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CITATION_RE = re.compile(r"^tg://chat/-?\d+/message/\d+$")
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class PackError(ValueError):
    """Base error for invalid or unsafe export-pack input."""


class PackVerificationError(PackError):
    """A pack cannot be treated as trusted."""


@dataclass(frozen=True)
class ExportScope:
    """The authorization boundary declared in a portable pack manifest.

    A saved scope is portable provenance metadata.  Both a concrete chat
    selection and at least one date boundary are always required; this
    prevents an accidental full-archive export.
    """

    chat_ids: tuple[int, ...] = ()
    since: str | None = None
    until: str | None = None
    saved_scope: str | None = None
    max_records: int = 0
    token_budget: int = 0

    def validate(self) -> "ExportScope":
        if self.max_records <= 0:
            raise PackError("max_records must be a positive bounded value")
        if self.token_budget <= 0:
            raise PackError("token_budget must be a positive bounded value")
        if len(set(self.chat_ids)) != len(self.chat_ids) or any(not isinstance(value, int) for value in self.chat_ids):
            raise PackError("chat_ids must contain unique integer IDs")
        if self.saved_scope is not None and not _SAFE_SEGMENT_RE.fullmatch(self.saved_scope):
            raise PackError("saved_scope must be a normalized logical name")
        for value, label in ((self.since, "since"), (self.until, "until")):
            if value is not None:
                _parse_timestamp(value, label)
        if self.since and self.until and _parse_timestamp(self.since, "since") > _parse_timestamp(self.until, "until"):
            raise PackError("since must not be after until")
        # ``saved_scope`` is portable provenance metadata only.  The pure pack
        # builder cannot resolve a database scope name, so callers must pass
        # the already-resolved concrete boundary as well.
        if not self.chat_ids or not (self.since or self.until):
            raise PackError("scope requires resolved chat_ids with a since/until boundary")
        return self

    def as_manifest(self) -> dict[str, Any]:
        self.validate()
        return {
            "chat_ids": sorted(self.chat_ids),
            "max_records": self.max_records,
            "saved_scope": self.saved_scope,
            "since": self.since,
            "token_budget": self.token_budget,
            "until": self.until,
        }


@dataclass(frozen=True)
class WikiPage:
    """Derived wiki input supplied by a future wiki read adapter.

    ``assertions`` is intentionally passed through as structured data, but every
    assertion must contain a non-empty ``source_citations`` list.  ``updated_at``
    and ``snapshot_id`` let an offline consumer apply its own freshness policy.
    """

    slug: str
    markdown: str
    assertions: tuple[Mapping[str, Any], ...]
    snapshot_id: str
    updated_at: str


@dataclass(frozen=True)
class PackBuildResult:
    path: Path
    manifest: Mapping[str, Any]
    reused_paths: tuple[str, ...]


def build_pack(
    *,
    name: str,
    scope: ExportScope,
    raw_evidence: Iterable[Mapping[str, Any]] = (),
    wiki_pages: Iterable[WikiPage | Mapping[str, Any]] = (),
    profile_alias: str,
    default_exports_dir: str | Path,
    output_path: str | Path | None = None,
    profile_cache_dir: str | Path | None = None,
    previous_pack: str | Path | None = None,
    source_snapshot_ids: Iterable[str] = (),
    generator_version: str = "tg-recall",
    created_at: str | None = None,
) -> PackBuildResult:
    """Build a new immutable directory pack from explicit, already-read inputs.

    The default destination is ``default_exports_dir/name``.  An external
    destination is allowed only through ``output_path`` and is never serialized
    into the pack.  Existing destinations are refused rather than overwritten.
    """

    scope.validate()
    _require_segment(name, "pack name")
    _require_segment(profile_alias, "profile alias")
    if not generator_version:
        raise PackError("generator_version is required")

    exports_dir = Path(default_exports_dir).resolve()
    external_destination = output_path is not None
    destination = Path(output_path).expanduser().resolve() if output_path is not None else (exports_dir / name).resolve()
    if not external_destination:
        _assert_within(destination, exports_dir, "default destination")
    if destination.exists() or destination.is_symlink():
        raise PackError(f"destination already exists: {destination}")
    if destination.name != name:
        raise PackError("destination basename must equal the normalized pack name")

    wiki_content = _render_wiki_pages(wiki_pages, scope)
    snapshots = tuple(sorted(set(source_snapshot_ids) | {item[2]["freshness"]["snapshot_id"] for item in wiki_content}))
    for snapshot_id in snapshots:
        _require_segment(snapshot_id, "source_snapshot_id")
    raw_bytes, raw_citations, raw_count, _raw_tokens = _render_raw_evidence(raw_evidence, scope)
    content: dict[str, tuple[bytes, dict[str, Any]]] = {}
    if raw_count:
        content["raw/evidence.jsonl"] = (
            raw_bytes,
            {"layer": "raw", "citations": raw_citations, "source_snapshot_ids": list(snapshots)},
        )
    for logical_path, payload, metadata in wiki_content:
        content[logical_path] = (payload, metadata)
    if not content:
        raise PackError("a pack must contain explicit raw evidence, wiki pages, or both")
    previous = _verified_previous(previous_pack)
    created = created_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    _parse_timestamp(created, "created_at")
    file_entries = [
        {
            "logical_path": logical_path,
            "sha256": _sha256(content[logical_path][0]),
            "size_bytes": len(content[logical_path][0]),
            **content[logical_path][1],
        }
        for logical_path in sorted(content)
    ]
    manifest = {
        "created_at": created,
        "external_destination": external_destination,
        "files": file_entries,
        "generator_version": generator_version,
        "kind": _pack_kind(file_entries),
        "profile_alias": profile_alias,
        "schema_version": PACK_SCHEMA_VERSION,
        "scope": scope.as_manifest(),
        "source_snapshot_ids": list(snapshots),
    }
    validate_manifest(manifest)
    manifest_payload = _canonical_json(manifest)
    estimated_tokens = _conservative_pack_tokens(content, manifest_payload)
    if estimated_tokens > scope.token_budget:
        raise PackError(f"pack estimated tokens ({estimated_tokens}) exceeds token_budget ({scope.token_budget})")

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(profile_cache_dir).resolve() if (not external_destination and profile_cache_dir) else destination.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    # An external destination may be an arbitrary user-owned directory.  Only
    # harden profile-owned staging parents; the finalized pack tree itself is
    # hardened below in both modes.
    if not external_destination:
        _harden_directory(stage_parent)
    stage = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=stage_parent))
    reused: list[str] = []
    try:
        for logical_path in sorted(content):
            payload, _metadata = content[logical_path]
            target = _safe_pack_file(stage, logical_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            prior = previous.get(logical_path)
            if prior and prior["sha256"] == _sha256(payload) and prior["source"].is_file():
                shutil.copyfile(prior["source"], target)
                reused.append(logical_path)
            else:
                target.write_bytes(payload)
            _harden_file(target)

        manifest_path = stage / MANIFEST_NAME
        manifest_path.write_bytes(manifest_payload)
        _harden_file(manifest_path)
        _harden_tree(stage)
        try:
            os.replace(stage, destination)
        except OSError as exc:
            raise PackError("could not atomically finalize pack; choose a destination on the same filesystem") from exc
        return PackBuildResult(destination, manifest, tuple(reused))
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def inspect_pack(pack_path: str | Path) -> Mapping[str, Any]:
    """Load and structurally validate a manifest without consuming content."""

    root = _safe_pack_root(pack_path)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise PackVerificationError("manifest.json is missing or is a symbolic link")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackVerificationError("manifest.json is not valid UTF-8 JSON") from exc
    validate_manifest(manifest)
    return manifest


def verify_pack(pack_path: str | Path, *, strict: bool = True, max_wiki_age_seconds: int | None = None) -> Mapping[str, Any]:
    """Verify a pack entirely offline and return a compact inspection result."""

    if max_wiki_age_seconds is not None and max_wiki_age_seconds < 0:
        raise PackVerificationError("max_wiki_age_seconds must not be negative")
    root = _safe_pack_root(pack_path)
    manifest = inspect_pack(root)
    declared = {entry["logical_path"]: entry for entry in manifest["files"]}
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PackVerificationError(f"symbolic link is not allowed: {path.relative_to(root).as_posix()}")
        if path.is_file():
            logical_path = path.relative_to(root).as_posix()
            if logical_path != MANIFEST_NAME:
                actual.add(logical_path)
    if strict and actual != set(declared):
        unexpected = sorted(actual - set(declared))
        missing = sorted(set(declared) - actual)
        raise PackVerificationError(f"declared files mismatch: unexpected={unexpected}, missing={missing}")
    for logical_path, entry in declared.items():
        source = _safe_pack_file(root, logical_path)
        if not source.is_file() or source.is_symlink():
            raise PackVerificationError(f"declared file is missing or unsafe: {logical_path}")
        payload = source.read_bytes()
        if len(payload) != entry["size_bytes"]:
            raise PackVerificationError(f"size mismatch: {logical_path}")
        if _sha256(payload) != entry["sha256"]:
            raise PackVerificationError(f"hash mismatch: {logical_path}")
        _verify_file_payload(entry, payload, manifest["scope"])
        if max_wiki_age_seconds is not None and entry["layer"] == "derived":
            updated_at = _parse_timestamp(entry["freshness"]["updated_at"], "wiki freshness")
            age = (datetime.now(UTC) - updated_at).total_seconds()
            if age > max_wiki_age_seconds:
                raise PackVerificationError(f"stale wiki content: {logical_path}")
    _verify_wiki_pairs(declared)
    return {
        "files": len(declared),
        "kind": manifest["kind"],
        "ok": True,
        "schema_version": manifest["schema_version"],
        "strict": strict,
    }


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate only portable manifest data; no filesystem access is performed."""

    if not isinstance(manifest, Mapping) or set(manifest) != {
        "created_at", "external_destination", "files", "generator_version", "kind", "profile_alias", "schema_version", "scope", "source_snapshot_ids"
    }:
        raise PackVerificationError("manifest has missing or unsupported fields")
    if manifest["schema_version"] != PACK_SCHEMA_VERSION:
        raise PackVerificationError("unsupported pack schema_version")
    if not isinstance(manifest["external_destination"], bool):
        raise PackVerificationError("external_destination must be boolean")
    _require_segment(manifest["profile_alias"], "profile_alias", verification=True)
    if not isinstance(manifest["generator_version"], str) or not manifest["generator_version"]:
        raise PackVerificationError("generator_version must be non-empty")
    _parse_timestamp(manifest["created_at"], "created_at", verification=True)
    if manifest["kind"] not in {"raw", "wiki", "mixed"}:
        raise PackVerificationError("invalid pack kind")
    _validate_scope_manifest(manifest["scope"])
    snapshots = manifest["source_snapshot_ids"]
    if not isinstance(snapshots, list) or snapshots != sorted(set(snapshots)) or any(not isinstance(value, str) or not value for value in snapshots):
        raise PackVerificationError("source_snapshot_ids must be sorted unique non-empty strings")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise PackVerificationError("files must be a non-empty list")
    paths: set[str] = set()
    layers: set[str] = set()
    for entry in files:
        if not isinstance(entry, Mapping):
            raise PackVerificationError("file entry must be an object")
        allowed = {"logical_path", "sha256", "size_bytes", "layer", "citations", "source_snapshot_ids", "freshness"}
        if set(entry) - allowed or not {"logical_path", "sha256", "size_bytes", "layer", "citations", "source_snapshot_ids"} <= set(entry):
            raise PackVerificationError("file entry has missing or unsupported fields")
        path = entry["logical_path"]
        _validate_logical_path(path)
        if path in paths:
            raise PackVerificationError(f"duplicate logical path: {path}")
        paths.add(path)
        if not isinstance(entry["size_bytes"], int) or entry["size_bytes"] < 0:
            raise PackVerificationError(f"invalid size_bytes: {path}")
        if not isinstance(entry["sha256"], str) or not _SHA256_RE.fullmatch(entry["sha256"]):
            raise PackVerificationError(f"invalid sha256: {path}")
        if entry["layer"] not in {"raw", "derived"}:
            raise PackVerificationError(f"invalid layer: {path}")
        layers.add(entry["layer"])
        _validate_citations(entry["citations"], path)
        file_snapshots = entry["source_snapshot_ids"]
        if not isinstance(file_snapshots, list) or file_snapshots != sorted(set(file_snapshots)) or any(value not in snapshots for value in file_snapshots):
            raise PackVerificationError(f"invalid source_snapshot_ids: {path}")
        if entry["layer"] == "derived":
            freshness = entry.get("freshness")
            if not isinstance(freshness, Mapping) or set(freshness) != {"snapshot_id", "updated_at"}:
                raise PackVerificationError(f"derived file lacks freshness metadata: {path}")
            if freshness["snapshot_id"] not in snapshots:
                raise PackVerificationError(f"derived file snapshot is undeclared: {path}")
            _parse_timestamp(freshness["updated_at"], "freshness.updated_at", verification=True)
        elif "freshness" in entry:
            raise PackVerificationError(f"raw file must not include freshness: {path}")
    expected_kind = "mixed" if len(layers) == 2 else ("raw" if layers == {"raw"} else "wiki")
    if manifest["kind"] != expected_kind:
        raise PackVerificationError("manifest kind does not match file layers")


def _render_raw_evidence(records: Iterable[Mapping[str, Any]], scope: ExportScope) -> tuple[bytes, list[str], int, int]:
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise PackError("raw evidence entries must be mappings")
        chat_id = record.get("chat_id")
        message_id = record.get("message_id")
        timestamp = record.get("timestamp", record.get("date"))
        if not isinstance(chat_id, int) or not isinstance(message_id, int) or message_id < 0:
            raise PackError("raw evidence requires integer chat_id and non-negative message_id")
        if not isinstance(timestamp, str):
            raise PackError("raw evidence requires timestamp")
        occurred = _parse_timestamp(timestamp, "raw timestamp")
        if scope.chat_ids and chat_id not in scope.chat_ids:
            raise PackError(f"raw evidence is outside declared chat scope: {chat_id}")
        if scope.since and occurred < _parse_timestamp(scope.since, "since"):
            raise PackError("raw evidence is before declared scope")
        if scope.until and occurred > _parse_timestamp(scope.until, "until"):
            raise PackError("raw evidence is after declared scope")
        text = record.get("text", "")
        if not isinstance(text, str):
            raise PackError("raw evidence text must be a string")
        row = {
            "chat_id": chat_id,
            "citation": f"tg://chat/{chat_id}/message/{message_id}",
            "message_id": message_id,
            "text": text,
            "timestamp": timestamp,
        }
        for field in ("sender_id", "sender_name", "reply_to_message_id", "media_type", "has_media"):
            if field in record and record[field] is not None:
                row[field] = record[field]
        rows.append(row)
    rows.sort(key=lambda row: (row["timestamp"], row["chat_id"], row["message_id"]))
    if len(rows) > scope.max_records:
        raise PackError(f"raw evidence count ({len(rows)}) exceeds max_records ({scope.max_records})")
    citations = [row["citation"] for row in rows]
    if len(set(citations)) != len(citations):
        raise PackError("raw evidence contains duplicate citations")
    payload = b"".join(_canonical_json(row) + b"\n" for row in rows)
    # The final pack budget is calculated once from every serialized artifact
    # and the manifest before a staging directory is created.  This return
    # value is retained for the compact renderer contract only.
    return payload, citations, len(rows), len(payload)


def _render_wiki_pages(
    pages: Iterable[WikiPage | Mapping[str, Any]], scope: ExportScope
) -> list[tuple[str, bytes, dict[str, Any]]]:
    result: list[tuple[str, bytes, dict[str, Any]]] = []
    seen: set[str] = set()
    for value in pages:
        page = value if isinstance(value, WikiPage) else WikiPage(
            slug=str(value.get("slug", "")), markdown=value.get("markdown", ""), assertions=tuple(value.get("assertions", ())),
            snapshot_id=str(value.get("snapshot_id", "")), updated_at=str(value.get("updated_at", "")),
        )
        _require_segment(page.slug, "wiki slug")
        if page.slug in seen:
            raise PackError(f"duplicate wiki slug: {page.slug}")
        seen.add(page.slug)
        if not isinstance(page.markdown, str) or not page.markdown:
            raise PackError("wiki markdown must be non-empty")
        _require_segment(page.snapshot_id, "wiki snapshot_id")
        _parse_timestamp(page.updated_at, "wiki updated_at")
        assertions = [dict(assertion) for assertion in page.assertions]
        citations: list[str] = []
        for assertion in assertions:
            sources = assertion.get("source_citations")
            _validate_citations(sources, f"wiki assertion {page.slug}")
            if any(_citation_chat_id(citation) not in scope.chat_ids for citation in sources):
                raise PackError("wiki assertion citations exceed the declared chat scope")
            citations.extend(sources)
        assertions.sort(key=_canonical_json)
        citations = sorted(set(citations))
        metadata = {
            "layer": "derived",
            "citations": citations,
            "source_snapshot_ids": [page.snapshot_id],
            "freshness": {"snapshot_id": page.snapshot_id, "updated_at": page.updated_at},
        }
        result.extend([
            (f"wiki/{page.slug}.md", page.markdown.encode("utf-8"), metadata),
            (f"wiki/{page.slug}.assertions.json", _canonical_json({"assertions": assertions}), metadata),
        ])
    return result


def _verified_previous(previous_pack: str | Path | None) -> dict[str, dict[str, Any]]:
    if previous_pack is None:
        return {}
    verify_pack(previous_pack, strict=True)
    root = Path(previous_pack).resolve()
    manifest = inspect_pack(root)
    return {entry["logical_path"]: {**entry, "source": _safe_pack_file(root, entry["logical_path"])} for entry in manifest["files"]}


def _verify_file_payload(entry: Mapping[str, Any], payload: bytes, scope_data: Mapping[str, Any]) -> None:
    try:
        if entry["logical_path"] == "raw/evidence.jsonl":
            rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line]
            allowed = {"chat_id", "citation", "message_id", "text", "timestamp", "sender_id", "sender_name", "reply_to_message_id", "media_type", "has_media"}
            required = {"chat_id", "citation", "message_id", "text", "timestamp"}
            if any(not isinstance(row, Mapping) or not required <= set(row) or set(row) - allowed for row in rows):
                raise PackVerificationError("raw evidence contains unsafe or undeclared fields")
            citations = [row.get("citation") for row in rows]
            if citations != entry["citations"] or len(rows) > scope_data["max_records"]:
                raise PackVerificationError("raw evidence contains undeclared records")
            if any(row["citation"] != f"tg://chat/{row['chat_id']}/message/{row['message_id']}" for row in rows):
                raise PackVerificationError("raw evidence citation does not match record identity")
            scope = ExportScope(
                tuple(scope_data["chat_ids"]), scope_data["since"], scope_data["until"], scope_data["saved_scope"],
                scope_data["max_records"], scope_data["token_budget"],
            ).validate()
            _render_raw_evidence(rows, scope)
        elif entry["logical_path"].endswith(".assertions.json"):
            assertions = json.loads(payload.decode("utf-8")).get("assertions")
            if not isinstance(assertions, list):
                raise PackVerificationError("wiki assertion mapping is invalid")
            found = sorted({citation for assertion in assertions for citation in assertion.get("source_citations", [])})
            if found != entry["citations"]:
                raise PackVerificationError("wiki assertion sources do not match manifest")
    except (UnicodeDecodeError, json.JSONDecodeError, PackError, AttributeError, TypeError) as exc:
        if isinstance(exc, PackVerificationError):
            raise
        raise PackVerificationError(f"invalid content: {entry['logical_path']}") from exc


def _verify_wiki_pairs(entries: Mapping[str, Mapping[str, Any]]) -> None:
    """Keep Markdown's derived layer tied to its assertion/source mapping."""

    for logical_path, entry in entries.items():
        if entry["layer"] != "derived" or not logical_path.endswith(".md"):
            continue
        mapping_path = logical_path.removesuffix(".md") + ".assertions.json"
        mapping = entries.get(mapping_path)
        if mapping is None:
            raise PackVerificationError(f"wiki page lacks assertion mapping: {logical_path}")
        if mapping["citations"] != entry["citations"] or mapping["freshness"] != entry["freshness"]:
            raise PackVerificationError(f"wiki page provenance does not match assertion mapping: {logical_path}")


def _validate_scope_manifest(scope: Any) -> None:
    if not isinstance(scope, Mapping) or set(scope) != {"chat_ids", "max_records", "saved_scope", "since", "token_budget", "until"}:
        raise PackVerificationError("invalid scope metadata")
    try:
        ExportScope(tuple(scope["chat_ids"]), scope["since"], scope["until"], scope["saved_scope"], scope["max_records"], scope["token_budget"]).validate()
    except (TypeError, PackError) as exc:
        raise PackVerificationError("invalid scope metadata") from exc


def _validate_citations(citations: Any, label: str) -> None:
    if not isinstance(citations, list) or citations != sorted(set(citations)) or any(not isinstance(value, str) or not _CITATION_RE.fullmatch(value) for value in citations):
        raise PackVerificationError(f"invalid citations: {label}")


def _validate_logical_path(value: Any) -> None:
    if not isinstance(value, str) or value == MANIFEST_NAME:
        raise PackVerificationError("invalid logical path")
    path = PurePosixPath(value)
    if path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in path.parts):
        raise PackVerificationError(f"unsafe logical path: {value}")


def _safe_pack_root(pack_path: str | Path) -> Path:
    root = Path(pack_path)
    if root.is_symlink() or not root.is_dir():
        raise PackVerificationError("pack root must be a real directory")
    return root.resolve()


def _safe_pack_file(root: Path, logical_path: str) -> Path:
    _validate_logical_path(logical_path)
    candidate = root.joinpath(*PurePosixPath(logical_path).parts)
    _assert_within(candidate, root, "logical path")
    return candidate


def _assert_within(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PackError(f"{label} escapes its permitted root") from exc


def _require_segment(value: Any, label: str, *, verification: bool = False) -> None:
    if not isinstance(value, str) or not _SAFE_SEGMENT_RE.fullmatch(value):
        error = PackVerificationError if verification else PackError
        raise error(f"{label} must be a normalized logical name")


def _parse_timestamp(value: Any, label: str, *, verification: bool = False) -> datetime:
    error = PackVerificationError if verification else PackError
    if not isinstance(value, str):
        raise error(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise error(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise error(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _conservative_pack_tokens(
    content: Mapping[str, tuple[bytes, Mapping[str, Any]]], manifest_payload: bytes
) -> int:
    """Count every final file byte; one UTF-8 byte is a conservative token cap."""

    return len(manifest_payload) + sum(len(payload) for payload, _metadata in content.values())


def _citation_chat_id(citation: str) -> int:
    match = _CITATION_RE.fullmatch(citation)
    if match is None:  # Defensive: callers normally passed _validate_citations.
        raise PackError("citation must be an exact tg://chat/.../message/... reference")
    return int(match.group(0).removeprefix("tg://chat/").split("/", 1)[0])


def _pack_kind(entries: Iterable[Mapping[str, Any]]) -> str:
    layers = {entry["layer"] for entry in entries}
    return "mixed" if len(layers) == 2 else ("raw" if layers == {"raw"} else "wiki")


def _harden_file(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _harden_directory(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass


def _harden_tree(root: Path) -> None:
    for path in root.rglob("*"):
        _harden_directory(path) if path.is_dir() else _harden_file(path)
    _harden_directory(root)
