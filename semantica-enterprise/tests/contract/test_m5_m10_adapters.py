from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from packages.semantica_adapter.extract import _effective_temperature, extract_semantics
from packages.semantica_adapter.governance import govern_entities
from packages.semantica_adapter.normalize import normalize_and_split
from packages.semantica_adapter.retrieval import fuse_results


def test_m5_normalization_has_stable_chunk_ids() -> None:
    kwargs = {
        "version_id": "version-1",
        "element_key": "element-1",
        "method": "recursive",
        "chunk_size": 100,
        "chunk_overlap": 10,
    }
    first = normalize_and_split("国联集团  \r\n建设组织级知识平台。" * 12, **kwargs)
    second = normalize_and_split("国联集团  \r\n建设组织级知识平台。" * 12, **kwargs)
    assert first
    assert [item.chunk_id for item in first] == [item.chunk_id for item in second]
    assert all("\r" not in item.text for item in first)


def test_m6_extraction_validates_and_clamps_model_output() -> None:
    output = extract_semantics(
        "国联集团发布 NexusOne。",
        chunk_key="chunk-1",
        api_key="test",
        model="test",
        base_url=None,
        generator=lambda _: {
            "entities": [{"text": "国联集团", "type": "组织", "confidence": 3}],
            "relations": [
                {
                    "subject": "国联集团",
                    "predicate": "发布",
                    "object": "NexusOne",
                    "confidence": "0.88",
                    "evidence": "发布 NexusOne",
                }
            ],
            "events": [{"type": "发布", "trigger": "发布", "participants": ["国联集团"], "confidence": 0.9}],
        },
    )
    assert output.entities[0]["confidence"] == 1.0
    assert output.relations[0]["predicate"] == "发布"
    assert output.events[0]["participants"] == ["国联集团"]


def test_m6_kimi_k3_uses_its_required_temperature() -> None:
    assert _effective_temperature("kimi-k3", 0.1) == 1.0
    assert _effective_temperature("other-model", 0.1) == 0.1


def test_m7_governance_merges_exact_mentions() -> None:
    governed, _ = govern_entities(
        [
            {"mention_id": "m1", "text": "国联集团", "entity_type": "组织", "confidence": 0.8},
            {"mention_id": "m2", "text": " 国联集团 ", "entity_type": "组织", "confidence": 0.9},
        ]
    )
    assert len(governed) == 1
    assert set(governed[0].mention_ids) == {"m1", "m2"}


def test_m9_embedding_fallback_is_rejected() -> None:
    fake = MagicMock()
    fake.get_method.return_value = "fallback"
    with patch("semantica.embeddings.text_embedder.TextEmbedder", return_value=fake):
        from packages.semantica_adapter.embedding import SemanticEmbedder

        with pytest.raises(RuntimeError, match="禁止使用"):
            SemanticEmbedder("missing-model")


def test_m10_rrf_fuses_same_fragment_across_channels() -> None:
    fused = fuse_results(
        [
            [{"id": "chunk-1", "score": 10, "channel": "keyword"}, {"id": "chunk-2", "score": 8}],
            [{"id": "chunk-1", "score": 0.9, "channel": "vector"}],
            [{"id": "chunk-3", "score": 0.8, "channel": "graph"}],
        ],
        3,
    )
    assert fused[0]["id"] == "chunk-1"
