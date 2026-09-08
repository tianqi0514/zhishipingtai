from __future__ import annotations

import hashlib
import heapq
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable


TRUSTED_BLOCK_TYPES = {
    "knowledge_citation",
    "verified_fact",
    "computed_metric",
    "inference_conclusion",
    "manual_assumption",
    "decision_gate",
    "alternative_plan",
    "action_task",
    "data_table",
    "geo_route",
    "risk_warning",
}

SOURCE_TYPES = {
    "official_brief",
    "policy_document",
    "database_query",
    "computation",
    "semantica_inference",
    "mcp_tool",
    "model_extraction",
    "manual_input",
    "manual_override",
    "historical",
}

FACT_STATUSES = {
    "current",
    "stale",
    "invalid",
    "unverified",
    "manual_override",
    "superseded",
}

PLAN_PROFILES = {
    "speed": {
        "name": "响应速度优先",
        "weights": {"time": 0.62, "risk": 0.18, "capacity": 0.12, "gap": 0.08},
    },
    "safety": {
        "name": "安全风险优先",
        "weights": {"time": 0.18, "risk": 0.62, "capacity": 0.12, "gap": 0.08},
    },
    "balanced": {
        "name": "综合平衡",
        "weights": {"time": 0.34, "risk": 0.34, "capacity": 0.18, "gap": 0.14},
    },
}

