"""Local-only primitives for the optional hybrid retrieval index.

This module deliberately has no database or CLI dependency.  Persistence and
access-control are supplied by the archive layer; the core is therefore easy
to test with fixtures and cannot make an embedding provider a hidden network
dependency.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
import re
from typing import Protocol, runtime_checkable


class SemanticUnavailableError(RuntimeError):
    """Stable error for callers which explicitly require vector retrieval."""

    code = "semantic_unavailable"

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"{self.code}: {reason}")


class RetrievalMode(StrEnum):
    AUTO = "auto"
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


class SourceType(StrEnum):
    MESSAGE = "message"
    TRANSCRIPT = "transcript"


@dataclass(frozen=True)
class LocalEmbeddingConfig:
    """An explicit, already-downloaded local model configuration.

    ``model_path`` must be a directory on the local filesystem.  The core
    never accepts a model hub identifier: this prevents provider fallback from
    acquiring a model or sending archive content over the network.
    """

    model_path: Path
    device: str = "cpu"
    normalize_embeddings: bool = True
    batch_size: int = 32

    def __post_init__(self) -> None:
        if not self.model_path.is_dir():
            raise ValueError("embedding model_path must be an existing local directory")
        if self.batch_size < 1:
            raise ValueError("embedding batch_size must be positive")


@dataclass(frozen=True)
class EmbeddingModelMetadata:
    provider: str
    model_id: str
    dimensions: int
    model_source_hash: str

    def __post_init__(self) -> None:
        if not self.provider or not self.model_id:
            raise ValueError("embedding provider and model_id are required")
        if self.dimensions < 1:
            raise ValueError("embedding dimensions must be positive")
        if not re.fullmatch(r"[0-9a-f]{64}", self.model_source_hash):
            raise ValueError("model_source_hash must be a normalized lowercase SHA-256 hex digest")

    @property
    def identity(self) -> str:
        # Dimensions are part of the identity: the same local files cannot be
        # safely mixed with a provider which reports a different shape.
        return f"{self.provider}:{self.model_id}:{self.dimensions}:{self.model_source_hash}"


@dataclass(frozen=True)
class EmbeddingRecord:
    source_type: SourceType
    source_id: int
    chat_id: int
    message_id: int
    text: str
    source_hash: str

    @classmethod
    def from_text(
        cls, source_type: SourceType, source_id: int, chat_id: int, message_id: int, text: str
    ) -> "EmbeddingRecord":
        return cls(source_type, source_id, chat_id, message_id, text, text_source_hash(text))


@dataclass(frozen=True)
class StoredVectorMetadata:
    source_type: SourceType
    source_id: int
    source_hash: str
    model: EmbeddingModelMetadata
    indexed_at: datetime

    def is_current_for(self, record: EmbeddingRecord, model: EmbeddingModelMetadata) -> bool:
        return (
            self.source_type == record.source_type
            and self.source_id == record.source_id
            and self.source_hash == record.source_hash
            and self.model == model
        )


@dataclass(frozen=True)
class IndexCheckpoint:
    """Persistence-neutral checkpoint stored after a whole successful batch."""

    model_identity: str
    completed_source_keys: tuple[tuple[SourceType, int, str], ...] = ()
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def completed(self, record: EmbeddingRecord, model: EmbeddingModelMetadata) -> bool:
        return model.identity == self.model_identity and (
            record.source_type,
            record.source_id,
            record.source_hash,
        ) in self.completed_source_keys

    def advance(self, records: Iterable[EmbeddingRecord], model: EmbeddingModelMetadata) -> "IndexCheckpoint":
        if model.identity != self.model_identity:
            return IndexCheckpoint(
                model_identity=model.identity,
                completed_source_keys=tuple(_record_key(record) for record in records),
            )
        completed = {*self.completed_source_keys, *(_record_key(record) for record in records)}
        return IndexCheckpoint(model.identity, tuple(sorted(completed, key=_checkpoint_sort_key)))


@dataclass(frozen=True)
class EmbeddingBatch:
    ordinal: int
    records: tuple[EmbeddingRecord, ...]


@dataclass(frozen=True)
class EmbeddedVector:
    record: EmbeddingRecord
    values: tuple[float, ...]
    model: EmbeddingModelMetadata


@runtime_checkable
class EmbeddingProvider(Protocol):
    """A local provider.  Implementations must not download models implicitly."""

    @property
    def metadata(self) -> EmbeddingModelMetadata: ...

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...


class SentenceTransformersLocalProvider:
    """Optional SentenceTransformers provider pinned to an existing local path.

    SentenceTransformer supports ``local_files_only``; it is set together with
    an existing directory check and ``trust_remote_code=False`` so model
    loading cannot fetch files or execute repository-provided Python.
    """

    def __init__(self, config: LocalEmbeddingConfig):
        self._config = config
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - tested without extra installed
            raise SemanticUnavailableError("local_embedding_runtime_not_installed") from exc
        try:
            self._model = SentenceTransformer(
                str(config.model_path),
                device=config.device,
                local_files_only=True,
                trust_remote_code=False,
            )
        except Exception as exc:
            raise SemanticUnavailableError("local_embedding_model_unavailable") from exc
        model_hash = directory_source_hash(config.model_path)
        dimensions = int(self._model.get_sentence_embedding_dimension())
        self._metadata = EmbeddingModelMetadata(
            provider="sentence-transformers-local",
            model_id=config.model_path.name,
            dimensions=dimensions,
            model_source_hash=model_hash,
        )

    @property
    def metadata(self) -> EmbeddingModelMetadata:
        return self._metadata

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        result = self._model.encode(
            list(texts),
            batch_size=self._config.batch_size,
            normalize_embeddings=self._config.normalize_embeddings,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        vectors = [tuple(float(value) for value in row) for row in result]
        if len(vectors) != len(texts) or any(len(vector) != self.metadata.dimensions for vector in vectors):
            raise RuntimeError("local embedding provider returned incompatible dimensions")
        return vectors


def plan_embedding_batches(
    records: Iterable[EmbeddingRecord],
    model: EmbeddingModelMetadata,
    checkpoint: IndexCheckpoint | None,
    stored: Iterable[StoredVectorMetadata] = (),
    *,
    batch_size: int = 32,
) -> list[EmbeddingBatch]:
    """Return only missing or stale work in deterministic, resumable batches."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    stored_by_key = {(item.source_type, item.source_id): item for item in stored}
    pending = []
    for record in sorted(records, key=_record_sort_key):
        if checkpoint and checkpoint.completed(record, model):
            continue
        stored_item = stored_by_key.get((record.source_type, record.source_id))
        if stored_item is None or not stored_item.is_current_for(record, model):
            pending.append(record)
    return [EmbeddingBatch(index, tuple(pending[start : start + batch_size])) for index, start in enumerate(range(0, len(pending), batch_size))]


