from __future__ import annotations

import html
import time
from collections import defaultdict
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.semantica_adapter.embedding import SemanticEmbedder
from packages.semantica_adapter.graph import query_graph_facts
from packages.semantica_adapter.indexing import search_point_id
from packages.semantica_adapter.retrieval import fuse_results, keyword_search, vector_search

from .audit import audit
from .config import get_settings
from .models import (
    CanonicalEntity,
    Chunk,
    ContentElement,
    Document,
    DocumentVersion,
    Fact,
    GraphRelease,
    InferenceEvidence,
    InferredFact,
    IndexRelease,
    MediaFrame,
    MediaProcessingRun,
    ModelConfig,
    QueryRun,
)
from .media import media_type_for
from .security import decrypt_secret


settings = get_settings()


def _active(model):
    return model.deleted_at.is_(None)


def _filter_item(item: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Apply the small, stable filter contract after every retrieval channel."""
    if not filters:
        return True
    for key in ("document_id", "version_id", "media_type", "language"):
        expected = filters.get(key)
        if expected not in (None, "") and item.get(key) != expected:
            return False
    document_ids = filters.get("document_ids")
    if document_ids and item.get("document_id") not in document_ids:
        return False
    tags = filters.get("tags")
    if tags and not set(tags).intersection(item.get("document_tags") or []):
        return False
    return True


def _canonicalize_chunk_identity(db: Session, item: dict[str, Any]) -> dict[str, Any]:
    """Resolve legacy and current search payloads to the database chunk UUID.

    Older published releases used the database UUID as the Qdrant point ID and
    did not persist ``chunk_db_id``. Current releases use a stable semantic
    point ID and carry both identifiers. Retrieval must support both forms so
    an in-place platform upgrade never produces an invalid 64-character
    citation foreign key or splits one chunk into duplicate RRF entries.
    """
    value = dict(item)
    semantic_id = str(value.get("chunk_id") or "")
    candidates = [value.get("chunk_db_id"), value.get("id")]
    chunk: Chunk | None = None
    for candidate in candidates:
        if not candidate:
            continue
        candidate_text = str(candidate)
        if len(candidate_text) <= 36:
            chunk = db.get(Chunk, candidate_text)
            if chunk is not None:
                break
    if chunk is None and semantic_id:
        criteria = [Chunk.chunk_id == semantic_id]
        if value.get("version_id"):
            criteria.append(Chunk.version_id == value["version_id"])
        if value.get("document_id"):
            criteria.append(Chunk.document_id == value["document_id"])
        chunk = db.scalar(select(Chunk).where(*criteria).limit(1))
    if chunk is None:
        return value
    value["id"] = chunk.id
    value["chunk_db_id"] = chunk.id
    value["chunk_id"] = chunk.chunk_id
    value.setdefault("version_id", chunk.version_id)
    value.setdefault("document_id", chunk.document_id)
    value.setdefault("space_id", chunk.space_id)
    value.setdefault("page_number", chunk.page_number)
    value.setdefault("structural_path", chunk.structural_path)
    value["source_span"] = chunk.source_span or value.get("source_span") or {}
    value["start_seconds"] = value["source_span"].get("time_start")
    value["end_seconds"] = value["source_span"].get("time_end")
    element = db.get(ContentElement, chunk.element_id) if chunk.element_id else None
    metadata = dict(element.element_metadata or {}) if element else {}
    version = db.get(DocumentVersion, chunk.version_id)
    media_type = media_type_for(version.filename, version.content_type) if version else None
    if element is not None:
        value["element_type"] = element.element_type
    if media_type in {"image", "audio", "video"}:
        value["media_type"] = media_type
        value["media_url"] = f"/api/v1/documents/{chunk.document_id}/media-content"
        value["scene_id"] = metadata.get("scene_id")
        value["scene_index"] = metadata.get("scene_index")
        frame_indexes = list((metadata.get("evidence") or {}).get("frame_indexes") or [])
        if metadata.get("frame_index") is not None:
            frame_indexes.append(metadata["frame_index"])
        frame_indexes = list(dict.fromkeys(int(item) for item in frame_indexes))
        if frame_indexes:
            latest_run = db.scalar(select(MediaProcessingRun).where(
                MediaProcessingRun.version_id == chunk.version_id,
                MediaProcessingRun.status.in_(["succeeded", "partial"]),
                MediaProcessingRun.deleted_at.is_(None),
            ).order_by(MediaProcessingRun.created_at.desc()).limit(1))
            if latest_run:
                frames = list(db.scalars(select(MediaFrame).where(
                    MediaFrame.run_id == latest_run.id,
                    MediaFrame.frame_index.in_(frame_indexes),
                    MediaFrame.deleted_at.is_(None),
                ).order_by(MediaFrame.frame_index)))
                value["frame_ids"] = [row.id for row in frames]
                if frames:
                    value["thumbnail_url"] = f"/api/v1/media/frames/{frames[0].id}/thumbnail"
    return value


def _is_current_chunk_item(
    db: Session,
    item: dict[str, Any],
    *,
    tenant_id: str,
    space_ids: list[str],
) -> bool:
    """Fail closed when a search backend returns a stale or foreign point.

    OpenSearch and Qdrant releases are external projections.  A document can
    be deleted, or a newer document version can become current, between a
    release being published and a query being served.  PostgreSQL remains the
    authority, so every backend hit is resolved to an active, published chunk
    owned by the requested tenant and current document version before fusion.
    """
    chunk_id = item.get("chunk_db_id") or item.get("id")
    if not chunk_id:
        return False
    chunk = db.get(Chunk, str(chunk_id))
    if (
        chunk is None
        or chunk.deleted_at is not None
        or chunk.status != "published"
        or chunk.tenant_id != tenant_id
        or chunk.space_id not in space_ids
    ):
        return False
    document = db.get(Document, chunk.document_id)
    if (
        document is None
        or document.deleted_at is not None
        or document.tenant_id != tenant_id
        or document.space_id != chunk.space_id
        or document.current_version_id != chunk.version_id
    ):
        return False
    item["document_id"] = chunk.document_id
    item["version_id"] = chunk.version_id
    item["space_id"] = chunk.space_id
    item["title"] = document.title
    item["document_tags"] = document.tags or []
    return True


def _graph_search(
    db: Session,
    query: str,
    space_ids: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not query.casefold().strip():
        return []
    releases: list[GraphRelease] = []
    for space_id in space_ids:
        release = db.scalar(
            select(GraphRelease)
            .where(
                GraphRelease.space_id == space_id,
                GraphRelease.status == "published",
            )
            .order_by(GraphRelease.release_number.desc())
            .limit(1)
        )
        if release is not None:
            releases.append(release)
    if not releases:
        return []
    ranked_facts: dict[str, float] = {}
    for release in releases:
        for item in query_graph_facts(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            graph_name=release.graph_name,
            query=query,
            limit=limit,
        ):
            ranked_facts[item["fact_id"]] = max(
                ranked_facts.get(item["fact_id"], 0), float(item["score"])
            )
    if not ranked_facts:
        return []
    facts_by_id = {
        row.id: row
        for row in db.scalars(
            select(Fact).where(
                Fact.id.in_(ranked_facts),
                Fact.space_id.in_(space_ids),
                Fact.status == "published",
                _active(Fact),
            )
        )
    }
    inferred_by_id = {
        row.id: row
        for row in db.scalars(
            select(InferredFact).where(
                InferredFact.id.in_(ranked_facts),
                InferredFact.space_id.in_(space_ids),
                InferredFact.status == "published",
                _active(InferredFact),
            )
        )
    }
    result: list[dict[str, Any]] = []
    for fact_id, graph_score in sorted(
        ranked_facts.items(), key=lambda item: item[1], reverse=True
    ):
        fact = facts_by_id.get(fact_id)
        inferred = inferred_by_id.get(fact_id)
        if fact is None and inferred is None:
            continue
        evidence = None
        if inferred is not None:
            evidence = db.scalar(
                select(InferenceEvidence)
                .where(
                    InferenceEvidence.inferred_fact_id == inferred.id,
                    InferenceEvidence.source_chunk_id.is_not(None),
                )
                .order_by(InferenceEvidence.ordinal)
            )
        source_chunk_id = fact.source_chunk_id if fact is not None else evidence.source_chunk_id if evidence else None
        chunk = db.get(Chunk, source_chunk_id) if source_chunk_id else None
        if chunk is None or chunk.space_id not in space_ids:
            continue
        document = db.get(Document, chunk.document_id)
        if document is None or document.deleted_at is not None or document.current_version_id != chunk.version_id:
            continue
        relation = fact or inferred
        subject = db.get(CanonicalEntity, relation.subject_entity_id)
        obj = db.get(CanonicalEntity, relation.object_entity_id) if relation.object_entity_id else None
        result.append(
            {
                "id": search_point_id(chunk.chunk_id),
                "chunk_db_id": chunk.id,
                "score": max(float(relation.confidence), graph_score),
                "channel": "graph",
                "space_id": chunk.space_id,
                "document_id": chunk.document_id,
                "version_id": chunk.version_id,
                "chunk_id": chunk.chunk_id,
                "title": document.title,
                "document_tags": document.tags or [],
                "text": chunk.text,
                "snippet": html.escape(
                    f"{'[规则推导] ' if inferred is not None else ''}{subject.canonical_name if subject else '—'} — {relation.predicate} → "
                    f"{obj.canonical_name if obj else relation.object_value or '—'}"
                ),
                "page_number": chunk.page_number,
                "structural_path": chunk.structural_path,
                "origin_type": "inferred" if inferred is not None else "asserted",
                "inference_id": inferred.id if inferred is not None else None,
            }
        )
        if len(result) >= limit:
            break
    return result


def _remote_rerank(
    model: ModelConfig,
    query: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Call a Cohere/Jina compatible rerank endpoint using a configured model."""
    config = model.config or {}
    if not model.base_url:
        raise ValueError("重排模型未配置 API 地址")
    endpoint = str(config.get("endpoint_path") or "/rerank")
    url = f"{model.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    key = decrypt_secret(model.api_key_encrypted)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    documents = [str(item.get("text") or item.get("snippet") or "") for item in items]
    request = {
        "model": model.model_name,
        "query": query,
        "documents": documents,
        "top_n": len(documents),
        **dict(config.get("request_parameters") or {}),
    }
    with httpx.Client(timeout=float(config.get("timeout", 30))) as client:
        response = client.post(url, headers=headers, json=request)
        response.raise_for_status()
        payload = response.json()
    rows = payload.get("results") or payload.get("data") or []
    scores: dict[int, float] = {}
    for row in rows:
        index = row.get("index")
        score = row.get("relevance_score", row.get("score"))
        if isinstance(index, int) and isinstance(score, (int, float)):
            scores[index] = float(score)
    if not scores:
        raise ValueError("重排服务未返回可识别的分数")
    ranked: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        updated = dict(item)
        updated["rerank_score"] = scores.get(index)
        ranked.append(updated)
    ranked.sort(
        key=lambda item: (
            item["rerank_score"] is not None,
            item["rerank_score"] if item["rerank_score"] is not None else item["fused_score"],
        ),
        reverse=True,
    )
    return ranked


def execute_hybrid_search(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    query: str,
    space_ids: list[str],
    top_k: int,
    use_keyword: bool,
    use_vector: bool,
    use_graph: bool,
    use_reranker: bool,
    filters: dict[str, Any] | None = None,
    audit_action: str = "knowledge.search",
) -> dict[str, Any]:
    """Execute the authoritative retrieval pipeline for UI, REST, MCP and Harness."""
    filters = filters or {}
    started = time.perf_counter()
    warnings: list[str] = []
    timings: dict[str, int] = {}
    releases: list[IndexRelease] = []
    for space_id in space_ids:
        release = db.scalar(
            select(IndexRelease)
            .where(
                IndexRelease.space_id == space_id,
                IndexRelease.status == "published",
                _active(IndexRelease),
            )
            .order_by(IndexRelease.release_number.desc())
            .limit(1)
        )
        if release:
            releases.append(release)

    channel_results: dict[str, list[dict[str, Any]]] = {
        "keyword": [],
        "vector": [],
        "graph": [],
    }
    if use_keyword and releases:
        channel_started = time.perf_counter()
        try:
            channel_results["keyword"] = keyword_search(
                settings.opensearch_url,
                [row.opensearch_index for row in releases],
                query,
                allowed_space_ids=space_ids,
                limit=top_k * 3,
            )
        except Exception as exc:
            warnings.append(f"全文检索暂不可用：{str(exc)[:160]}")
        timings["keyword_ms"] = round((time.perf_counter() - channel_started) * 1000)

    if use_vector and releases:
        channel_started = time.perf_counter()
        by_model: dict[str, list[IndexRelease]] = defaultdict(list)
        for release in releases:
            by_model[release.model_config_id].append(release)
        for model_id, model_releases in by_model.items():
            model = db.get(ModelConfig, model_id)
            if model is None or not model.enabled:
                warnings.append("部分向量索引所需模型已停用")
                continue
            try:
                embedder = SemanticEmbedder(model.model_name, model.config or {})
                channel_results["vector"].extend(
                    vector_search(
                        settings.qdrant_url,
                        [row.qdrant_collection for row in model_releases],
                        query,
                        allowed_space_ids=space_ids,
                        embedder=embedder,
                        limit=top_k * 3,
                    )
                )
            except Exception as exc:
                warnings.append(f"向量检索暂不可用：{str(exc)[:160]}")
        timings["vector_ms"] = round((time.perf_counter() - channel_started) * 1000)

    if use_graph:
        channel_started = time.perf_counter()
        try:
            channel_results["graph"] = _graph_search(db, query, space_ids, top_k * 3)
        except Exception as exc:
            warnings.append(f"图谱检索暂不可用：{str(exc)[:160]}")
        timings["graph_ms"] = round((time.perf_counter() - channel_started) * 1000)

    stale_filtered = 0
    for channel, rows in channel_results.items():
        accepted: list[dict[str, Any]] = []
        for item in rows:
            canonical = _canonicalize_chunk_identity(db, item)
            if not _is_current_chunk_item(
                db,
                canonical,
                tenant_id=tenant_id,
                space_ids=space_ids,
            ):
                stale_filtered += 1
                continue
            if _filter_item(canonical, filters):
                accepted.append(canonical)
        channel_results[channel] = accepted

    score_maps: dict[str, dict[str, float]] = {}
    rank_maps: dict[str, dict[str, int]] = {}
    for channel, rows in channel_results.items():
        score_maps[channel] = {str(row["id"]): float(row.get("score") or 0) for row in rows}
        rank_maps[channel] = {str(row["id"]): rank for rank, row in enumerate(rows, start=1)}

    fusion_started = time.perf_counter()
    fused = fuse_results(list(channel_results.values()), top_k * 3)
    timings["fusion_ms"] = round((time.perf_counter() - fusion_started) * 1000)
    prepared: list[dict[str, Any]] = []
    for item in fused:
        point_id = str(item["id"])
        chunk_db_id = item.get("chunk_db_id")
        channels = [channel for channel in channel_results if point_id in score_maps[channel]]
        prepared.append(
            {
                **item,
                "chunk_id": chunk_db_id or item.get("chunk_id") or point_id,
                "semantic_chunk_id": item.get("chunk_id"),
                "channels": channels,
                "keyword_score": score_maps["keyword"].get(point_id),
                "vector_score": score_maps["vector"].get(point_id),
                "graph_score": score_maps["graph"].get(point_id),
                "channel_ranks": {
                    channel: rank_maps[channel][point_id]
                    for channel in channels
                },
                "fused_score": float(item.get("score") or 0),
                "rerank_score": None,
                "fragment_url": f"/api/v1/fragments/{chunk_db_id or item.get('chunk_id') or point_id}",
            }
        )

    reranked = False
    if use_reranker and prepared:
        reranker = db.scalar(
            select(ModelConfig)
            .where(
                ModelConfig.tenant_id == tenant_id,
                ModelConfig.model_kind == "reranker",
                ModelConfig.enabled.is_(True),
                ModelConfig.is_default.is_(True),
                _active(ModelConfig),
            )
            .limit(1)
        )
        if reranker:
            rerank_started = time.perf_counter()
            try:
                prepared = _remote_rerank(reranker, query, prepared)
                reranked = True
            except Exception as exc:
                warnings.append(f"重排暂不可用，已保留融合排序：{str(exc)[:160]}")
            timings["rerank_ms"] = round((time.perf_counter() - rerank_started) * 1000)
        else:
            warnings.append("未配置默认重排模型，已使用 RRF 融合排序")

    items = prepared[:top_k]
    for rank, item in enumerate(items, start=1):
        item["rank"] = rank
    total_before_dedup = sum(len(rows) for rows in channel_results.values())
    timings["total_ms"] = round((time.perf_counter() - started) * 1000)
    channel_counts = {channel: len(rows) for channel, rows in channel_results.items()}
    trace_summary = {
        "query": query,
        "space_ids": space_ids,
        "channel_counts": channel_counts,
        "before_dedup": total_before_dedup,
        "after_dedup": len(fused),
        "stale_filtered": stale_filtered,
        "rrf_applied": True,
        "reranker_applied": reranked,
        "selected_chunk_ids": [item["chunk_id"] for item in items],
        "timings": timings,
        "evidence_insufficient": not bool(items),
    }
    query_run = QueryRun(
        tenant_id=tenant_id,
        user_id=user_id,
        query=query,
        space_ids=space_ids,
        retrieval_policy={
            "top_k": top_k,
            "use_keyword": use_keyword,
            "use_vector": use_vector,
            "use_graph": use_graph,
            "use_reranker": use_reranker,
            "filters": filters,
        },
        result_count=len(items),
        results=items,
        metrics={**trace_summary, "warnings": warnings},
    )
    db.add(query_run)
    audit(
        db,
        tenant_id,
        user_id,
        audit_action,
        "query",
        query_run.id,
        {"spaces": space_ids, "result_count": len(items)},
    )
    db.flush()
    return {
        "query_id": query_run.id,
        "normalized_query": query.strip(),
        "items": items,
        "channel_counts": channel_counts,
        "channels": channel_counts,
        "warnings": warnings,
        "trace_summary": trace_summary,
    }
