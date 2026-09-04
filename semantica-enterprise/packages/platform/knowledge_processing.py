from __future__ import annotations

from typing import Any, Literal, cast


KnowledgeProcessingMode = Literal["vector", "graph", "both"]

PROCESSING_TARGETS: dict[str, frozenset[str]] = {
    "vector": frozenset({"vector"}),
    "graph": frozenset({"graph"}),
    "both": frozenset({"vector", "graph"}),
}


def normalize_processing_mode(value: Any, *, default: str = "both") -> KnowledgeProcessingMode:
    mode = str(value or default).strip().lower()
    if mode not in PROCESSING_TARGETS:
        raise ValueError("知识加工方式仅支持 vector、graph 或 both")
    return cast(KnowledgeProcessingMode, mode)


def processing_targets(mode: str) -> frozenset[str]:
    return PROCESSING_TARGETS[normalize_processing_mode(mode)]


def processing_mode_for_targets(targets: set[str] | frozenset[str]) -> KnowledgeProcessingMode:
    normalized = {str(item) for item in targets if str(item) in {"vector", "graph"}}
    if normalized == {"vector"}:
        return "vector"
    if normalized == {"graph"}:
        return "graph"
    return "both"


def completed_processing_targets(summary: dict[str, Any] | None) -> set[str]:
    """Read the durable per-version target state while preserving legacy data.

    Releases created before target selection always built both projections.
    Treat those published versions as complete for both targets.
    """
    payload = summary or {}
    explicit = payload.get("knowledge_targets_completed")
    if isinstance(explicit, list):
        return {str(item) for item in explicit if str(item) in {"vector", "graph"}}
    if payload.get("knowledge_status") == "published":
        return set(processing_targets(payload.get("knowledge_processing_mode") or "both"))
    return set()


def version_in_vector_projection(summary: dict[str, Any] | None) -> bool:
    payload = summary or {}
    explicit = payload.get("knowledge_targets_completed")
    if isinstance(explicit, list):
        return "vector" in explicit
    # Legacy chunks predate this switch and were always published to both
    # OpenSearch and Qdrant. New versions carry a processing mode from upload
    # time and must not enter a space snapshot until vector publication has
    # actually completed.
    if "knowledge_processing_mode" in payload or payload.get("knowledge_processing_protocol") == "targets_v1":
        return False
    return True


def version_in_graph_projection(summary: dict[str, Any] | None) -> bool:
    payload = summary or {}
    explicit = payload.get("knowledge_targets_completed")
    if isinstance(explicit, list):
        return "graph" in explicit
    if "knowledge_processing_mode" in payload or payload.get("knowledge_processing_protocol") == "targets_v1":
        return False
    return True