def embed_batch(provider: EmbeddingProvider, batch: EmbeddingBatch) -> list[EmbeddedVector]:
    """Embed one planned batch; callers persist the returned whole batch atomically.

    A failed provider call leaves the supplied checkpoint untouched.  The
    storage layer may therefore commit vectors and then advance its checkpoint
    only after this function returns a complete, dimension-compatible batch.
    """

    vectors = provider.embed([record.text for record in batch.records])
    metadata = provider.metadata
    if len(vectors) != len(batch.records):
        raise RuntimeError("local embedding provider returned an incomplete batch")
    if any(len(vector) != metadata.dimensions for vector in vectors):
        raise RuntimeError("local embedding provider returned incompatible dimensions")
    return [EmbeddedVector(record, tuple(vector), metadata) for record, vector in zip(batch.records, vectors, strict=True)]


@dataclass(frozen=True)
class RetrievalCandidate:
    """A pre-filtered candidate from FTS or an actual vector similarity index."""

    citation: str
    chat_id: int
    message_id: int
    text: str
    source_type: SourceType
    source_id: int
    score: float
    channel: RetrievalMode
    transcript_id: int | None = None

    def __post_init__(self) -> None:
        if self.channel not in {RetrievalMode.KEYWORD, RetrievalMode.SEMANTIC}:
            raise ValueError("candidate channel must be keyword or semantic")


@dataclass(frozen=True)
class RankedEvidence:
    citation: str
    chat_id: int
    message_id: int
    text: str
    score: float
    provenance: tuple[str, ...]
    source_types: tuple[SourceType, ...]
    transcript_ids: tuple[int, ...]


@dataclass(frozen=True)
class RetrievalDecision:
    mode: RetrievalMode
    fallback_reason: str | None = None


