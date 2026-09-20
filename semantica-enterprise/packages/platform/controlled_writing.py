"""Deterministic primitives for controlled, provenance-aware writing.

The language model may propose entities, claims and prose, but it must never
decide which historical value is inherited or which current document blocks
are changed.  This module therefore contains the small, testable decisions
used by the API orchestrator:

* turn an audited source structure into a value-free writing skeleton;
* align skeleton slots to current project facts without inheriting old values;
* classify editor operations before an optional model-assisted review; and
* calculate a bounded, explainable dependency closure.

The functions intentionally know nothing about SQLAlchemy or HTTP.  Keeping
them pure lets API, worker and DeepSeek Work plugin flows share one contract.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
import hashlib
import json
import re
from typing import Any, Iterable


SLOT_PATTERN = re.compile(r"\{\{node:([a-zA-Z][a-zA-Z0-9_.-]{0,159})\}\}")
NUMBER_PATTERN = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])")

PROPAGATION_ACTIONS = {
    "DERIVES_FROM": "recompute",
    "DEPENDS_ON": "rejudge",
    "SUPPORTS": "restate_claim",
    "CAUSES": "rewrite_argument",
    "CONTRADICTS": "block",
    "ASSUMES": "revalidate",
    "RESTATES": "sync_text",
    "COMPARES_WITH": "recheck_choice",
    "REFERENCES": "recheck_validity",
    "CONTEXT_OF": "relayout",
}

EDITOR_RELATIONS = {
    "RESTATES", "REFERENCES", "DERIVES_FROM", "CONTRADICTS", "ASSUMES",
    "SUPPORTS", "CAUSES", "CONTEXT_OF", "NEW", "UNBOUND",
}


def stable_checksum(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _display_value(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("number", value.get("value"))
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        decimal = Decimal(str(value))
        return format(decimal.normalize(), "f")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def build_value_free_skeleton(
    text: str,
    slots: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Replace only explicitly linked historical values with stable slots.

    A blind number regex would erase dates, numbering and units without a way
    to bind them.  Callers must therefore pass audited slots containing a key
    and historical value.  The function fails if an audited value remains in
    the output, preventing an old project value from leaking to the agent.
    """
    skeleton = str(text)
    replacements: list[dict[str, Any]] = []
    for item in slots:
        key = str(item.get("key") or "").strip()
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,159}", key):
            raise ValueError(f"invalid skeleton slot: {key or '<empty>'}")
        display = _display_value(item.get("historical_value"))
        if display is None:
            raise ValueError(f"slot {key} is missing a historical value")
        occurrence = str(item.get("source_text") or display)
        if occurrence not in skeleton:
            raise ValueError(f"slot {key} does not occur in source text")
        marker = f"{{{{node:{key}}}}}"
        count = skeleton.count(occurrence)
        skeleton = skeleton.replace(occurrence, marker)
        replacements.append({
            "key": key,
            "marker": marker,
            "occurrences": count,
            "unit": item.get("unit"),
            "source_id": item.get("source_id"),
        })
        if occurrence in skeleton:
            raise ValueError(f"historical value for {key} remains in skeleton")
    return {
        "text": skeleton,
        "slots": replacements,
        "slot_keys": sorted(set(SLOT_PATTERN.findall(skeleton))),
        "checksum": stable_checksum({"text": skeleton, "slots": replacements}),
    }


def skeletonize_untrusted_sample(text: str, *, source_id: str) -> dict[str, Any]:
    """Conservatively remove every numeric literal from an ungoverned sample.

    Once governed metric bindings exist callers should prefer
    :func:`build_value_free_skeleton`.  This fallback is deliberately strict:
    an unclassified date/page number may become a missing slot, but a historical
    project value can never be offered to the writing agent as current data.
    """
    matches = list(NUMBER_PATTERN.finditer(str(text)))
    if not matches:
        return {
            "text": str(text), "slots": [], "slot_keys": [],
            "checksum": stable_checksum({"text": str(text), "slots": []}),
        }
    output: list[str] = []
    slots: list[dict[str, Any]] = []
    cursor = 0
    for ordinal, match in enumerate(matches, 1):
        key = f"sample_{stable_checksum(source_id)[:10]}_{ordinal}"
        marker = f"{{{{node:{key}}}}}"
        output.extend([str(text)[cursor:match.start()], marker])
        cursor = match.end()
        slots.append({
            "key": key,
            "marker": marker,
            "source_id": source_id,
            "source_offset": [match.start(), match.end()],
            "classification": "unclassified_historical_literal",
            "requires_alignment": True,
        })
    output.append(str(text)[cursor:])
    skeleton = "".join(output)
    return {
        "text": skeleton,
        "slots": slots,
        "slot_keys": [item["key"] for item in slots],
        "checksum": stable_checksum({"text": skeleton, "slots": slots}),
    }