BUILTIN_FORMULAS = {
    "resource_gap": {"name": "资源缺口", "expression": "max(0, required - available)", "unit": None},
    "shelter_gap": {"name": "安置容量缺口", "expression": "max(0, evacuees - sum(available_capacities))", "unit": "人"},
    "water_demand": {"name": "饮水需求", "expression": "sheltered_population * liters_per_person_day", "unit": "升/日"},
    "vehicle_trips": {"name": "车辆趟次", "expression": "ceil(cargo_demand / (vehicle_count * single_vehicle_capacity))", "unit": "趟"},
    "ambulance_trips": {"name": "救护车趟次", "expression": "ceil(severe_injured / (ambulances * persons_per_trip))", "unit": "趟"},
    "medical_pressure": {"name": "医疗分流压力", "expression": "severe_injured / available_trauma_beds", "unit": None},
    "route_utility": {"name": "路线效用评分", "expression": "controlled weighted route score", "unit": "分"},
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_scenario_contract(contract: dict[str, Any]) -> dict[str, Any]:
    required = {
        "input_schema",
        "ontology_mapping",
        "chapter_template",
        "output_schema",
        "decision_gates",
        "comparison_dimensions",
        "config",
    }
    missing = sorted(required - set(contract))
    if missing:
        raise ValueError(f"场景包缺少字段：{', '.join(missing)}")
    input_schema = contract.get("input_schema")
    if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
        raise ValueError("input_schema 必须是 object 类型的 JSON Schema")
    if not isinstance(input_schema.get("properties"), dict):
        raise ValueError("input_schema.properties 必须是对象")
    declared = set(input_schema["properties"])
    unknown_required = sorted(set(input_schema.get("required") or []) - declared)
    if unknown_required:
        raise ValueError(f"必填字段未在 properties 中声明：{', '.join(unknown_required)}")
    config = contract.get("config") or {}
    minimum = int(config.get("minimum_plan_count", 2))
    default = int(config.get("default_plan_count", 3))
    if minimum < 2 or default < minimum or default > 5:
        raise ValueError("方案数量必须满足 2 <= minimum_plan_count <= default_plan_count <= 5")
    gates = contract.get("decision_gates")
    if not isinstance(gates, list) or not gates:
        raise ValueError("场景包至少需要一个人工确认节点")
    gate_keys = [str(item.get("key") or "") for item in gates if isinstance(item, dict)]
    if len(gate_keys) != len(set(gate_keys)) or any(not key for key in gate_keys):
        raise ValueError("人工确认节点 key 必须非空且唯一")
    chapters = (contract.get("chapter_template") or {}).get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("场景包必须声明至少一个文稿章节")
    return contract


def validate_scenario_input(schema: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic validation findings for the supported JSON Schema subset."""
    findings: list[dict[str, Any]] = []
    properties = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in values or values[key] in (None, ""):
            findings.append({"code": "required", "field": key, "message": "缺少必填事实"})
    for key, value in values.items():
        spec = properties.get(key)
        if spec is None:
            findings.append({"code": "unknown", "field": key, "message": "字段不属于当前场景包"})
            continue
        expected = spec.get("type")
        matches = (
            expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool)
        ) or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool)) or (
            expected == "string" and isinstance(value, str)
        ) or (expected == "boolean" and isinstance(value, bool)) or (
            expected == "array" and isinstance(value, list)
        ) or (expected == "object" and isinstance(value, dict))
        if expected and not matches:
            findings.append({"code": "type", "field": key, "message": f"字段类型应为 {expected}"})
            continue
        if isinstance(value, (int, float)):
            if "minimum" in spec and value < spec["minimum"]:
                findings.append({"code": "minimum", "field": key, "message": "数值低于允许范围"})
            if "maximum" in spec and value > spec["maximum"]:
                findings.append({"code": "maximum", "field": key, "message": "数值超过允许范围"})
        if spec.get("enum") and value not in spec["enum"]:
            findings.append({"code": "enum", "field": key, "message": "值不在允许选项中"})
    return findings


def _number(inputs: dict[str, Any], name: str) -> Decimal:
    value = inputs.get(name)
    if value is None or isinstance(value, bool):
        raise ValueError(f"缺少数值输入：{name}")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"输入 {name} 不是有效数值") from exc


def _round(value: Decimal, config: dict[str, Any]) -> Decimal | int:
    digits = int(config.get("digits", 0))
    mode = str(config.get("mode") or "half_up")
    if mode == "ceil":
        factor = Decimal(10) ** digits
        return math.ceil(value * factor) / factor
    quantizer = Decimal(1).scaleb(-digits)
    rounded = value.quantize(quantizer, rounding=ROUND_HALF_UP)
    return int(rounded) if digits == 0 else rounded


def execute_formula(
    operation: str,
    inputs: dict[str, Any],
    *,
    parameters: dict[str, Any] | None = None,
    rounding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a whitelisted deterministic formula; expressions are never evaluated."""
    params = parameters or {}
    if operation == "resource_gap":
        result = max(Decimal(0), _number(inputs, "required") - _number(inputs, "available"))
    elif operation == "shelter_gap":
        capacities = inputs.get("available_capacities")
        if not isinstance(capacities, list):
            raise ValueError("available_capacities 必须是数组")
        result = max(Decimal(0), _number(inputs, "evacuees") - sum(Decimal(str(item)) for item in capacities))
    elif operation == "water_demand":
        liters = Decimal(str(params.get("liters_per_person_day", 15)))
        result = _number(inputs, "sheltered_population") * liters
    elif operation == "vehicle_trips":
        demand = _number(inputs, "cargo_demand")
        capacity = _number(inputs, "vehicle_count") * _number(inputs, "single_vehicle_capacity")
        if capacity <= 0:
            raise ValueError("车辆数量和单车容量必须大于 0")
        result = Decimal(math.ceil(demand / capacity))
    elif operation == "ambulance_trips":
        capacity = _number(inputs, "ambulances") * Decimal(str(params.get("persons_per_trip", 2)))
        if capacity <= 0:
            raise ValueError("救护车运力必须大于 0")
        result = Decimal(math.ceil(_number(inputs, "severe_injured") / capacity))
    elif operation == "medical_pressure":
        beds = _number(inputs, "available_trauma_beds")
        if beds <= 0:
            raise ValueError("可用创伤床位必须大于 0")
        result = _number(inputs, "severe_injured") / beds
    elif operation == "route_utility":
        result = (
            Decimal(100)
            - Decimal(str(params.get("time_weight", 0.5))) * _number(inputs, "minutes")
            - Decimal(str(params.get("road_risk_weight", 8))) * _number(inputs, "road_risk")
            + Decimal(str(params.get("capacity_weight", 6))) * _number(inputs, "capacity")
            - Decimal(str(params.get("slope_risk_weight", 8))) * _number(inputs, "slope_risk")
        )
    else:
        raise ValueError(f"不支持的确定性公式：{operation}")
    rounded = _round(result, rounding or {})
    return {
        "operation": operation,
        "value": float(rounded) if isinstance(rounded, Decimal) else rounded,
        "inputs": inputs,
        "parameters": params,
        "rounding": rounding or {"mode": "half_up", "digits": 0},
    }


def shortest_path(
    graph: dict[str, list[dict[str, Any]]],
    start: str,
    target: str,
    *,
    time_weight: float,
    risk_weight: float,
) -> dict[str, Any]:
    """Dijkstra path using time and risk from live input instead of canned routes."""
    queue: list[tuple[float, str, list[str], float, float]] = [(0.0, start, [start], 0.0, 0.0)]
    best: dict[str, float] = {}
    while queue:
        score, node, path, minutes, risk = heapq.heappop(queue)
        if node in best and best[node] <= score:
            continue
        best[node] = score
        if node == target:
            return {"path": path, "score": round(score, 4), "minutes": minutes, "risk": risk}
        for edge in graph.get(node, []):
            if edge.get("available") is False:
                continue
            nxt = str(edge.get("to") or "")
            if not nxt:
                continue
            edge_minutes = float(edge.get("minutes") or 0)
            edge_risk = float(edge.get("risk") or 0)
            edge_score = time_weight * edge_minutes + risk_weight * edge_risk
            heapq.heappush(
                queue,
                (score + edge_score, nxt, [*path, nxt], minutes + edge_minutes, risk + edge_risk),
            )
    raise ValueError(f"从 {start} 到 {target} 没有可用路线")


def _route_candidates(
    graph: dict[str, list[dict[str, Any]]],
    start: str,
    target: str,
    *,
    max_hops: int = 12,
    max_candidates: int = 200,
) -> list[dict[str, Any]]:
    """Enumerate bounded simple paths for explainable alternative planning.

    A single shortest path cannot produce genuinely different alternatives.
    The graph is small and project-scoped, so a bounded DFS gives the planner
    auditable candidates without accepting executable expressions or canned
    route answers.
    """
    candidates: list[dict[str, Any]] = []
    stack: list[tuple[str, list[str], float, float]] = [(start, [start], 0.0, 0.0)]
    while stack and len(candidates) < max_candidates:
        node, path, minutes, risk = stack.pop()
        if node == target:
            candidates.append({"path": path, "minutes": minutes, "risk": risk})
            continue
        if len(path) - 1 >= max_hops:
            continue
        for edge in reversed(graph.get(node, [])):
            if edge.get("available") is False:
                continue
            nxt = str(edge.get("to") or "")
            if not nxt or nxt in path:
                continue
            edge_minutes = float(edge.get("minutes") or 0)
            edge_risk = float(edge.get("risk") or 0)
            if edge_minutes < 0 or edge_risk < 0:
                raise ValueError("路线时间和风险不能为负数")
            stack.append((nxt, [*path, nxt], minutes + edge_minutes, risk + edge_risk))
    if not candidates:
        raise ValueError(f"从 {start} 到 {target} 没有可用路线")
    return candidates


def generate_alternative_plans(inputs: dict[str, Any], count: int = 3) -> list[dict[str, Any]]:
    if count < 2 or count > 3:
        raise ValueError("当前生产策略支持生成 2 或 3 套方案")
    graph = inputs.get("route_graph")
    start = str(inputs.get("route_start") or "")
    target = str(inputs.get("route_target") or "")
    if not isinstance(graph, dict) or not start or not target:
        raise ValueError("生成方案需要 route_graph、route_start 和 route_target")
    profiles = ["speed", "safety", "balanced"][:count]
    candidates = _route_candidates(graph, start, target)
    unique_paths = {tuple(item["path"]) for item in candidates}
    if len(unique_paths) < count:
        raise ValueError(f"当前路线网络只有 {len(unique_paths)} 条可用路径，无法生成 {count} 套真实差异方案")
    results: list[dict[str, Any]] = []
    used_paths: set[tuple[str, ...]] = set()
    for key in profiles:
        profile = PLAN_PROFILES[key]
        weights = profile["weights"]
        ranked = sorted(
            candidates,
            key=lambda item: (
                float(weights["time"]) * float(item["minutes"])
                + float(weights["risk"]) * float(item["risk"]),
                float(item["minutes"]),
                float(item["risk"]),
                tuple(item["path"]),
            ),
        )
        route = next(item for item in ranked if tuple(item["path"]) not in used_paths)
        used_paths.add(tuple(route["path"]))
        route = {
            **route,
            "score": round(
                float(weights["time"]) * float(route["minutes"])
                + float(weights["risk"]) * float(route["risk"]),
                4,
            ),
        }
        resource_rows = []
        unresolved = []
        for resource in inputs.get("resources") or []:
            required = Decimal(str(resource.get("required") or 0))
            available = Decimal(str(resource.get("available") or 0))
            gap = max(Decimal(0), required - available)
            row = {
                "resource": str(resource.get("name") or ""),
                "unit": str(resource.get("unit") or ""),
                "required": float(required),
                "available": float(available),
                "gap": float(gap),
            }
            resource_rows.append(row)
            if gap:
                unresolved.append(row)
        results.append(
            {
                "plan_key": key,
                "name": profile["name"],
                "objective": key,
                "weights": weights,
                "route": route,
                "resource_allocation": resource_rows,
                "unresolved_gaps": unresolved,
                "algorithm": {
                    "name": "bounded-multi-objective-simple-path",
                    "version": "1.0.0",
                    "candidate_count": len(unique_paths),
                },
                "input_fingerprint": content_hash(inputs),
            }
        )
    return results


def evaluate_earthquake_criteria(facts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate numeric thresholds before Datalog; no numeric comparison is faked in rules."""
    magnitude = _number({"magnitude": (facts.get("magnitude") or {}).get("number")}, "magnitude")
    density = _number(
        {"population_density": (facts.get("population_density") or {}).get("number")},
        "population_density",
    )
    return [
        {
            "fact_key": "criterion_major_magnitude",
            "label": "重大震级判据",
            "value": {"boolean": Decimal("6.0") <= magnitude < Decimal("7.0")},
            "unit": None,
            "formula": "6.0 <= magnitude < 7.0",
            "inputs": ["magnitude"],
        },
        {
            "fact_key": "criterion_high_population_density",
            "label": "高人口密度判据",
            "value": {"boolean": density > Decimal("200")},
            "unit": None,
            "formula": "population_density > 200",
            "inputs": ["population_density"],
        },
    ]


def earthquake_reasoning_payload(
    *, project_id: str, event_name: str, criteria: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    true_criteria = {str(item["fact_key"]): item for item in criteria if (item.get("value") or {}).get("boolean") is True}
    predicate_by_key = {
        "criterion_major_magnitude": "满足重大震级判据",
        "criterion_high_population_density": "满足高人口密度判据",
    }
    facts = [
        {
            "id": item.get("id") or key,
            "space_id": "writing-project",
            "subject_entity_id": project_id,
            "subject_name": event_name,
            "predicate": predicate_by_key[key],
            "object_entity_id": None,
            "object_value": "成立",
            "source_chunk_id": None,
            "confidence": 1.0,
        }
        for key, item in true_criteria.items()
    ]
    rules = [
        {
            "id": "earthquake-grade-major-v1",
            "version_id": "earthquake-grade-major-v1",
            "definition": {
                "conditions": [
                    {"predicate": "满足重大震级判据", "subject": "Event", "object": "成立"},
                    {"predicate": "满足高人口密度判据", "subject": "Event", "object": "成立"},
                ],
                "conclusion": {
                    "predicate": "灾害等级",
                    "subject": "Event",
                    "object": "重大地震灾害（Ⅱ级）",
                },
            },
            "confidence": 1.0,
        }
    ]
    return facts, rules


def walk_plate_nodes(nodes: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get("children")
        if isinstance(children, list):
            yield from walk_plate_nodes(children)


def validate_plate_content(content: list[dict[str, Any]], bindings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    block_ids: set[str] = set()
    for node in walk_plate_nodes(content):
        node_type = str(node.get("type") or "")
        block_id = str(node.get("id") or "")
        if block_id:
            if block_id in block_ids:
                issues.append({"code": "duplicate_block_id", "block_id": block_id, "message": "正文块标识重复"})
            block_ids.add(block_id)
        if node_type not in TRUSTED_BLOCK_TYPES:
            continue
        if not block_id:
            issues.append({"code": "missing_block_id", "message": "可信业务块缺少稳定标识"})
            continue
        binding = bindings.get(block_id)
        if not binding:
            issues.append({"code": "missing_binding", "block_id": block_id, "message": "可信业务块没有来源绑定"})
            continue
        if binding.get("source_type") not in SOURCE_TYPES:
            issues.append({"code": "invalid_source_type", "block_id": block_id, "message": "来源类型不受支持"})
        if binding.get("freshness_status") != "current":
            issues.append({"code": "stale_binding", "block_id": block_id, "message": "正文块依据已经变化"})
        if node_type in {"verified_fact", "computed_metric", "inference_conclusion"} and (
            binding.get("verification_status") != "verified"
        ):
            issues.append({"code": "unverified_binding", "block_id": block_id, "message": "权威内容尚未核验"})
    return issues


def affected_dependency_ids(
    changed_fact_ids: set[str],
    computation_runs: Iterable[dict[str, Any]],
    bindings: Iterable[dict[str, Any]],
) -> dict[str, list[str]]:
    affected_runs = {
        str(run["id"])
        for run in computation_runs
        if changed_fact_ids.intersection(str(item) for item in (run.get("input_fact_ids") or []))
    }
    affected_blocks = {
        str(binding["block_id"])
        for binding in bindings
        if str(binding.get("fact_id") or "") in changed_fact_ids
        or str(binding.get("computation_run_id") or "") in affected_runs
    }
    return {
        "fact_ids": sorted(changed_fact_ids),
        "computation_run_ids": sorted(affected_runs),
        "block_ids": sorted(affected_blocks),
    }
