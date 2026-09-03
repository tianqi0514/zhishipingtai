from __future__ import annotations

from apps.api.schemas import KnowledgeFactCreate


def test_knowledge_fact_accepts_optional_evidence_chunk() -> None:
    payload = KnowledgeFactCreate(
        space_id="space",
        subject_entity_id="subject",
        predicate="贯通",
        object_entity_id="object",
        source_chunk_id="chunk",
    )
    assert payload.source_chunk_id == "chunk"
