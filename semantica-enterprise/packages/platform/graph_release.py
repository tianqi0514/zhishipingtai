from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.semantica_adapter.graph import publish_graph, validate_graph

from .config import get_settings
from .models import CanonicalEntity, Chunk, Document, Fact, GraphRelease, InferredFact


def publish_graph_snapshot(db: Session, tenant_id: str, space_id: str) -> GraphRelease:
    settings = get_settings()
    entities = list(
        db.scalars(
            select(CanonicalEntity).where(
                CanonicalEntity.tenant_id == tenant_id,
                CanonicalEntity.space_id == space_id,
                CanonicalEntity.status == "published",
                CanonicalEntity.deleted_at.is_(None),
            )
        )
    )
    candidate_facts = list(
        db.scalars(
            select(Fact).where(
                Fact.tenant_id == tenant_id,
                Fact.space_id == space_id,
                Fact.status == "published",
                Fact.deleted_at.is_(None),
            )
        )
    )
    asserted: list[Fact] = []
    for fact in candidate_facts:
        if fact.source_chunk_id is None:
            asserted.append(fact)
            continue
        source_chunk = db.get(Chunk, fact.source_chunk_id)
        source_document = db.get(Document, source_chunk.document_id) if source_chunk else None
        if (
            source_chunk is not None
            and source_chunk.deleted_at is None
            and source_document is not None
            and source_document.deleted_at is None
            and source_document.current_version_id == source_chunk.version_id
        ):
            asserted.append(fact)
    inferred = list(
        db.scalars(
            select(InferredFact).where(
                InferredFact.tenant_id == tenant_id,
                InferredFact.space_id == space_id,
                InferredFact.status == "published",
                InferredFact.deleted_at.is_(None),
            )
        )
    )
    graph_entities = [
        {
            "id": row.id,
            "name": row.canonical_name,
            "type": row.entity_type,
            "space_id": row.space_id,
        }
        for row in entities
    ]
    graph_relations = [
        {
            "id": row.id,
            "source": row.subject_entity_id,
            "target": row.object_entity_id,
            "type": row.predicate,
            "confidence": row.confidence,
            "origin": "asserted",
        }
        for row in asserted
        if row.object_entity_id
    ] + [
        {
            "id": row.id,
            "source": row.subject_entity_id,
            "target": row.object_entity_id,
            "type": row.predicate,
            "confidence": row.confidence,
            "origin": "inferred",
        }
        for row in inferred
        if row.object_entity_id
    ]
    validation = validate_graph(graph_entities, graph_relations)
    serious = [
        item for item in validation.get("issues", []) if item.get("severity") in {"critical", "error"}
    ]
    if serious:
        raise ValueError(f"知识图谱校验失败：{serious[:3]}")
    graph_number = (
        db.scalar(select(func.max(GraphRelease.release_number)).where(GraphRelease.space_id == space_id)) or 0
    ) + 1
    graph_name = f"space_{space_id.replace('-', '')}_r{graph_number}"
    publish_graph(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        graph_name=graph_name,
        entities=graph_entities,
        relationships=graph_relations,
    )
    release = GraphRelease(
        tenant_id=tenant_id,
        space_id=space_id,
        release_number=graph_number,
        graph_name=graph_name,
        entity_count=len(graph_entities),
        fact_count=len(graph_relations),
        validation_report={
            **validation,
            "asserted_facts": len([row for row in asserted if row.object_entity_id]),
            "inferred_facts": len([row for row in inferred if row.object_entity_id]),
        },
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    return release
