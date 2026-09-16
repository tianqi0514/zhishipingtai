from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from packages.semantica_adapter.embedding import SemanticEmbedder
from packages.semantica_adapter.indexing import SearchIndexer, search_point_id

from .config import get_settings
from .curation import effective_chunk_payloads
from .media import media_type_for
from .knowledge_processing import version_in_fulltext_projection, version_in_vector_projection
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
    graph_release: GraphRelease | None,
    embedding_model: ModelConfig | None = None,
    include_pending_version_ids: set[str] | None = None,
    publish_fulltext: bool = True,
    publish_vector: bool = True,
) -> tuple[IndexRelease, dict]:
    """Build one immutable search snapshot from the effective curated chunks."""
    # Index and KnowledgeRelease numbers share one serialized publication
    # boundary. Without this lock, parallel document jobs may delete/recreate
    # the same OpenSearch index and Qdrant collection while another worker is
    # still publishing it.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"knowledge-release:{tenant_id}:{space_id}"},
        )
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
    if not publish_fulltext and not publish_vector:
        raise ValueError("索引发布至少需要选择全文或向量通道")
    if publish_vector:
        if embedding_model is None and previous and previous.model_config_id:
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
    fulltext_chunks: list[dict] = []
    vector_chunks: list[dict] = []
    for item in effective:
        row = item["row"]
        document = db.get(Document, row.document_id)
        version = db.get(DocumentVersion, row.version_id)
        if version is None:
            continue
        element = db.get(ContentElement, row.element_id) if row.element_id else None
        element_metadata = dict(element.element_metadata or {}) if element else {}
        media_type = media_type_for(version.filename, version.content_type) if version else None
        point_key = row.chunk_id if item["effective_hash"] == row.content_hash else f"{row.chunk_id}:{item['effective_hash']}"
        payload = {
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
        }
        pending = version.id in (include_pending_version_ids or set())
        if (pending and publish_fulltext) or version_in_fulltext_projection(version.parse_summary):
            fulltext_chunks.append(payload)
        if (pending and publish_vector) or version_in_vector_projection(version.parse_summary):
            vector_chunks.append(payload)

    release_number = (
        db.scalar(select(func.max(IndexRelease.release_number)).where(IndexRelease.space_id == space_id)) or 0
    ) + 1
    embedder = (
        SemanticEmbedder(embedding_model.model_name, embedding_model.config or {})
        if publish_vector and embedding_model else None
    )
    previous_collection = (
        previous.qdrant_collection
        if previous
        and embedding_model is not None
        and previous.model_config_id == embedding_model.id
        and embedder is not None
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
        fulltext_chunks=fulltext_chunks,
        vector_chunks=vector_chunks,
        embedder=embedder,
        publish_fulltext=publish_fulltext,
        publish_vector=publish_vector,
        previous_index=previous.opensearch_index if previous else None,
        previous_collection=previous_collection,
    )
    release = IndexRelease(
        tenant_id=tenant_id,
        space_id=space_id,
        release_number=release_number,
        opensearch_index=result["opensearch_index"],
        qdrant_collection=result["qdrant_collection"],
        graph_release_id=graph_release.id if graph_release else None,
        model_config_id=(embedding_model.id if publish_vector and embedding_model else previous.model_config_id if previous else None),
        embedding_dimension=(result["dimension"] or (previous.embedding_dimension if previous else None)),
        document_count=len({item["document_id"] for item in [*fulltext_chunks, *vector_chunks]}),
        chunk_count=result["chunk_count"],
        checksums={
            "chunks": result["checksum"],
            "fulltext_chunk_count": result["fulltext_chunk_count"],
            "vector_chunk_count": result["vector_chunk_count"],
            "embedded_count": result["embedded_count"],
            "reused_vector_count": result["reused_vector_count"],
            "curated_chunks": sum(
                bool(item.get("curation_decision_id"))
                for item in {item["id"]: item for item in [*fulltext_chunks, *vector_chunks]}.values()
            ),
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
    graph_release: GraphRelease | None,
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
        "graph_release": graph_release.release_number if graph_release else None,
        "index_release": index_release.release_number,
        "graph_valid": bool((graph_release.validation_report or {}).get("valid", True)) if graph_release else None,
        "graph_entities": graph_release.entity_count if graph_release else 0,
        "graph_facts": graph_release.fact_count if graph_release else 0,
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
        graph_release_id=graph_release.id if graph_release else None,
        index_release_id=index_release.id,
        curation_batch_id=curation_batch_id,
        checksum=checksum,
        validation_report=report,
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()
    return release