def decide_retrieval_mode(
    requested: RetrievalMode,
    *,
    provider_available: bool,
    index_current: bool,
) -> RetrievalDecision:
    """Choose only genuine vector retrieval; never substitute token overlap."""

    if requested == RetrievalMode.KEYWORD:
        return RetrievalDecision(RetrievalMode.KEYWORD)
    if provider_available and index_current:
        if requested == RetrievalMode.SEMANTIC:
            return RetrievalDecision(RetrievalMode.SEMANTIC)
        return RetrievalDecision(RetrievalMode.HYBRID)
    reason = "embedding_provider_unavailable" if not provider_available else "embedding_index_unavailable"
    if requested == RetrievalMode.SEMANTIC:
        raise SemanticUnavailableError(reason)
    return RetrievalDecision(RetrievalMode.KEYWORD, fallback_reason=reason)


def fuse_candidates(
    keyword_candidates: Iterable[RetrievalCandidate],
    semantic_candidates: Iterable[RetrievalCandidate],
    *,
    keyword_weight: float = 0.45,
    semantic_weight: float = 0.55,
    limit: int = 20,
) -> list[RankedEvidence]:
    """Normalize each bounded channel, sum weights, then collapse by citation.

    Equal scores normalize to 1.0; ties are always resolved by citation and
    source identifiers, making fixture results stable across SQLite row order.
    """

    if limit < 1:
        return []
    if keyword_weight < 0 or semantic_weight < 0 or keyword_weight + semantic_weight <= 0:
        raise ValueError("fusion weights must be non-negative with a positive sum")
    weighted: list[tuple[RetrievalCandidate, float]] = []
    for candidates, weight in ((keyword_candidates, keyword_weight), (semantic_candidates, semantic_weight)):
        ordered = sorted(candidates, key=_candidate_sort_key)
        scores = _normalize_scores(candidate.score for candidate in ordered)
        weighted.extend((candidate, score * weight) for candidate, score in zip(ordered, scores, strict=True))
    grouped: dict[str, list[tuple[RetrievalCandidate, float]]] = defaultdict(list)
    for candidate, score in weighted:
        grouped[candidate.citation].append((candidate, score))
    evidence = []
    for citation, entries in grouped.items():
        ordered_entries = sorted(entries, key=lambda item: _candidate_sort_key(item[0]))
        representative = ordered_entries[0][0]
        provenance = tuple(sorted({candidate.channel.value for candidate, _ in entries}))
        source_types = tuple(sorted({candidate.source_type for candidate, _ in entries}, key=str))
        transcript_ids = tuple(sorted({candidate.transcript_id for candidate, _ in entries if candidate.transcript_id is not None}))
        evidence.append(
            RankedEvidence(
                citation=citation,
                chat_id=representative.chat_id,
                message_id=representative.message_id,
                text=representative.text,
                score=sum(score for _, score in entries),
                provenance=provenance,
                source_types=source_types,
                transcript_ids=transcript_ids,
            )
        )
    return sorted(evidence, key=lambda item: (-item.score, item.citation, item.transcript_ids))[:limit]


def text_source_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def directory_source_hash(path: Path) -> str:
    """Stable SHA-256 of local model files and their contents, never symlinks."""

    if not path.is_dir():
        raise ValueError("model path must be an existing directory")
    digest = sha256()
    for file_path in sorted((item for item in path.rglob("*") if item.is_file() and not item.is_symlink()), key=lambda item: item.relative_to(path).as_posix()):
        digest.update(file_path.relative_to(path).as_posix().encode("utf-8"))
        with file_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _normalize_scores(scores: Iterable[float]) -> list[float]:
    values = list(scores)
    if not values:
        return []
    minimum, maximum = min(values), max(values)
    if maximum == minimum:
        return [1.0] * len(values)
    return [(value - minimum) / (maximum - minimum) for value in values]


def _record_key(record: EmbeddingRecord) -> tuple[SourceType, int, str]:
    return record.source_type, record.source_id, record.source_hash


def _record_sort_key(record: EmbeddingRecord) -> tuple[str, int, int, int]:
    return record.source_type.value, record.chat_id, record.message_id, record.source_id


def _checkpoint_sort_key(item: tuple[SourceType, int, str]) -> tuple[str, int, str]:
    return item[0].value, item[1], item[2]


def _candidate_sort_key(candidate: RetrievalCandidate) -> tuple[float, str, str, int]:
    return -candidate.score, candidate.citation, candidate.source_type.value, candidate.source_id
