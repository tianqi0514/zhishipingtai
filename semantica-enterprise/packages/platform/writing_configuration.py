from __future__ import annotations

from typing import Any


_DEFAULT_INPUT_LABELS = {
    "magnitude": "震级",
    "population_density": "人口密度",
    "rescue_required": "搜救人员需求",
    "rescue_available": "可用搜救人员",
    "trauma_beds_required": "创伤床位需求",
    "county_trauma_beds": "县域可用床位",
    "callable_trauma_beds": "可调用床位",
    "tents_required": "帐篷需求",
    "tents_available": "可用帐篷",
}

_DEFAULT_INPUT_UNITS = {
    "magnitude": "级",
    "population_density": "人/km²",
    "rescue_required": "人",
    "rescue_available": "人",
    "trauma_beds_required": "张",
    "county_trauma_beds": "张",
    "callable_trauma_beds": "张",
    "tents_required": "顶",
    "tents_available": "顶",
}


def compile_business_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    """Compile business-facing settings into the existing executable contract.

    The runtime continues to consume the stable ScenarioPackageVersion shape;
    this adapter keeps JSON Schema and chapter implementation details out of
    the ordinary configuration experience.
    """

    properties: dict[str, Any] = {}
    required: list[str] = []
    for item in payload["inputs"]:
        spec: dict[str, Any] = {
            "type": item["data_type"],
            "title": item["label"],
            "confirmation_required": bool(item.get("confirmation_required", True)),
        }
        for key in ("unit", "minimum", "maximum", "source_guidance"):
            value = item.get(key)
            if value not in (None, ""):
                spec[key] = value
        properties[item["key"]] = spec
        if item.get("required", True):
            required.append(item["key"])

    chapters = [
        {
            "key": item["key"],
            "title": item["title"],
            "instruction": item["purpose"],
            "generation_mode": item.get("generation_mode", "agent"),
            "required_inputs": list(item.get("required_inputs") or []),
            "toolbox_outputs": list(item.get("toolbox_outputs") or []),
            "citation_required": bool(item.get("citation_required", True)),
            "knowledge": dict(item.get("knowledge") or {}),
        }
        for item in payload["sections"]
    ]
    toolbox = dict(payload.get("toolbox") or {})
    writing_policy = dict(payload.get("writing_policy") or {})
    output = dict(payload.get("output") or {})
    return {
        "input_schema": {"type": "object", "required": required, "properties": properties},
        "ontology_mapping": {},
        "rule_set_ids": list(toolbox.get("rule_set_ids") or []) if toolbox.get("reasoning_enabled", True) else [],
        "formula_ids": list(toolbox.get("formula_ids") or []) if toolbox.get("calculation_enabled", True) else [],
        "tool_ids": [],
        "chapter_template": {"chapters": chapters},
        "output_schema": {
            "type": "object",
            "required": ["title", "chapters", "evidence_manifest"],
            "title_pattern": output.get("title_pattern", "{project_name}"),
            "allowed_formats": list(output.get("allowed_formats") or ["docx", "pdf"]),
        },
        "review_rules": {
            **writing_policy,
            "citation_required_sections": [item["key"] for item in chapters if item["citation_required"]],
        },
        "decision_gates": list(payload.get("decision_gates") or []),
        "comparison_dimensions": list(payload.get("comparison_dimensions") or []),
        "config": {
            "minimum_plan_count": int(payload.get("minimum_plan_count", 2)),
            "default_plan_count": int(payload.get("default_plan_count", 3)),
            "business_contract_version": 1,
            "business_validation": "accepted",
            "toolbox": {
                "reasoning_enabled": bool(toolbox.get("reasoning_enabled", True)),
                "calculation_enabled": bool(toolbox.get("calculation_enabled", True)),
                "target_sections": dict(toolbox.get("target_sections") or {}),
            },
            "writing_policy": writing_policy,
            "output": output,
        },
    }