def corpus_artifact_manifest(
    *,
    package_id: str,
    version: int,
    source_ids: list[str],
    outline: list[dict[str, Any]],
    skeletons: list[dict[str, Any]],
    graph_release_id: str | None,
    created_at: str,
) -> dict[str, Any]:
    """Create one manifest mapping spec artifacts to authoritative stores."""
    artifacts = {
        "manifest": "postgres:writing_corpus_package_versions.manifest",
        "meta": "postgres:writing_corpus_packages",
        "provenance": "postgres:writing_evidence",
        "outline": "postgres:writing_corpus_package_versions.outline",
        "normalized_source": "minio:source-document-versions",
        "offset_map": "postgres:content_elements.locator",
        "assets": "minio:document-assets",
        "chunks": "postgres:chunks",
        "skeletons": "postgres:writing_corpus_package_versions.skeletons",
        "graph_nodes": "postgres:writing_graph_release_items",
        "rules": "postgres:analysis_rule_versions",
        "relations": "postgres:writing_relations",
        "claims": "postgres:writing_claims",
        "invariants": "postgres:scenario_package_versions.review_rules",
        "evidence": "postgres:writing_evidence",
        "derivation": "postgres:writing_reasoning_runs",
        "decisions": "postgres:writing_governance_actions",
        "conflicts": "postgres:writing_fact_conflicts",
        "gaps": "postgres:writing_inheritance_alignments.todo",
        "gate_report": "postgres:writing_generation_runs.quality_report",
        "style_profile": "postgres:writing_corpus_package_versions.style_profile",
        "section_templates": "postgres:writing_corpus_package_versions.outline",
        "node_index": "falkordb:writing-graph-release",
        "term_index": "opensearch:released-writing-corpus",
        "signature": "postgres:writing_corpus_package_versions.checksum",
    }
    payload = {
        "package_id": package_id,
        "version": version,
        "created_at": created_at,
        "source_ids": sorted(set(source_ids)),
        "graph_release_id": graph_release_id,
        "outline_count": len(outline),
        "skeleton_count": len(skeletons),
        "artifact_mapping": artifacts,
        "authority_principle": "one-current-authority-versioned-projections",
    }
    payload["checksum"] = stable_checksum(payload)
    return payload


UNIT_DIMENSIONS = {
    "人": "count", "个": "count", "张": "count", "顶": "count", "辆": "count",
    "元": "currency", "万元": "currency", "亿元": "currency",
    "平方米": "area", "㎡": "area", "m²": "area",
    "%": "ratio", "百分比": "ratio", "": "dimensionless",
}


def units_compatible(expected: str | None, actual: str | None) -> bool:
    left, right = str(expected or "").strip(), str(actual or "").strip()
    if left == right:
        return True
    return UNIT_DIMENSIONS.get(left) is not None and UNIT_DIMENSIONS.get(left) == UNIT_DIMENSIONS.get(right)


def _normalized_term(value: str) -> str:
    return re.sub(r"[\s_\-（）()：:]", "", value).casefold()


