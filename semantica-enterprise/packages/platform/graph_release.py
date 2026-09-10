from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from packages.semantica_adapter.graph import publish_graph, validate_graph

from .config import get_settings
from .curation import effective_chunk_payloads, effective_entity, effective_fact
from .knowledge_processing import version_in_graph_projection
from .models import CanonicalEntity, Chunk, Document, DocumentVersion, Fact, GraphRelease, InferredFact
from .writing import content_hash


def publish_graph_snapshot(
    db: Session,
    tenant_id: str,
    space_id: str,
    *,
    include_pending_version_ids: set[str] | None = None,
    include_pending_entity_ids: set[str] | None = None,
) -> GraphRelease:
    # Multiple documents in one space may finish governance concurrently. The
    # immutable graph name and release number must be allocated serially or two
    # workers can publish the same ``rN`` graph. Transaction advisory locks are
    # released by the caller's commit/rollback and are a no-op in SQLite tests.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"graph-release:{tenant_id}:{space_id}"},
        )
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
    entities = [
        (row, value)
        for row, value in entities
        if value.get("status") == "published" or row.id in (include_pending_entity_ids or set())
    ]
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
        source_version = db.get(DocumentVersion, source_chunk.version_id) if source_chunk else None
        if (
            source_chunk is not None
            and source_chunk.deleted_at is None
            and source_document is not None
            and source_document.deleted_at is None
            and source_document.current_version_id == source_chunk.version_id
            and source_version is not None
            and (
                source_version.id in (include_pending_version_ids or set())
                or version_in_graph_projection(source_version.parse_summary)
            )
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
    # A release must not dereference mutable Fact/entity rows later. Store the
    # effective, evidence-linked assertions (including literal properties) that
    # were actually accepted at this publication boundary. No source text or
    # connector credentials are copied into the snapshot.
    names = {row.id: value for row, value in entities}
    evidence_facts = []
    for row, value in asserted:
        chunk = db.get(Chunk, row.source_chunk_id) if row.source_chunk_id else None
        document = db.get(Document, chunk.document_id) if chunk else None
        version = db.get(DocumentVersion, chunk.version_id) if chunk else None
        payload = effective_chunk_payloads(db, [chunk])[0] if chunk else None
        evidence_facts.append({
            "id": row.id, "space_id": space_id,
            "subject_entity_id": value["subject_entity_id"],
            "subject_name": names[value["subject_entity_id"]]["canonical_name"],
            "subject_type": names[value["subject_entity_id"]]["entity_type"],
            "predicate": value["predicate"],
            "object_entity_id": value.get("object_entity_id"),
            "object_name": names.get(value.get("object_entity_id"), {}).get("canonical_name"),
            "object_type": names.get(value.get("object_entity_id"), {}).get("entity_type"),
            "object_value": value.get("object_value"),
            "confidence": value.get("confidence", 1),
            "source_chunk_id": row.source_chunk_id,
            "source": {"chunk_id": chunk.id, "document_id": chunk.document_id,
                       "version_id": chunk.version_id, "title": document.title,
                       "version_number": version.version_number,
                       "page_number": chunk.page_number, "structural_path": chunk.structural_path,
                       "content_hash": chunk.content_hash, "effective_hash": payload["effective_hash"]} if chunk and document and version and payload else None,
        })
    evidence_snapshot = {"version": 1, "facts": evidence_facts}
    evidence_snapshot["checksum"] = content_hash(evidence_facts)
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
            "evidence_snapshot": evidence_snapshot,
            "asserted_facts": len([row for row, effective in asserted if effective.get("object_entity_id")]),
            "curated_entities": len([value for _, value in entities if "manual" in value.get("field_origins", {}).values()]),
            "curated_facts": len([value for _, value in asserted if "manual" in value.get("field_origins", {}).values()]),
            "inferred_facts": len([row for row in inferred if row.object_entity_id]),
        },
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    return release
