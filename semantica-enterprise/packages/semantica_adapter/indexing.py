from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import httpx

from .embedding import SemanticEmbedder


def search_point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"semantica-chunk:{chunk_id}"))


class SearchIndexer:
    def __init__(self, *, opensearch_url: str, qdrant_url: str):
        self.opensearch_url = opensearch_url.rstrip("/")
        self.qdrant_url = qdrant_url

    def build_release(
        self,
        *,
        tenant_id: str,
        space_id: str,
        release_number: int,
        chunks: list[dict[str, Any]],
        embedder: SemanticEmbedder,
        previous_collection: str | None = None,
    ) -> dict[str, Any]:
        from semantica.vector_store.qdrant_store import QdrantStore

        suffix = f"{space_id.replace('-', '')[:12]}_{release_number}"
        index_name = f"knowledge_{suffix}"
        alias_name = f"knowledge_{space_id.replace('-', '')[:12]}_active"
        collection_name = f"knowledge_{suffix}"
        mapping = {
            "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
            "mappings": {
                "properties": {
                    "text": {"type": "text"},
                    "title": {"type": "text"},
                    "tenant_id": {"type": "keyword"},
                    "space_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "version_id": {"type": "keyword"},
                    "chunk_id": {"type": "keyword"},
                    "chunk_db_id": {"type": "keyword"},
                    "page_number": {"type": "integer"},
                    "structural_path": {"type": "keyword"},
                    "scope_tokens": {"type": "keyword"},
                    "effective_hash": {"type": "keyword"},
                    "curation_boost": {"type": "float"},
                    "curation_decision_id": {"type": "keyword"},
                }
            },
        }
        with httpx.Client(timeout=60) as client:
            existing = client.head(f"{self.opensearch_url}/{index_name}")
            if existing.status_code == 200:
                removed = client.delete(f"{self.opensearch_url}/{index_name}")
                removed.raise_for_status()
            response = client.put(f"{self.opensearch_url}/{index_name}", json=mapping)
            response.raise_for_status()
            if chunks:
                lines: list[str] = []
                for item in chunks:
                    lines.append(json.dumps({"index": {"_index": index_name, "_id": item["id"]}}))
                    lines.append(json.dumps(item, ensure_ascii=False, default=str))
                bulk = client.post(
                    f"{self.opensearch_url}/_bulk?refresh=true",
                    content=("\n".join(lines) + "\n").encode("utf-8"),
                    headers={"Content-Type": "application/x-ndjson"},
                )
                bulk.raise_for_status()
                if bulk.json().get("errors"):
                    raise RuntimeError("OpenSearch 批量写入存在失败项")

        store = QdrantStore(url=self.qdrant_url)
        store.connect()
        if store.client.collection_exists(collection_name):
            store.client.delete_collection(collection_name)
        store.create_collection(collection_name, vector_size=embedder.dimension, distance="Cosine")
        payloads = {
            item["id"]: {
                "tenant_id": tenant_id,
                "space_id": space_id,
                "document_id": item["document_id"],
                "version_id": item["version_id"],
                "chunk_id": item["chunk_id"],
                "chunk_db_id": item["chunk_db_id"],
                "title": item.get("title", ""),
                "text": item["text"],
                "page_number": item.get("page_number"),
                "structural_path": item.get("structural_path", ""),
                "scope_tokens": item.get("scope_tokens", []),
                "effective_hash": item.get("effective_hash"),
                "curation_boost": float(item.get("curation_boost") or 1.0),
                "curation_decision_id": item.get("curation_decision_id"),
            }
            for item in chunks
        }
        reused_ids: set[str] = set()
        if previous_collection and store.client.collection_exists(previous_collection) and chunks:
            from qdrant_client.models import PointStruct

            requested = [item["id"] for item in chunks]
            for offset in range(0, len(requested), 256):
                records = store.client.retrieve(
                    collection_name=previous_collection,
                    ids=requested[offset : offset + 256],
                    with_vectors=True,
                    with_payload=False,
                )
                points = []
                for record in records:
                    point_id = str(record.id)
                    if record.vector is None or point_id not in payloads:
                        continue
                    reused_ids.add(point_id)
                    points.append(PointStruct(id=record.id, vector=record.vector, payload=payloads[point_id]))
                if points:
                    store.client.upsert(collection_name=collection_name, points=points, wait=True)
        changed_chunks = [item for item in chunks if item["id"] not in reused_ids]
        vectors = embedder.embed_batch([str(item["text"]) for item in changed_chunks]) if changed_chunks else []
        if chunks:
            if changed_chunks:
                store.insert_vectors(
                    vectors=list(vectors),
                    ids=[item["id"] for item in changed_chunks],
                    payloads=[payloads[item["id"]] for item in changed_chunks],
                    wait=True,
                )

        with httpx.Client(timeout=30) as client:
            aliases = client.get(f"{self.opensearch_url}/_alias/{alias_name}")
            actions: list[dict[str, Any]] = []
            if aliases.status_code == 200:
                actions.extend({"remove": {"index": name, "alias": alias_name}} for name in aliases.json())
            actions.append({"add": {"index": index_name, "alias": alias_name}})
            switched = client.post(f"{self.opensearch_url}/_aliases", json={"actions": actions})
            switched.raise_for_status()

        checksum = hashlib.sha256(
            "".join(
                item["id"]
                + item["chunk_id"]
                + item["version_id"]
                + hashlib.sha256(str(item["text"]).encode()).hexdigest()
                for item in chunks
            ).encode()
        ).hexdigest()
        return {
            "opensearch_index": index_name,
            "opensearch_alias": alias_name,
            "qdrant_collection": collection_name,
            "dimension": embedder.dimension,
            "chunk_count": len(chunks),
            "embedded_count": len(changed_chunks),
            "reused_vector_count": len(reused_ids),
            "checksum": checksum,
        }
