from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Literal, cast


KnowledgeProcessingMode = Literal["vector", "graph", "both"]

PROCESSING_TARGETS: dict[str, frozenset[str]] = {
    # Before targets_v2 the UI called the combined OpenSearch + Qdrant
    # projection "vector".  Keep that API contract while exposing both
    # projections as independent targets to new clients.
    "vector": frozenset({"fulltext", "vector"}),
    "graph": frozenset({"graph"}),
    "both": frozenset({"fulltext", "vector", "graph"}),
}

VALID_PROCESSING_TARGETS = frozenset({"fulltext", "vector", "graph", "writing_graph"})


def normalize_processing_mode(value: Any, *, default: str = "both") -> KnowledgeProcessingMode:
    mode = str(value or default).strip().lower()
    if mode not in PROCESSING_TARGETS:
        raise ValueError("知识加工方式仅支持 vector、graph 或 both")
    return cast(KnowledgeProcessingMode, mode)


def processing_targets(mode: str) -> frozenset[str]:
    return PROCESSING_TARGETS[normalize_processing_mode(mode)]


def processing_mode_for_targets(targets: set[str] | frozenset[str]) -> KnowledgeProcessingMode:
    normalized = {str(item) for item in targets if str(item) in VALID_PROCESSING_TARGETS}
    search_targets = normalized & {"fulltext", "vector"}
    if search_targets and "graph" not in normalized:
        return "vector"
    if "graph" in normalized and not search_targets:
        return "graph"
    return "both"


def normalize_processing_targets(
    value: Any,
    *,
    legacy_mode: Any = "both",
) -> frozenset[str]:
    """Return the explicit v2 processing targets.

    The browser submits a JSON array, while internal callers may use a list,
    set or comma-separated string.  Empty explicit input falls back to the
    legacy mode so old REST/MCP/CLI clients retain their behaviour.
    """

    if value is None or value == "":
        return processing_targets(normalize_processing_mode(legacy_mode))
    parsed = value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError("知识加工目标不是有效 JSON 数组") from exc
        else:
            parsed = [item.strip() for item in stripped.split(",") if item.strip()]
    if not isinstance(parsed, Iterable) or isinstance(parsed, (bytes, dict)):
        raise ValueError("知识加工目标必须是数组")
    normalized = {str(item).strip().lower() for item in parsed if str(item).strip()}
    invalid = normalized - VALID_PROCESSING_TARGETS
    if invalid:
        raise ValueError(f"不支持的知识加工目标：{'、'.join(sorted(invalid))}")
    if not normalized:
        raise ValueError("请至少选择一种知识加工目标")
    return frozenset(normalized)


def completed_processing_targets(summary: dict[str, Any] | None) -> set[str]:
    """Read the durable per-version target state while preserving legacy data.

    Releases created before target selection always built both projections.
    Treat those published versions as complete for both targets.
    """
    payload = summary or {}
    explicit = payload.get("knowledge_targets_completed")
    if isinstance(explicit, list):
        result = {str(item) for item in explicit if str(item) in VALID_PROCESSING_TARGETS}
        # targets_v1 used "vector" for the combined full-text/vector index.
        if payload.get("knowledge_processing_protocol") != "targets_v2" and "vector" in result:
            result.add("fulltext")
        return result
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


def version_in_fulltext_projection(summary: dict[str, Any] | None) -> bool:
    payload = summary or {}
    explicit = payload.get("knowledge_targets_completed")
    if isinstance(explicit, list):
        if payload.get("knowledge_processing_protocol") == "targets_v2":
            return "fulltext" in explicit
        return "vector" in explicit or "fulltext" in explicit
    if "knowledge_processing_mode" in payload or payload.get("knowledge_processing_protocol") in {
        "targets_v1", "targets_v2",
    }:
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
