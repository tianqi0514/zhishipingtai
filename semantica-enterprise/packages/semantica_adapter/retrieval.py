from __future__ import annotations

import html
from typing import Any

import httpx

from .embedding import SemanticEmbedder


def keyword_search(
    opensearch_url: str,
    index_names: list[str],
    query: str,
    *,
    allowed_space_ids: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not index_names or not allowed_space_ids:
        return []
    body = {
        "size": limit,
        "query": {"function_score": {
            "query": {"bool": {
                    "must": [{"multi_match": {"query": query, "fields": ["text^2", "title"]}}],
                    "filter": [{"terms": {"space_id": allowed_space_ids}}],
            }},
            "field_value_factor": {"field": "curation_boost", "factor": 1.0, "missing": 1.0},
            "boost_mode": "multiply",
        }},
        "highlight": {"fields": {"text": {"fragment_size": 240, "number_of_fragments": 1}}},
    }
    with httpx.Client(timeout=20) as client:
        response = client.post(f"{opensearch_url.rstrip('/')}/{','.join(index_names)}/_search", json=body)
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
    return [
        {
            "id": str(hit["_id"]),
            "score": float(hit.get("_score") or 0),
            "channel": "keyword",
            **hit.get("_source", {}),
            "snippet": html.escape(" … ".join(hit.get("highlight", {}).get("text", [])) or hit.get("_source", {}).get("text", "")[:240]),
        }
        for hit in hits
    ]


def vector_search(
    qdrant_url: str,
    collections: list[str],
    query: str,
    *,
    allowed_space_ids: list[str],
    embedder: SemanticEmbedder,
    limit: int,
) -> list[dict[str, Any]]:
    from semantica.vector_store.qdrant_store import QdrantStore

    vector = embedder.embed_query(query)
    result: list[dict[str, Any]] = []
    for collection in collections:
        store = QdrantStore(url=qdrant_url)
        store.connect()
        store.get_collection(collection)
        if hasattr(store.client, "search"):
            found = store.search_vectors(
                vector,
                limit=min(200, max(limit, limit * 3)),
                filter={"space_id": allowed_space_ids},
            )
        else:
            from qdrant_client.models import FieldCondition, Filter, MatchAny

            response = store.client.query_points(
                collection_name=collection,
                query=vector.tolist(),
                query_filter=Filter(must=[FieldCondition(key="space_id", match=MatchAny(any=allowed_space_ids))]),
                limit=min(200, max(limit, limit * 3)),
                with_payload=True,
            )
            found = [
                {"id": str(point.id), "score": float(point.score), "metadata": point.payload or {}}
                for point in response.points
            ]
        for item in found:
            metadata = item.get("metadata") or {}
            if metadata.get("space_id") not in allowed_space_ids:
                continue
            boost = max(0.1, min(5.0, float(metadata.get("curation_boost") or 1.0)))
            result.append(
                {
                    "id": str(item["id"]),
                    "score": float(item.get("score") or 0) * boost,
                    "channel": "vector",
                    **metadata,
                    "snippet": html.escape(str(metadata.get("text") or "")[:240]),
                }
            )
    return sorted(result, key=lambda item: item["score"], reverse=True)[:limit]


def fuse_results(channels: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    from semantica.vector_store.hybrid_search import SearchRanker

    populated = [items for items in channels if items]
    if not populated:
        return []
    return SearchRanker("reciprocal_rank_fusion").rank(populated)[:limit]
