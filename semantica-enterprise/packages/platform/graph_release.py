from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.semantica_adapter.graph import publish_graph, validate_graph

from .config import get_settings
from .curation import effective_chunk_payloads, effective_entity, effective_fact
from .models import CanonicalEntity, Chunk, Document, Fact, GraphRelease, InferredFact


def publish_graph_snapshot(db: Session, tenant_id: str, space_id: str) -> GraphRelease:
    settings = get_settings()
    entity_rows = list(
        db.scalars(
            select(CanonicalEntity).where(
                CanonicalEntity.tenant_id == tenant_id,
                CanonicalEntity.space_id == space_id,
                CanonicalEntity.deleted_at.is_(None),
            )
        )
    )
    entities = [(row, effective_entity(db, row)) for row in entity_rows]
    entities = [(row, value) for row, value in entities if value.get("status") == "published"]
    active_entity_ids = {row.id for row, _ in entities}
    candidate_facts = list(
        db.scalars(
            select(Fact).where(
                Fact.tenant_id == tenant_id,
                Fact.space_id == space_id,
                Fact.deleted_at.is_(None),
            )
        )
    )
    asserted: list[tuple[Fact, dict]] = []
    for fact in candidate_facts:
        effective = effective_fact(db, fact)
        if effective.get("status") != "published":
            continue
        if effective.get("subject_entity_id") not in active_entity_ids:
            continue
        if effective.get("object_entity_id") and effective.get("object_entity_id") not in active_entity_ids:
            continue
        if fact.source_chunk_id is None:
            asserted.append((fact, effective))
            continue
        source_chunk = db.get(Chunk, fact.source_chunk_id)
        source_document = db.get(Document, source_chunk.document_id) if source_chunk else None
        if (
            source_chunk is not None
            and source_chunk.deleted_at is None
            and source_document is not None
            and source_document.deleted_at is None
            and source_document.current_version_id == source_chunk.version_id
            and effective_chunk_payloads(db, [source_chunk])
        ):
            asserted.append((fact, effective))
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
            "name": effective["canonical_name"],
            "type": effective["entity_type"],
            "space_id": row.space_id,
        }
        for row, effective in entities
    ]
    graph_relations = [
        {
            "id": row.id,
            "source": effective["subject_entity_id"],
            "target": effective["object_entity_id"],
            "type": effective["predicate"],
            "confidence": effective["confidence"],
            "origin": "curated" if any(value == "manual" for value in effective.get("field_origins", {}).values()) else "asserted",
        }
        for row, effective in asserted
        if effective.get("object_entity_id")
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
        and row.subject_entity_id in active_entity_ids
        and row.object_entity_id in active_entity_ids
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
            "asserted_facts": len([row for row, effective in asserted if effective.get("object_entity_id")]),
            "curated_entities": len([value for _, value in entities if "manual" in value.get("field_origins", {}).values()]),
            "curated_facts": len([value for _, value in asserted if "manual" in value.get("field_origins", {}).values()]),
            "inferred_facts": len([row for row in inferred if row.object_entity_id]),
        },
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    return release
