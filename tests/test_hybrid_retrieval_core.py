from __future__ import annotations

from datetime import UTC, datetime
import sys
from types import SimpleNamespace

import pytest

from tg_recall.hybrid_retrieval import (
    EmbeddingModelMetadata,
    EmbeddingRecord,
    EmbeddingBatch,
    IndexCheckpoint,
    LocalEmbeddingConfig,
    RetrievalCandidate,
    RetrievalMode,
    SemanticUnavailableError,
    SentenceTransformersLocalProvider,
    SourceType,
    StoredVectorMetadata,
    decide_retrieval_mode,
    directory_source_hash,
    embed_batch,
    fuse_candidates,
    plan_embedding_batches,
    text_source_hash,
)


def model() -> EmbeddingModelMetadata:
    return EmbeddingModelMetadata("fixture", "fixture-model", 3, "a" * 64)


def record(source_id: int, text: str = "text", *, source_type: SourceType = SourceType.MESSAGE) -> EmbeddingRecord:
    return EmbeddingRecord.from_text(source_type, source_id, 10, source_id, text)


def candidate(
    score: float,
    channel: RetrievalMode,
    *,
    source_type: SourceType = SourceType.MESSAGE,
    source_id: int = 1,
    citation: str = "tg://chat/10/message/1",
) -> RetrievalCandidate:
    return RetrievalCandidate(citation, 10, 1, "evidence", source_type, source_id, score, channel, source_id if source_type == SourceType.TRANSCRIPT else None)


def test_source_hash_is_utf8_deterministic() -> None:
    assert text_source_hash("привет") == text_source_hash("привет")
    assert text_source_hash("привет") != text_source_hash("Привет")


def test_model_metadata_requires_sha256() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        EmbeddingModelMetadata("fixture", "model", 3, "wrong")
    with pytest.raises(ValueError, match="SHA-256"):
        EmbeddingModelMetadata("fixture", "model", 3, "g" * 64)
    with pytest.raises(ValueError, match="normalized lowercase"):
        EmbeddingModelMetadata("fixture", "model", 3, "A" * 64)


def test_local_provider_never_uses_model_id_or_downloads(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeSentenceTransformer:
        def __init__(self, model_path: str, **kwargs: object) -> None:
            calls.append((model_path, kwargs))

        def get_sentence_embedding_dimension(self) -> int:
            return 3

    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=FakeSentenceTransformer))
    provider = SentenceTransformersLocalProvider(LocalEmbeddingConfig(tmp_path))
    assert calls == [(str(tmp_path), {"device": "cpu", "local_files_only": True, "trust_remote_code": False})]
    assert provider.metadata.dimensions == 3
    with pytest.raises(ValueError, match="existing local directory"):
        LocalEmbeddingConfig(tmp_path / "not-downloaded-model")


def test_directory_hash_detects_local_model_change(tmp_path) -> None:
    (tmp_path / "config.json").write_text("one", encoding="utf-8")
    first = directory_source_hash(tmp_path)
    (tmp_path / "config.json").write_text("two", encoding="utf-8")
    assert first != directory_source_hash(tmp_path)


def test_plan_batches_skips_current_and_resumes_checkpoint() -> None:
    current, done, stale = record(1, "current"), record(2, "done"), record(3, "stale")
    stored = [StoredVectorMetadata(SourceType.MESSAGE, 1, current.source_hash, model(), datetime.now(UTC))]
    checkpoint = IndexCheckpoint(model().identity, ((done.source_type, done.source_id, done.source_hash),))
    batches = plan_embedding_batches([stale, done, current], model(), checkpoint, stored, batch_size=1)
    assert [[item.source_id for item in batch.records] for batch in batches] == [[3]]


def test_stale_model_and_source_are_planned_again() -> None:
    item = record(1, "changed")
    old = StoredVectorMetadata(SourceType.MESSAGE, 1, text_source_hash("old"), model(), datetime.now(UTC))
    assert [batch.records for batch in plan_embedding_batches([item], model(), None, [old])] == [(item,)]


def test_checkpoint_advances_only_completed_batch() -> None:
    first, second = record(1), record(2)
    checkpoint = IndexCheckpoint(model().identity).advance([first], model())
    assert checkpoint.completed(first, model())
    assert not checkpoint.completed(second, model())


def test_embed_batch_returns_only_complete_dimension_compatible_vectors() -> None:
    class FixtureProvider:
        metadata = model()

        def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
            assert texts == ["first", "second"]
            return [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]

    batch = EmbeddingBatch(0, (record(1, "first"), record(2, "second")))
    embedded = embed_batch(FixtureProvider(), batch)
    assert [item.record.source_id for item in embedded] == [1, 2]
    assert all(item.model == model() for item in embedded)


def test_embed_batch_rejects_incomplete_provider_result() -> None:
    class BrokenProvider:
        metadata = model()

        def embed(self, _texts: list[str]) -> list[tuple[float, ...]]:
            return [(1.0, 0.0, 0.0)]

    with pytest.raises(RuntimeError, match="incomplete batch"):
        embed_batch(BrokenProvider(), EmbeddingBatch(0, (record(1), record(2))))


def test_fusion_is_deterministic_and_deduplicates_transcript() -> None:
    keyword = candidate(10, RetrievalMode.KEYWORD)
    transcript = candidate(0.7, RetrievalMode.SEMANTIC, source_type=SourceType.TRANSCRIPT, source_id=8)
    other = candidate(9, RetrievalMode.KEYWORD, source_id=2, citation="tg://chat/10/message/2")
    first = fuse_candidates([other, keyword], [transcript])
    second = fuse_candidates([keyword, other], [transcript])
    assert first == second
    assert len(first) == 2
    assert first[0].citation == "tg://chat/10/message/1"
    assert first[0].provenance == ("keyword", "semantic")
    assert first[0].transcript_ids == (8,)


def test_fusion_equal_channel_scores_is_stable() -> None:
    items = fuse_candidates(
        [candidate(1, RetrievalMode.KEYWORD, citation="tg://chat/10/message/2"), candidate(1, RetrievalMode.KEYWORD)],
        [],
    )
    assert [item.citation for item in items] == ["tg://chat/10/message/1", "tg://chat/10/message/2"]


def test_auto_falls_back_but_strict_semantic_is_explicit() -> None:
    decision = decide_retrieval_mode(RetrievalMode.AUTO, provider_available=False, index_current=False)
    assert decision.mode == RetrievalMode.KEYWORD
    assert decision.fallback_reason == "embedding_provider_unavailable"
    with pytest.raises(SemanticUnavailableError, match="semantic_unavailable: embedding_provider_unavailable"):
        decide_retrieval_mode(RetrievalMode.SEMANTIC, provider_available=False, index_current=False)


def test_current_provider_and_index_choose_hybrid() -> None:
    assert decide_retrieval_mode(RetrievalMode.AUTO, provider_available=True, index_current=True).mode == RetrievalMode.HYBRID