def business_scenario_from_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Project an executable contract back into editable business settings."""

    input_schema = dict(contract.get("input_schema") or {})
    required = set(input_schema.get("required") or [])
    inputs = []
    for key, raw in (input_schema.get("properties") or {}).items():
        spec = dict(raw or {})
        inputs.append(
            {
                "key": key,
                "label": spec.get("title") or _DEFAULT_INPUT_LABELS.get(key) or key.replace("_", " "),
                "data_type": spec.get("type") or "string",
                "unit": spec.get("unit") or _DEFAULT_INPUT_UNITS.get(key),
                "required": key in required,
                "confirmation_required": bool(spec.get("confirmation_required", True)),
                "source_guidance": spec.get("source_guidance") or "",
                "minimum": spec.get("minimum"),
                "maximum": spec.get("maximum"),
            }
        )
    sections = []
    for item in (contract.get("chapter_template") or {}).get("chapters") or []:
        if not isinstance(item, dict):
            continue
        sections.append(
            {
                "key": item.get("key"),
                "title": item.get("title"),
                "purpose": item.get("instruction") or "根据已核验资料撰写本章节。",
                "generation_mode": item.get("generation_mode") or "agent",
                "required_inputs": list(item.get("required_inputs") or []),
                "toolbox_outputs": list(item.get("toolbox_outputs") or []),
                "citation_required": bool(item.get("citation_required", True)),
                "knowledge": dict(item.get("knowledge") or {}),
            }
        )
    config = dict(contract.get("config") or {})
    toolbox = dict(config.get("toolbox") or {})
    policy = dict(config.get("writing_policy") or contract.get("review_rules") or {})
    output = dict(config.get("output") or {})
    output_schema = dict(contract.get("output_schema") or {})
    return {
        "inputs": inputs,
        "sections": sections,
        "toolbox": {
            "reasoning_enabled": bool(toolbox.get("reasoning_enabled", True)),
            "rule_set_ids": list(contract.get("rule_set_ids") or []),
            "calculation_enabled": bool(toolbox.get("calculation_enabled", True)),
            "formula_ids": list(contract.get("formula_ids") or []),
            "target_sections": dict(toolbox.get("target_sections") or {}),
        },
        "writing_policy": {
            "missing_input_action": policy.get("missing_input_action", "block"),
            "unverified_fact_action": policy.get("unverified_fact_action", "block"),
            "require_citations": bool(policy.get("require_citations", True)),
            "allow_manual_override": bool(policy.get("allow_manual_override", True)),
        },
        "output": {
            "title_pattern": output.get("title_pattern") or output_schema.get("title_pattern") or "{project_name}",
            "allowed_formats": list(output.get("allowed_formats") or output_schema.get("allowed_formats") or ["docx", "pdf"]),
        },
        "decision_gates": list(contract.get("decision_gates") or []),
        "comparison_dimensions": list(contract.get("comparison_dimensions") or []),
        "minimum_plan_count": int(config.get("minimum_plan_count", 2)),
        "default_plan_count": int(config.get("default_plan_count", 3)),
        "activate": False,
    }


def business_setting_effects(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Explain only settings that have a concrete runtime consumer."""

    return [
        {"setting": "输入项", "runtime_effect": "决定报告生成前必须存在并由用户确认的业务事实。"},
        {"setting": "章节", "runtime_effect": "决定 Agent 的生成顺序，以及计算和推演结果写入哪个正文块。"},
        {"setting": "推演工具", "runtime_effect": "决定生成前实际执行的规则集、公式和结果落位。"},
        {"setting": "写作约束", "runtime_effect": "决定缺失输入、未确认事实和无引用内容是否阻止生成或发布。"},
        {"setting": "导出格式", "runtime_effect": "限制审校发布页可以真实生成的文件类型。"},
        {"setting": "人工确认", "runtime_effect": "决定哪些业务决策未完成时不能正式发布。"},
    ]
