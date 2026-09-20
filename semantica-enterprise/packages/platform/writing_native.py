"""Deterministic validation of chapters authored by an external native DSH Agent.

This module never calls a model and never accepts model-provided verification
status. It validates the returned Plate tree and resolves binding IDs against
the immutable, permission-scoped work package issued by the API.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from .writing import content_hash
from .writing_flow import report_quality_review


NUMBER = re.compile(r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?|(?<=[\u3400-\u9fff])[-+]?\d[\d,]*(?:\.\d+)?")
NODE_FIELDS = {
    "id", "type", "children", "text", "bold", "italic", "underline",
    "strikethrough", "fontSize", "fontFamily", "lineHeight", "align",
    "indent", "listStyleType", "colSpan", "rowSpan", "colSizes",
}
NODE_TYPES = {"p", "h3", "blockquote", "table", "tr", "td", "th"}


MAX_NATIVE_EVIDENCE = 30


def prioritize_native_evidence(
    available_ids: list[str],
    *,
    requested_ids: list[str] | None = None,
    direct_dependency_ids: set[str] | None = None,
    limit: int = MAX_NATIVE_EVIDENCE,
) -> dict:
    """Build one bounded, deterministic, bindable source page.

    A chapter packet previously exposed the first N source chunks while facts
    in the same packet could point at a later chunk.  The Agent could see that
    chunk in ``source_inventory`` but was forbidden to bind it because only
    ``evidence`` is part of the submit vocabulary.  Put the direct dependency
    closure first, then fill the remaining budget with the caller's source
    selection.  If the closure itself exceeds the bound, fail closed instead
    of silently issuing an internally inconsistent packet.

    ``available_ids`` is already permission- and version-scoped by the API;
    this helper deliberately does not broaden that authority boundary.
    """
    if limit < 1:
        raise ValueError("来源分页上限必须大于零")
    ordered_available = list(dict.fromkeys(str(item) for item in available_ids))
    allowed = set(ordered_available)
    requested = set(str(item) for item in (requested_ids or []))
    direct = set(str(item) for item in (direct_dependency_ids or set())) & allowed
    if len(direct) > limit:
        raise ValueError(
            f"本章事实、计算和关系的直接来源共有 {len(direct)} 条，超过单章来源上限 {limit} 条；"
            "请用 fact_keys 或 source_chunk_ids 缩小本章工作包"
        )
    selected_scope = allowed if not requested else requested & allowed
    direct_ordered = [item for item in ordered_available if item in direct]
    supplemental = [
        item for item in ordered_available
        if item in selected_scope and item not in direct
    ]
    candidates = direct_ordered + supplemental
    returned = candidates[:limit]
    return {
        "ids": returned,
        "page": {
            "selection": "direct_dependencies_first",
            "limit": limit,
            "total_available": len(ordered_available),
            "requested_count": len(selected_scope),
            "direct_dependency_count": len(direct_ordered),
            "candidate_count": len(candidates),
            "returned_count": len(returned),
            "omitted_count": max(0, len(candidates) - len(returned)),
            "truncated": len(candidates) > len(returned),
            "direct_dependency_complete": set(direct_ordered).issubset(returned),
        },
    }


def text_leaves(node: dict, path: tuple[int, ...] = ()):
    if "text" in node:
        yield path, node
    for index, child in enumerate(node.get("children") or []):
        yield from text_leaves(child, (*path, index))


def _number(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("布尔值不是正式数值")
    try:
        result = Decimal(str(value).replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("权威输入不是有效数值") from exc
    if not result.is_finite():
        raise ValueError("权威输入不是有限数值")
    return result


def numeric_value(fact: dict) -> Any:
    value = fact.get("value") or {}
    return value.get("number", value.get("value")) if isinstance(value, dict) else value


def validate_tree(blocks: list[dict]) -> None:
    ids: set[str] = set()
    count = 0

    def walk(node: Any, depth: int):
        nonlocal count
        count += 1
        if count > 8000 or depth > 20 or not isinstance(node, dict):
            raise ValueError("章节结构过大或不合法")
        if set(node) - NODE_FIELDS:
            raise ValueError("Plate 节点包含未声明字段；状态和受控绑定由服务端分配")
        if "text" in node:
            if not isinstance(node["text"], str) or len(node["text"]) > 20000 or "children" in node:
                raise ValueError("Plate 文本叶不合法")
            return
        if node.get("type") not in NODE_TYPES or not isinstance(node.get("children"), list) or not node["children"]:
            raise ValueError("章节包含不支持的 Plate 节点")
        if not isinstance(node.get("id"), str) or not node["id"] or len(node["id"]) > 100 or node["id"] in ids:
            raise ValueError("每个 Plate 元素必须有唯一稳定 ID")
        ids.add(node["id"])
        for child in node["children"]:
            walk(child, depth + 1)

    for block in blocks:
        walk(block, 0)


def validate_native_chapter(snapshot: dict, blocks: list[dict], specs: list[dict], run_id: str):
    """Returns ordinary Plate blocks plus authoritative legacy/chunk bindings.

    Precise fact/metric numbers require explicit occurrence positions. Numeric
    policy references may instead quote a pinned source with that number.
    This checks support, not full semantic entailment; prose remains unverified
    until reviewed and can never self-assert a verified status.
    """
    from .writing_occurrences import bind_numeric_occurrences

    validate_tree(blocks)
    result = deepcopy(blocks)
    facts = {row["id"]: row for row in snapshot["facts"]}
    computations = {row["id"]: row for row in snapshot["computations"]}
    evidence = {row["id"]: row for row in snapshot["evidence"]}
    public = {row["id"]: row for row in snapshot["public_references"]}
    graph = snapshot["graph"]
    graph_facts = {row["id"]: row for row in graph.get("facts", [])}
    relations = {row["id"]: row for row in graph.get("relations", [])}
    by_id = {block["id"]: block for block in result}
    spec_by_id = {spec["block_id"]: spec for spec in specs}
    if len(spec_by_id) != len(specs) or set(spec_by_id) - set(by_id):
        raise ValueError("绑定必须对应唯一的本章顶层正文块")
    bindings = []
    for block in result:
        spec = spec_by_id.get(block["id"], {})
        for field, allowed in (("fact_ids", facts), ("evidence_ids", evidence),
                               ("computation_run_ids", computations), ("writing_fact_ids", graph_facts),
                               ("relation_ids", relations), ("public_reference_ids", public)):
            if set(spec.get(field) or []) - set(allowed):
                raise ValueError(f"{field} 包含不在本次工作包中的依据")
        bound_facts = {key: facts[key] for key in spec.get("fact_ids") or []}
        bound_runs = {key: computations[key] for key in spec.get("computation_run_ids") or []}
        ranges: dict[tuple, list[tuple[int, int]]] = {}
        occurrence_specs = []
        for index, occ in enumerate(spec.get("occurrences") or []):
            if occ["source_type"] == "fact":
                source = bound_facts.get(occ["source_id"])
                if source is None:
                    raise ValueError("数值位置引用了未绑定的 Fact")
                value = numeric_value(source)
                metadata = {"fact_id": source["id"], "fact_key": source["fact_key"], "fact_version": source["version"]}
                unit = source.get("unit") or ""
            else:
                source = bound_runs.get(occ["source_id"])
                if source is None:
                    raise ValueError("数值位置引用了未绑定的 ComputationRun")
                output = (source.get("result") or {}).get("output_fact") or {}
                value = (source.get("result") or {}).get("value")
                metadata = {"fact_key": output.get("fact_key"), "computation_run_id": source["id"]}
                unit = output.get("unit") or ""
            _number(value)
            display_unit = occ.get("display_unit") or unit
            scale = occ.get("scale")
            ranges.setdefault(tuple(occ["leaf_path"]), []).append((occ["start"], occ["end"]))
            occurrence_specs.append({"leaf_path": occ["leaf_path"], "start": occ["start"], "end": occ["end"], "binding": {
                **metadata, "occurrence_id": f"{run_id}:{block['id']}:{index}", "unit": unit,
                "value": value,
                "display_unit": display_unit, "scale": scale,
                **({"decimal_places": occ["decimal_places"]} if occ.get("decimal_places") is not None else {}), "rounding": "half_up",
                "evidence_ids": list(spec.get("evidence_ids") or []),
                "evidence_type": "source_chunk",
                "grouping": occ.get("grouping", False), "show_unit": occ.get("show_unit", False),
            }})
        # Any number matching an authoritative value must have a position
        # binding; copying a citation cannot evade future change propagation.
        authority_numbers = {_number(v) for f in facts.values() if isinstance((v := numeric_value(f)), (int, float, Decimal)) and not isinstance(v, bool)}
        authority_numbers |= {_number(r["result"]["value"]) for r in computations.values() if r.get("result", {}).get("value") is not None}
        cited_text = "\n".join(str(evidence[key].get("text") or "") for key in spec.get("evidence_ids") or [])
        cited_text += "\n" + "\n".join(str(public[key].get("excerpt") or "") for key in spec.get("public_reference_ids") or [])
        cited_numbers = {_number(match.group()) for match in NUMBER.finditer(cited_text)}
        for path, leaf in text_leaves(block):
            for match in NUMBER.finditer(leaf["text"]):
                covered = any(start <= match.start() and end >= match.end() for start, end in ranges.get(path, []))
                if not covered and (_number(match.group()) in authority_numbers or _number(match.group()) not in cited_numbers):
                    raise ValueError(f"正文块 {block['id']} 有无依据或未做精确位置绑定的数值：{match.group()}")
        if occurrence_specs:
            bound = bind_numeric_occurrences(block, occurrence_specs)
            block.clear()
            block.update(bound)
        fact_ids = list(bound_facts)
        computation_ids = list(bound_runs)
        metadata = {
            "section_key": snapshot["section"]["key"], "section_title": snapshot["section"]["title"],
            "input_fact_ids": fact_ids, "input_keys": [facts[key]["fact_key"] for key in fact_ids],
            "computation_run_ids": computation_ids,
            "metric_keys": [computations[key]["result"]["output_fact"]["fact_key"] for key in computation_ids],
            "source_chunk_ids": list(spec.get("evidence_ids") or []),
            "writing_fact_ids": list(spec.get("writing_fact_ids") or []),
            "writing_relation_ids": list(spec.get("relation_ids") or []),
            "public_reference_ids": list(spec.get("public_reference_ids") or []),
            "content_hash_algorithm": "canonical-json-v1", "authoring_runtime": "dsh_native",
            "support_status": "source_bound" if any(spec.get(key) for key in ("fact_ids", "evidence_ids", "computation_run_ids", "writing_fact_ids", "relation_ids", "public_reference_ids")) else "narrative",
        }
        bindings.append({"block_id": block["id"], "block_type": block["type"], "source_type": "model_extraction",
            "source_id": run_id, "evidence_ids": fact_ids,
            "chunk_id": next(iter(spec.get("evidence_ids") or []), None),
            "verification_status": "unverified", "freshness_status": "current",
            "content_hash": content_hash(block), "metadata": metadata})
    heading = {"id": f"native-{run_id}-heading", "type": "h2", "children": [{"text": snapshot["section"]["title"]}]}
    result.insert(0, heading)
    quality = report_quality_review(result, section_plan=[snapshot["section"]],
        bindings={b["block_id"]: b for b in bindings}, require_citations=False)
    if snapshot["section"].get("citation_required") and not any(
        spec.get("evidence_ids") or spec.get("public_reference_ids") for spec in specs
    ):
        quality["issues"].append({"code": "missing_section_evidence", "severity": "error", "message": "本章要求真实来源依据"})
        quality["ok"] = False
    quality["review_required"] = True
    quality["support_check"] = "identity_numeric_scope_not_semantic_entailment"
    return result, bindings, quality
