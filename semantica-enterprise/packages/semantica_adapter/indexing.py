from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from .embedding import SemanticEmbedder
from .resilience import classify_external_failure, retry_transient_call


def search_point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"semantica-chunk:{chunk_id}"))


def opensearch_index_mapping() -> dict[str, Any]:
    """Return the stable mapping shared by every immutable search release.

    ``source_span`` is parser-owned provenance. Its nested shape intentionally
    varies between PDFs, spreadsheets, emails, archives and media. We retain
    the complete value in ``_source`` for citations, but do not let OpenSearch
    dynamically index its nested fields: a field such as ``headers`` may be a
    string in one parser and an object in another.
    """
    return {
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
                "source_span": {"type": "object", "enabled": False},
                "start_seconds": {"type": "float"},
                "end_seconds": {"type": "float"},
                "element_type": {"type": "keyword"},
                "media_type": {"type": "keyword"},
                "scene_id": {"type": "keyword"},
                "scene_index": {"type": "integer"},
                "frame_indexes": {"type": "integer"},
                "scope_tokens": {"type": "keyword"},
                "effective_hash": {"type": "keyword"},
                "curation_boost": {"type": "float"},
                "curation_decision_id": {"type": "keyword"},
            }
        },
    }


def summarize_bulk_errors(payload: dict[str, Any], *, limit: int = 3) -> str:
    failures: list[str] = []
    for entry in payload.get("items") or []:
        operation = next(iter(entry.values()), {})
        error = operation.get("error")
        if not error:
            continue
        reason = error.get("reason") if isinstance(error, dict) else str(error)
        failures.append(f"{operation.get('_id', 'unknown')}: {reason}")
        if len(failures) >= limit:
            break
    return "；".join(failures) or "OpenSearch 未返回失败详情"


class SearchIndexer:
    def __init__(
        self,
        *,
        opensearch_url: str,
        qdrant_url: str,
        qdrant_timeout_seconds: float = 60,
        qdrant_max_attempts: int = 3,
        retry_sleeper: Callable[[float], Any] = time.sleep,
    ):
        self.opensearch_url = opensearch_url.rstrip("/")
        self.qdrant_url = qdrant_url
        self.qdrant_timeout_seconds = max(1.0, min(float(qdrant_timeout_seconds), 300.0))
        self.qdrant_max_attempts = max(1, min(int(qdrant_max_attempts), 5))
        self.retry_sleeper = retry_sleeper

    def _qdrant_call(self, operation_name: str, operation: Callable[[], Any]) -> Any:
        """Run an idempotent Qdrant operation with bounded transient retries."""

        try:
            return retry_transient_call(
                operation,
                max_attempts=self.qdrant_max_attempts,
                sleeper=self.retry_sleeper,
            )
        except Exception as exc:
            failure = classify_external_failure(exc)
            raise RuntimeError(
                f"Qdrant {operation_name}失败（{failure.category}）：{failure.error_type}: "
                f"{failure.message}"
            ) from exc

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
        mapping = opensearch_index_mapping()
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
                bulk_payload = bulk.json()
                if bulk_payload.get("errors"):
                    raise RuntimeError(f"OpenSearch 批量写入存在失败项：{summarize_bulk_errors(bulk_payload)}")

        store = QdrantStore(url=self.qdrant_url)
        # Semantica intentionally owns the Qdrant adapter.  The platform only
        # supplies an explicit request timeout because qdrant-client's default
        # is too short while a CPU-bound embedding job is publishing in
        # parallel on a small demonstration host.
        store.connect(timeout=self.qdrant_timeout_seconds)

        def prepare_collection() -> Any:
            # The release-specific collection is safe to recreate.  If a
            # create request reached Qdrant but its response timed out, the
            # retry first removes that incomplete collection.
            if store.client.collection_exists(collection_name):
                store.client.delete_collection(collection_name)
            return store.create_collection(
                collection_name,
                vector_size=embedder.dimension,
                distance="Cosine",
            )

        self._qdrant_call("准备发布集合", prepare_collection)
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
                "source_span": item.get("source_span", {}),
                "start_seconds": item.get("start_seconds"),
                "end_seconds": item.get("end_seconds"),
                "element_type": item.get("element_type"),
                "media_type": item.get("media_type"),
                "scene_id": item.get("scene_id"),
                "scene_index": item.get("scene_index"),
                "frame_indexes": item.get("frame_indexes", []),
                "scope_tokens": item.get("scope_tokens", []),
                "effective_hash": item.get("effective_hash"),
                "curation_boost": float(item.get("curation_boost") or 1.0),
                "curation_decision_id": item.get("curation_decision_id"),
            }
            for item in chunks
        }
        reused_ids: set[str] = set()
        previous_exists = bool(
            previous_collection
            and self._qdrant_call(
                "检查上一版本集合",
                lambda: store.client.collection_exists(previous_collection),
            )
        )
        if previous_collection and previous_exists and chunks:
            from qdrant_client.models import PointStruct

            requested = [item["id"] for item in chunks]
            for offset in range(0, len(requested), 256):
                records = self._qdrant_call(
                    "读取上一版本向量",
                    lambda offset=offset: store.client.retrieve(
                        collection_name=previous_collection,
                        ids=requested[offset : offset + 256],
                        with_vectors=True,
                        with_payload=False,
                    ),
                )
                points = []
                for record in records:
                    point_id = str(record.id)
                    if record.vector is None or point_id not in payloads:
                        continue
                    reused_ids.add(point_id)
                    points.append(PointStruct(id=record.id, vector=record.vector, payload=payloads[point_id]))
                if points:
                    self._qdrant_call(
                        "复用上一版本向量",
                        lambda points=points: store.client.upsert(
                            collection_name=collection_name,
                            points=points,
                            wait=True,
                        ),
                    )
        changed_chunks = [item for item in chunks if item["id"] not in reused_ids]
        vectors = embedder.embed_batch([str(item["text"]) for item in changed_chunks]) if changed_chunks else []
        if chunks:
            if changed_chunks:
                self._qdrant_call(
                    "写入当前版本向量",
                    lambda: store.insert_vectors(
                        vectors=list(vectors),
                        ids=[item["id"] for item in changed_chunks],
                        payloads=[payloads[item["id"]] for item in changed_chunks],
                        wait=True,
                    ),
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