def align_inheritance(
    expected_nodes: Iterable[dict[str, Any]],
    current_facts: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Align structure to current facts; historical values are never copied."""
    facts = list(current_facts)
    by_key = {str(item.get("fact_key") or ""): item for item in facts}
    by_term: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        for term in {str(fact.get("fact_key") or ""), str(fact.get("label") or "")}:
            if term:
                by_term.setdefault(_normalized_term(term), []).append(fact)
    alignments: list[dict[str, Any]] = []
    todo: list[dict[str, Any]] = []
    for node in expected_nodes:
        key = str(node.get("key") or "").strip()
        label = str(node.get("label") or key).strip()
        expected_unit = node.get("unit")
        match: dict[str, Any] | None = by_key.get(key)
        method = "field_id" if match else None
        candidates: list[dict[str, Any]] = []
        if match is None:
            candidates = by_term.get(_normalized_term(label), []) + by_term.get(_normalized_term(key), [])
            unique = {str(item.get("id") or item.get("fact_key")): item for item in candidates}
            candidates = list(unique.values())
            if len(candidates) == 1:
                match, method = candidates[0], "term_normalization"
        if match is None:
            # Bounded L3 fallback.  This is only a candidate: unlike exact and
            # normalized-term matches it always requires explicit human
            # confirmation before it can participate in an applied alignment.
            ranked = sorted(
                [
                    (
                        SequenceMatcher(
                            None,
                            _normalized_term(label),
                            _normalized_term(str(fact.get("label") or fact.get("fact_key") or "")),
                        ).ratio(),
                        fact,
                    )
                    for fact in facts
                    if units_compatible(expected_unit, fact.get("unit"))
                ],
                key=lambda item: item[0],
                reverse=True,
            )
            if ranked and ranked[0][0] >= 0.72 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.08):
                match, method = ranked[0][1], "semantic_candidate"
                semantic_score = round(ranked[0][0], 4)
            else:
                semantic_score = None
        else:
            semantic_score = None
        compatible = bool(match and units_compatible(expected_unit, match.get("unit")))
        verified = bool(match and str(match.get("verification_status") or "verified") == "verified")
        if match and not verified:
            decision = "current_fact_requires_confirmation"
            status = "needs_confirmation"
        elif match and not compatible:
            decision = "convert_or_reconfirm"
            status = "needs_confirmation"
        elif match and method == "semantic_candidate":
            decision = "semantic_match_requires_confirmation"
            status = "needs_confirmation"
        elif match:
            decision = "use_current_project_value"
            status = "ready"
        elif node.get("not_applicable"):
            decision = "not_applicable"
            status = "prune"
        elif node.get("formula") and all(str(dep) in by_key for dep in node.get("dependencies") or []):
            decision = "recompute"
            status = "ready"
        else:
            decision = "missing_current_fact"
            status = "blocking" if node.get("required", True) else "todo"
        item = {
            "node_key": key,
            "label": label,
            "expected_unit": expected_unit,
            "matched_fact_id": match.get("id") if match else None,
            "matched_fact_key": match.get("fact_key") if match else None,
            "current_value": match.get("value") if match else None,
            "current_unit": match.get("unit") if match else None,
            "match_method": method,
            "semantic_score": semantic_score,
            "unit_compatible": compatible,
            "fact_verified": verified,
            "decision": decision,
            "status": status,
            "historical_value_inherited": False,
        }
        alignments.append(item)
        if status in {"blocking", "todo", "needs_confirmation"}:
            todo.append({"node_key": key, "label": label, "reason": decision, "status": status})
    result = {
        "alignments": alignments,
        "todo": todo,
        "blocking_count": sum(item["status"] == "blocking" for item in alignments),
        "ready_count": sum(item["status"] == "ready" for item in alignments),
        "historical_values_inherited": 0,
    }
    result["checksum"] = stable_checksum(result)
    return result


def classify_edit_operations(
    operations: Iterable[dict[str, Any]],
    bindings: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Classify ADD/DEL/MOD/MOVE with deterministic rules first.

    Ambiguous ADD or prose-only MOD operations are explicitly marked for model
    assistance or human confirmation; the model is never allowed to enlarge
    the deterministic propagation set.
    """
    verdicts: list[dict[str, Any]] = []
    for operation in operations:
        kind = str(operation.get("operation") or "").upper()
        if kind not in {"ADD", "DEL", "MOD", "MOVE"}:
            raise ValueError(f"unsupported edit operation: {kind}")
        block_id = str(operation.get("block_id") or "")
        block_bindings = bindings.get(block_id, [])
        before = str(operation.get("before") or "")
        after = str(operation.get("after") or "")
        relation = "UNBOUND"
        confidence = "review"
        propagation = False
        reason = "无法通过确定性绑定判断，需要人工确认"
        if kind == "MOVE":
            relation, confidence, propagation = "CONTEXT_OF", "deterministic", True
            reason = "仅更新章节归属、编号与交叉引用"
        elif kind == "DEL":
            relation, confidence, propagation = ("REFERENCES" if block_bindings else "UNBOUND"), "deterministic", bool(block_bindings)
            reason = "删除正文不删除权威事实；检查孤儿引用和失去支撑的陈述"
        elif kind == "MOD":
            old_numbers, new_numbers = NUMBER_PATTERN.findall(before), NUMBER_PATTERN.findall(after)
            has_authority = any(item.get("binding_type") in {"project_fact", "computation_run", "inferred_fact"} for item in block_bindings)
            if old_numbers != new_numbers and has_authority:
                relation, confidence, propagation = "RESTATES", "deterministic", True
                reason = "受控值发生变化，必须转入事实变更与影响预览"
            elif before.strip() != after.strip() and block_bindings:
                relation, confidence = "RESTATES", "bounded"
                reason = "措辞变化但权威绑定未改变，不触发数值重算"
            else:
                relation, confidence = "NEW", "review"
                reason = "未绑定的普通措辞修改"
        elif kind == "ADD":
            supplied_ids = set(operation.get("binding_ids") or [])
            existing_ids = {str(item.get("binding_id")) for values in bindings.values() for item in values}
            if supplied_ids and supplied_ids <= existing_ids:
                relation, confidence, propagation = "REFERENCES", "deterministic", True
                reason = "新增内容显式引用现有权威对象"
            elif NUMBER_PATTERN.search(after):
                relation, confidence = "UNBOUND", "blocking"
                reason = "新增精确数字没有权威绑定，不能进入正式稿"
            else:
                relation, confidence = "NEW", "model_assist"
                reason = "新增分析性表达可由模型辅助分类，但须人工确认"
        verdicts.append({
            "operation": kind,
            "block_id": block_id,
            "semantic_relation": relation,
            "confidence": confidence,
            "propagation_required": propagation,
            "reason": reason,
            "binding_ids": [str(item.get("binding_id")) for item in block_bindings],
        })
    return verdicts


@dataclass(frozen=True)
class PropagationEdge:
    source: str
    target: str
    relation: str
    metadata: dict[str, Any]


def propagation_closure(
    roots: Iterable[str],
    edges: Iterable[PropagationEdge | dict[str, Any]],
    *,
    max_depth: int = 64,
    max_nodes: int = 2000,
) -> dict[str, Any]:
    """Return a bounded directed closure with every explanatory path."""
    if max_depth < 1 or max_depth > 64:
        raise ValueError("max_depth must be between 1 and 64")
    if max_nodes < 1 or max_nodes > 20_000:
        raise ValueError("max_nodes must be between 1 and 20000")
    normalized: list[PropagationEdge] = []
    for raw in edges:
        edge = raw if isinstance(raw, PropagationEdge) else PropagationEdge(
            source=str(raw.get("source") or ""),
            target=str(raw.get("target") or ""),
            relation=str(raw.get("relation") or "").upper(),
            metadata=dict(raw.get("metadata") or {}),
        )
        if not edge.source or not edge.target:
            raise ValueError("propagation edge needs source and target")
        if edge.relation not in PROPAGATION_ACTIONS:
            raise ValueError(f"unsupported propagation relation: {edge.relation}")
        normalized.append(edge)
    outgoing: dict[str, list[PropagationEdge]] = {}
    for edge in normalized:
        outgoing.setdefault(edge.source, []).append(edge)
    root_list = sorted({str(root) for root in roots if str(root)})
    queue = deque((root, 0, [root]) for root in root_list)
    best_depth = {root: 0 for root in root_list}
    impacts: dict[str, dict[str, Any]] = {}
    cycles: list[list[str]] = []
    truncated = False
    while queue:
        node, depth, path = queue.popleft()
        if depth >= max_depth:
            if outgoing.get(node):
                truncated = True
            continue
        for edge in outgoing.get(node, []):
            next_path = [*path, edge.target]
            if edge.target in path:
                cycles.append(next_path)
                continue
            next_depth = depth + 1
            existing = impacts.get(edge.target)
            candidate = {
                "node_id": edge.target,
                "direct": depth == 0,
                "depth": next_depth,
                "relation": edge.relation,
                "action": PROPAGATION_ACTIONS[edge.relation],
                "certainty": str(edge.metadata.get("certainty") or "definite"),
                "path": next_path,
                "metadata": deepcopy(edge.metadata),
            }
            # Reaching exactly the limit is not truncation: a bounded graph
            # with no additional unique target is still complete. Fail only
            # when a new impact would exceed the configured node budget.
            if existing is None and len(impacts) >= max_nodes:
                truncated = True
                queue.clear()
                break
            if existing is None or next_depth < existing["depth"]:
                impacts[edge.target] = candidate
            if next_depth < best_depth.get(edge.target, max_depth + 1):
                best_depth[edge.target] = next_depth
                queue.append((edge.target, next_depth, next_path))
    ordered = sorted(impacts.values(), key=lambda item: (item["depth"], item["node_id"]))
    result = {
        "roots": root_list,
        "impacts": ordered,
        # Keep an explicit path list for API/UI consumers that only need to
        # render the proof routes without re-projecting every impact object.
        "paths": [item["path"] for item in ordered],
        "direct_count": sum(item["direct"] for item in ordered),
        "indirect_count": sum(not item["direct"] for item in ordered),
        "cycles": cycles,
        "truncated": truncated,
        "limits": {"max_depth": max_depth, "max_nodes": max_nodes},
    }
    result["checksum"] = stable_checksum(result)
    return result


def require_complete_propagation(closure: dict[str, Any] | None) -> None:
    """Never approve a partial or legacy-unchecked dependency traversal.

    Callers may render partial results as diagnostics, but a mutation preview
    must prove that traversal finished within its limits before it is saved
    or applied. Older unchecked previews require a fresh preview.
    """
    if not isinstance(closure, dict) or closure.get("truncated") is not False:
        raise ValueError("影响范围超过安全遍历上限或尚未完整验证，不能应用，请缩小变更范围后重新预览")
