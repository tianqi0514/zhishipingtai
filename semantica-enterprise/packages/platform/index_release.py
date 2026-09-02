from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.semantica_adapter.embedding import SemanticEmbedder
from packages.semantica_adapter.indexing import SearchIndexer, search_point_id

from .config import get_settings
from .curation import effective_chunk_payloads
from .media import media_type_for
from .models import (
    Chunk,
    ContentElement,
    Document,
    DocumentVersion,
    GraphRelease,
    IndexRelease,
    KnowledgeRelease,
    ModelConfig,
)


def publish_index_snapshot(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    graph_release: GraphRelease,
    embedding_model: ModelConfig | None = None,
) -> tuple[IndexRelease, dict]:
    """Build one immutable search snapshot from the effective curated chunks."""
    settings = get_settings()
    previous = db.scalar(
        select(IndexRelease)
        .where(
            IndexRelease.tenant_id == tenant_id,
            IndexRelease.space_id == space_id,
            IndexRelease.status == "published",
            IndexRelease.deleted_at.is_(None),
        )
        .order_by(IndexRelease.release_number.desc())
        .limit(1)
    )
    if embedding_model is None:
        if previous is None:
            raise RuntimeError("知识空间尚无向量模型发布记录")
        embedding_model = db.get(ModelConfig, previous.model_config_id)
    if embedding_model is None or not embedding_model.enabled:
        raise RuntimeError("当前向量模型不可用")

    raw_chunks: list[Chunk] = []
    for chunk in db.scalars(
        select(Chunk).where(
            Chunk.tenant_id == tenant_id,
            Chunk.space_id == space_id,
            Chunk.deleted_at.is_(None),
        )
    ):
        document = db.get(Document, chunk.document_id)
        if document is None or document.deleted_at is not None or document.current_version_id != chunk.version_id:
            continue
        raw_chunks.append(chunk)
    effective = effective_chunk_payloads(db, raw_chunks)
    chunks: list[dict] = []
    for item in effective:
        row = item["row"]
        document = db.get(Document, row.document_id)
        version = db.get(DocumentVersion, row.version_id)
        element = db.get(ContentElement, row.element_id) if row.element_id else None
        element_metadata = dict(element.element_metadata or {}) if element else {}
        media_type = media_type_for(version.filename, version.content_type) if version else None
        point_key = row.chunk_id if item["effective_hash"] == row.content_hash else f"{row.chunk_id}:{item['effective_hash']}"
        chunks.append({
            "id": search_point_id(point_key),
            "chunk_db_id": row.id,
            "tenant_id": row.tenant_id,
            "space_id": row.space_id,
            "document_id": row.document_id,
            "version_id": row.version_id,
            "chunk_id": row.chunk_id,
            "title": document.title if document else "",
            "text": item["text"],
            "effective_hash": item["effective_hash"],
            "curation_boost": item["boost"],
            "curation_decision_id": item["curation_decision_id"],
            "page_number": row.page_number,
            "structural_path": row.structural_path,
            "source_span": row.source_span or {},
            "start_seconds": (row.source_span or {}).get("time_start"),
            "end_seconds": (row.source_span or {}).get("time_end"),
            "element_type": element.element_type if element else None,
            "media_type": media_type,
            "scene_id": element_metadata.get("scene_id"),
            "scene_index": element_metadata.get("scene_index"),
            "frame_indexes": list(dict.fromkeys(
                ([element_metadata["frame_index"]] if element_metadata.get("frame_index") is not None else [])
                + list((element_metadata.get("evidence") or {}).get("frame_indexes") or [])
            )),
            "scope_tokens": row.scope_tokens,
        })

    release_number = (
        db.scalar(select(func.max(IndexRelease.release_number)).where(IndexRelease.space_id == space_id)) or 0
    ) + 1
    embedder = SemanticEmbedder(embedding_model.model_name, embedding_model.config or {})
    previous_collection = (
        previous.qdrant_collection
        if previous
        and previous.model_config_id == embedding_model.id
        and previous.embedding_dimension == embedder.dimension
        else None
    )
    result = SearchIndexer(
        opensearch_url=settings.opensearch_url,
        qdrant_url=settings.qdrant_url,
    ).build_release(
        tenant_id=tenant_id,
        space_id=space_id,
        release_number=release_number,
        chunks=chunks,
        embedder=embedder,
        previous_collection=previous_collection,
    )
    release = IndexRelease(
        tenant_id=tenant_id,
        space_id=space_id,
        release_number=release_number,
        opensearch_index=result["opensearch_index"],
        qdrant_collection=result["qdrant_collection"],
        graph_release_id=graph_release.id,
        model_config_id=embedding_model.id,
        embedding_dimension=result["dimension"],
        document_count=len({item["document_id"] for item in chunks}),
        chunk_count=len(chunks),
        checksums={
            "chunks": result["checksum"],
            "embedded_count": result["embedded_count"],
            "reused_vector_count": result["reused_vector_count"],
            "curated_chunks": sum(bool(item.get("curation_decision_id")) for item in chunks),
        },
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()
    return release, result


def activate_knowledge_release(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    graph_release: GraphRelease,
    index_release: IndexRelease,
    curation_batch_id: str | None = None,
) -> KnowledgeRelease:
    for current in db.scalars(
        select(KnowledgeRelease).where(
            KnowledgeRelease.tenant_id == tenant_id,
            KnowledgeRelease.space_id == space_id,
            KnowledgeRelease.status == "published",
            KnowledgeRelease.deleted_at.is_(None),
        )
    ):
        current.status = "superseded"
    release_number = (
        db.scalar(select(func.max(KnowledgeRelease.release_number)).where(KnowledgeRelease.space_id == space_id)) or 0
    ) + 1
    report = {
        "graph_release": graph_release.release_number,
        "index_release": index_release.release_number,
        "graph_valid": bool((graph_release.validation_report or {}).get("valid", True)),
        "graph_entities": graph_release.entity_count,
        "graph_facts": graph_release.fact_count,
        "index_documents": index_release.document_count,
        "index_chunks": index_release.chunk_count,
    }
    checksum = hashlib.sha256(
        json.dumps(
            {"report": report, "index_checksums": index_release.checksums or {}},
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    release = KnowledgeRelease(
        tenant_id=tenant_id,
        space_id=space_id,
        release_number=release_number,
        graph_release_id=graph_release.id,
        index_release_id=index_release.id,
        curation_batch_id=curation_batch_id,
        checksum=checksum,
        validation_report=report,
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()
    return release
