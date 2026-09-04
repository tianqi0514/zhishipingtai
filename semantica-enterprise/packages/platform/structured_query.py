from __future__ import annotations

import hashlib
import json
import time
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    and_,
    bindparam,
    case,
    cast,
    column,
    distinct,
    exists,
    func,
    literal_column,
    not_,
    or_,
    select,
    table,
)
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.sql import Select

from apps.api.structured_schemas import QueryExpression, SemanticQueryIR, SemanticQueryPlan
from packages.platform.models import DataSourceSchemaVersion, SemanticMappingVersion, SourceConnector
from packages.platform.structured_data import (
    StructuredDataError,
    _json_value,
    _mask,
    canonical_json,
    create_source_engine,
    fingerprint,
    readonly_connection,
    inspect_distinct_values,
    sensitive_suggestion,
)


AGGREGATE_FUNCTIONS = {"count", "sum", "avg", "average", "min", "max"}
SCALAR_FUNCTIONS = {
    "lower", "upper", "coalesce", "abs", "round", "length", "date",
    "extract_year", "extract_month", "extract_day",
}
WINDOW_FUNCTIONS = {"row_number", "rank", "dense_rank", "sum", "avg", "count", "min", "max"}
BINARY_OPERATORS = {"=", "!=", ">", ">=", "<", "<=", "+", "-", "*", "/", "%", "like"}
CAST_TYPES = {
    "string": String,
    "integer": Integer,
    "number": Numeric,
    "boolean": Boolean,
    "date": Date,
    "datetime": DateTime,
}
_ACTIVE_QUERY_CONNECTIONS: dict[str, Any] = {}
_ACTIVE_QUERY_LOCK = threading.Lock()


def _canonicalize_generated_plan(raw: Any) -> Any:
    """Normalize a small, closed set of provider aliases before strict validation."""
    if not isinstance(raw, dict) or not isinstance(raw.get("plan"), dict):
        return raw
    plan = dict(raw["plan"])
    operator_aliases = {
        "=": "eq", "==": "eq", "!=": "ne", "<>": "ne",
        ">": "gt", ">=": "gte", "<": "lt", "<=": "lte",
    }
    filters = []
    for item in plan.get("filters") or []:
        if not isinstance(item, dict):
            filters.append(item)
            continue
        normalized = dict(item)
        normalized["operator"] = operator_aliases.get(normalized.get("operator"), normalized.get("operator"))
        filters.append(normalized)
    plan["filters"] = filters
    return {**raw, "plan": plan}


def _metric_filter_expression(required_filter: dict[str, Any], binding: str) -> dict[str, Any] | None:
    attribute = {"kind": "attribute", "attribute_id": required_filter.get("attribute_id"), "binding": binding}
    operator = required_filter.get("operator")
    if operator in {"eq", "ne", "gt", "gte", "lt", "lte"}:
        symbols = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        return {"kind": "binary", "operator": symbols[operator], "left": attribute, "right": {"kind": "literal", "value": required_filter.get("value")}}
    if operator == "between":
        return {"kind": "between", "expression": attribute, "lower": {"kind": "literal", "value": required_filter.get("value")}, "upper": {"kind": "literal", "value": required_filter.get("upper")}}
    if operator == "in" and isinstance(required_filter.get("value"), list):
        return {"kind": "in", "expression": attribute, "options": [{"kind": "literal", "value": value} for value in required_filter["value"]]}
    if operator in {"is_null", "is_not_null"}:
        return {"kind": "is_null", "expression": attribute, "negated": operator == "is_not_null"}
    return None


def _apply_metric_contracts(raw: Any, version: SemanticMappingVersion) -> Any:
    """Deterministically inject activated metric filters before strict validation.

    The model selects business IDs, while the platform owns mandatory metric
    population rules. Applying those rules here avoids a second model call for a
    contract the model is not allowed to override and keeps all SQL generation in
    the existing strict IR/compiler path.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("plan"), dict) or not isinstance(raw.get("query_ir"), dict):
        return raw
    plan, query_ir = dict(raw["plan"]), dict(raw["query_ir"])
    attributes = {item.get("id"): item for item in (version.manifest or {}).get("attributes") or []}
    bindings: dict[str, str] = {}
    from_entity = query_ir.get("from_entity") or {}
    if from_entity.get("entity_id") and from_entity.get("binding"):
        bindings[from_entity["entity_id"]] = from_entity["binding"]
    for join in query_ir.get("joins") or []:
        if join.get("entity_id") and join.get("binding"):
            bindings[join["entity_id"]] = join["binding"]
    filters = list(plan.get("filters") or [])
    where = query_ir.get("where")
    evidence = list(plan.get("evidence_constraints") or [])
    for output in plan.get("outputs") or []:
        for attribute_id in output.get("attribute_ids") or []:
            attribute = attributes.get(attribute_id) or {}
            if attribute.get("default_aggregate") and not output.get("aggregate"):
                output["aggregate"] = attribute["default_aggregate"]
            definition = attribute.get("business_definition")
            if definition and definition not in evidence:
                evidence.append(definition)
            for required_filter in attribute.get("required_filters") or []:
                if any(all(candidate.get(key) == value for key, value in required_filter.items()) for candidate in filters):
                    continue
                filters.append(dict(required_filter))
                binding = bindings.get(attribute.get("entity_id"))
                predicate = _metric_filter_expression(required_filter, binding) if binding else None
                if predicate:
                    where = predicate if where is None else {"kind": "logical", "operator": "and", "operands": [where, predicate]}
    plan["filters"] = filters
    plan["evidence_constraints"] = evidence
    query_ir["where"] = where
    return {**raw, "plan": plan, "query_ir": query_ir}


def apply_activated_metric_contracts(
    plan: SemanticQueryPlan,
    query_ir: SemanticQueryIR,
    version: SemanticMappingVersion,
) -> tuple[SemanticQueryPlan, SemanticQueryIR]:
    """Apply the server-owned metric population rules to a typed query.

    Agent clients are still required to submit a strict Plan and IR, but fixed
    business filters belong to the activated mapping rather than to the model.
    Re-applying them at the trusted API boundary prevents omission or override
    without accepting physical identifiers or raw SQL from the caller.
    """
    normalized = _apply_metric_contracts(
        {"plan": plan.model_dump(), "query_ir": query_ir.model_dump()},
        version,
    )
    return (
        SemanticQueryPlan.model_validate(normalized["plan"]),
        SemanticQueryIR.model_validate(normalized["query_ir"]),
    )


def semantic_catalog_for_planner(version: SemanticMappingVersion) -> dict[str, Any]:
    """Expose business IDs only; physical tables and columns stay server-side."""
    manifest = version.manifest or {}
    entities = manifest.get("entities") or []
    attributes = manifest.get("attributes") or []
    relationships = manifest.get("relationships") or []
    return {
        "mapping_version_id": version.id,
        "entities": [{
            "id": item.get("id"),
            "label": item.get("label"),
            "description": item.get("description"),
            "attribute_ids": [row.get("id") for row in attributes if row.get("entity_id") == item.get("id")],
        } for item in entities],
        "attributes": [{
            "id": item.get("id"),
            "entity_id": item.get("entity_id"),
            "label": item.get("label"),
            "description": item.get("description") or "",
            "aliases": item.get("aliases") or [],
            "business_definition": item.get("business_definition") or "",
            "data_type": item.get("data_type"),
            "semantic_type": item.get("semantic_type"),
            "is_measure": bool(item.get("is_measure")),
            "aggregation": item.get("aggregation"),
            "default_aggregate": item.get("default_aggregate"),
            "required_filters": item.get("required_filters") or [],
        } for item in attributes],
        "relationships": [{
            "id": item.get("id"),
            "label": item.get("label"),
            "from_entity_id": item.get("from_entity_id"),
            "to_entity_id": item.get("to_entity_id"),
            "cardinality": item.get("cardinality"),
        } for item in relationships],
    }


def collect_semantic_value_hints(
    source: SourceConnector,
    schema: DataSourceSchemaVersion,
    policy: Any,
    version: SemanticMappingVersion,
    *,
    max_attributes: int = 12,
    max_values: int = 20,
) -> dict[str, list[Any]]:
    """Inspect small enum-like business fields without exposing physical names to the model."""
    manifest = version.manifest or {}
    fragments = {
        fragment.get("id"): fragment
        for entity in manifest.get("entities") or []
        for fragment in entity.get("fragments") or []
    }
    enum_tokens = {
        "status", "state", "type", "category", "region", "level",
        "状态", "类型", "类别", "分类", "地区", "区域", "等级",
    }
    hints: dict[str, list[Any]] = {}
    candidates = []
    for attribute in manifest.get("attributes") or []:
        semantic_type = attribute.get("semantic_type") or attribute.get("data_type")
        searchable = f"{attribute.get('id', '')} {attribute.get('label', '')}".casefold()
        if semantic_type == "string" and any(token in searchable for token in enum_tokens):
            candidates.append(attribute)
    for attribute in candidates[:max_attributes]:
        fragment = fragments.get(attribute.get("fragment_id"))
        if fragment is None:
            continue
        try:
            result = inspect_distinct_values(
                source,
                schema,
                policy,
                object_id=fragment["object_id"],
                column_id=attribute["column_id"],
                limit=max_values,
            )
        except StructuredDataError:
            continue
        if result["values"]:
            hints[attribute["id"]] = result["values"]
    return hints


def generate_semantic_plan_ir(
    question: str,
    version: SemanticMappingVersion,
    *,
    api_key: str,
    model: str,
    base_url: str | None,
    temperature: float = 0.1,
    timeout: float = 60,
    max_retries: int = 2,
    request_parameters: dict[str, Any] | None = None,
    value_hints: dict[str, list[Any]] | None = None,
    generator=None,
) -> tuple[SemanticQueryPlan, SemanticQueryIR]:
    catalog = semantic_catalog_for_planner(version)
    for attribute in catalog["attributes"]:
        values = (value_hints or {}).get(attribute["id"])
        if values:
            attribute["allowed_values"] = values
    schema_contract = {
        "plan": SemanticQueryPlan.model_json_schema(),
        "query_ir": SemanticQueryIR.model_json_schema(),
    }
    prompt = f"""你是传神智库的结构化语义查询规划器。只输出一个 JSON 对象，键为 plan 和 query_ir，不要 Markdown。
只能使用下面语义目录中的 id；严禁输出 SQL、物理表名、物理字段名、未列出的函数或额外字段。
Plan 版本为 chuanshen.semantic-query-plan/v1；IR 版本为 chuanshen.query-ir/v1。
必须逐字段遵守下面的 JSON Schema，不能改名、缩写或沿用其他版本的字段。所有对象 extra=forbid。
IR expression kind 可用 attribute/literal/aggregate/function/binary/logical/not/between/in/is_null/case/cast/subquery/exists/window。
属性表达式必须包含 attribute_id 与 binding；aggregate 使用 function、expression 和 distinct；普通 function 才使用 arguments；比较使用 binary 的 operator/left/right；过滤值使用 literal 的 value。
同比、环比、比例、排名必须在 plan.calculation_steps 中明确计算步骤；简单汇总可以不填 calculation_steps。
如果属性提供 allowed_values，Plan 和 IR 的过滤值必须使用其中的真实值；不要翻译或改写数据库枚举值。
如果指标提供 default_aggregate、business_definition 或 required_filters，必须严格采用该业务口径；required_filters 必须同时写入 Plan filters 和 IR where，不能以用户未明确说明为由省略。
严格 Schema：{json.dumps(schema_contract, ensure_ascii=False)}
语义目录：{json.dumps(catalog, ensure_ascii=False)}
用户问题：{question}"""
    if generator is None:
        from semantica.semantic_extract.providers import OpenAIProvider
        from packages.semantica_adapter.extract import _effective_temperature
        from packages.semantica_adapter.llm_transport import apply_model_transport_options

        provider = apply_model_transport_options(
            OpenAIProvider(api_key=api_key, model=model, base_url=base_url),
            timeout=timeout,
            max_retries=max_retries,
            request_parameters=request_parameters,
        )
        generator = lambda value: provider.generate_structured(
            value,
            temperature=_effective_temperature(model, temperature),
        )
    raw = _apply_metric_contracts(_canonicalize_generated_plan(generator(prompt)), version)
    validation_error: Exception | None = None
    for attempt in range(2):
        if not isinstance(raw, dict) or not isinstance(raw.get("plan"), dict) or not isinstance(raw.get("query_ir"), dict):
            validation_error = ValueError("顶层必须是包含 plan 和 query_ir 对象的 JSON")
        else:
            try:
                plan = SemanticQueryPlan.model_validate(raw["plan"])
                query_ir = SemanticQueryIR.model_validate(raw["query_ir"])
                semantic_report = validate_ir(query_ir, plan, version)
                if semantic_report["ok"]:
                    return plan, query_ir
                validation_error = ValueError(
                    "确定性语义校验失败：" + "；".join(semantic_report["errors"][:12])
                )
            except Exception as exc:
                validation_error = exc
        if attempt == 0:
            repair_prompt = f"""上一次结构化查询计划不符合传神智库严格协议。只输出修正后的 JSON 对象，键必须是 plan 和 query_ir，不要解释、不要 Markdown、不要 SQL。
校验错误：{str(validation_error)[:6000]}
上一次输出：{json.dumps(raw, ensure_ascii=False)[:14000]}
严格 Schema：{json.dumps(schema_contract, ensure_ascii=False)}
语义目录：{json.dumps(catalog, ensure_ascii=False)}
原始问题：{question}
必须使用 Schema 中的原字段名，删除所有 extra 字段。"""
            raw = _apply_metric_contracts(_canonicalize_generated_plan(generator(repair_prompt)), version)
    raise StructuredDataError(
        "SEMANTIC_PLANNER_INVALID",
        f"模型查询计划连续两次未通过严格 Schema：{validation_error}",
    )


def _manifest_indexes(version: SemanticMappingVersion) -> tuple[dict, dict, dict, dict]:
    manifest = version.manifest or {}
    entities = {item["id"]: item for item in manifest.get("entities") or []}
    attributes = {item["id"]: item for item in manifest.get("attributes") or []}
    relationships = {item["id"]: item for item in manifest.get("relationships") or []}
    fragments = {
        fragment["id"]: (entity, fragment)
        for entity in entities.values()
        for fragment in entity.get("fragments") or []
    }
    return entities, attributes, relationships, fragments


def _plan_attribute_ids(plan: SemanticQueryPlan) -> set[str]:
    return {
        attribute_id
        for output in plan.outputs
        for attribute_id in output.attribute_ids
    } | {
        item.attribute_id for item in plan.filters
    } | set(plan.group_by_attribute_ids) | {
        attribute_id for item in plan.ordering for attribute_id in item.attribute_ids
    } | {
        attribute_id for step in plan.calculation_steps for attribute_id in step.attribute_ids
    } | {
        attribute_id for step in plan.calculation_steps for attribute_id in step.group_by_attribute_ids
    }


def validate_plan(plan: SemanticQueryPlan, version: SemanticMappingVersion) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if version.status != "active":
        errors.append("只能使用已激活且未过期的语义映射")
    entities, attributes, relationships, _ = _manifest_indexes(version)
    entity_ids = set(plan.entity_ids)
    for entity_id in plan.entity_ids:
        if entity_id not in entities:
            errors.append(f"查询计划引用了未知业务实体：{entity_id}")
    for relationship_id in plan.relationship_ids:
        relationship = relationships.get(relationship_id)
        if relationship is None:
            errors.append(f"查询计划引用了未知业务关系：{relationship_id}")
        elif {relationship["from_entity_id"], relationship["to_entity_id"]} - entity_ids:
            errors.append(f"业务关系的起止实体未完整加入计划：{relationship_id}")
    for attribute_id in sorted(_plan_attribute_ids(plan)):
        attribute = attributes.get(attribute_id)
        if attribute is None:
            errors.append(f"查询计划引用了未知业务属性：{attribute_id}")
        elif attribute["entity_id"] not in entity_ids:
            errors.append(f"业务属性所属实体未加入计划：{attribute_id}")
    for step in plan.calculation_steps:
        if set(step.entity_ids) - entity_ids:
            errors.append(f"计算步骤 {step.step_id} 使用了计划外实体")
        if set(step.relationship_ids) - set(plan.relationship_ids):
            errors.append(f"计算步骤 {step.step_id} 使用了计划外关系")
    if plan.metric_contract.base_entity_ids and set(plan.metric_contract.base_entity_ids) - entity_ids:
        errors.append("指标口径引用了计划外实体")
    if plan.metric_contract.base_relationship_ids and set(plan.metric_contract.base_relationship_ids) - set(plan.relationship_ids):
        errors.append("指标口径引用了计划外关系")
    plan_filters = [item.model_dump() for item in plan.filters]
    for output in plan.outputs:
        for attribute_id in output.attribute_ids:
            attribute = attributes.get(attribute_id) or {}
            required_filters = attribute.get("required_filters") or []
            if not attribute.get("is_measure") and not required_filters:
                continue
            default_aggregate = attribute.get("default_aggregate")
            if default_aggregate and output.aggregate != default_aggregate:
                errors.append(f"指标 {attribute.get('label') or attribute_id} 必须使用 {default_aggregate} 聚合")
            for required_filter in required_filters:
                if not any(all(candidate.get(key) == value for key, value in required_filter.items()) for candidate in plan_filters):
                    errors.append(f"指标 {attribute.get('label') or attribute_id} 缺少固定口径筛选：{required_filter.get('attribute_id')}")
    complex_terms = ("同比", "环比", "增长", "差值", "比例", "百分比", "排名", "top")
    if any(term in f"{plan.original_question} {plan.intent}".casefold() for term in complex_terms) and not plan.calculation_steps:
        errors.append("同比、环比、比例、排名或多阶段计算必须声明计算步骤")
    if any(output.aggregate for output in plan.outputs) and not plan.result_grain:
        warnings.append("聚合查询未说明结果粒度")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "plan_fingerprint": fingerprint(plan.model_dump()),
    }


def _walk_expression(expression: QueryExpression | None) -> Iterable[QueryExpression]:
    if expression is None:
        return
    yield expression
    for item in expression.arguments + expression.operands + expression.options + expression.partition_by:
        yield from _walk_expression(item)
    for item in (expression.left, expression.right, expression.expression, expression.lower, expression.upper, expression.else_expression):
        yield from _walk_expression(item)
    for branch in expression.whens:
        if isinstance(branch, dict):
            if "when" in branch:
                yield from _walk_expression(QueryExpression.model_validate(branch["when"]))
            if "then" in branch:
                yield from _walk_expression(QueryExpression.model_validate(branch["then"]))
    if expression.query:
        yield from _walk_ir_expressions(expression.query)


def _walk_ir_expressions(ir: SemanticQueryIR) -> Iterable[QueryExpression]:
    for projection in ir.select:
        yield from _walk_expression(projection.expression)
    yield from _walk_expression(ir.where)
    for item in ir.group_by:
        yield from _walk_expression(item)
    yield from _walk_expression(ir.having)
    for item in ir.order_by:
        yield from _walk_expression(item.expression)


def _walk_ir_nodes(ir: SemanticQueryIR) -> Iterable[SemanticQueryIR]:
    yield ir
    for expression in _walk_ir_expressions(ir):
        if expression.kind in {"subquery", "exists"} and expression.query is not None:
            # _walk_expression already traverses the nested query's expressions;
            # recurse here to validate its binding and join scope as well.
            yield from _walk_ir_nodes(expression.query)


def _ir_filter_signatures(expression: QueryExpression | None) -> list[dict[str, Any]]:
    """Return simple, auditable predicates used to prove Plan filters reached SQL IR."""
    if expression is None:
        return []
    if expression.kind == "logical":
        return [item for operand in expression.operands for item in _ir_filter_signatures(operand)]
    if expression.kind == "not":
        return _ir_filter_signatures(expression.expression)
    if expression.kind == "binary" and expression.left and expression.right:
        left, right = expression.left, expression.right
        if left.kind == "literal" and right.kind == "attribute":
            left, right = right, left
        operators = {"=": "eq", "!=": "ne", "<>": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
        if left.kind == "attribute" and right.kind == "literal" and expression.operator in operators:
            return [{"attribute_id": left.attribute_id, "operator": operators[expression.operator], "value": right.value, "upper": None}]
    if expression.kind == "between" and expression.expression and expression.lower and expression.upper:
        if expression.expression.kind == "attribute" and expression.lower.kind == "literal" and expression.upper.kind == "literal":
            return [{"attribute_id": expression.expression.attribute_id, "operator": "between", "value": expression.lower.value, "upper": expression.upper.value}]
    if expression.kind == "in" and expression.expression and expression.expression.kind == "attribute" and expression.options:
        if all(option.kind == "literal" for option in expression.options):
            return [{"attribute_id": expression.expression.attribute_id, "operator": "in", "value": [option.value for option in expression.options], "upper": None}]
    if expression.kind == "is_null" and expression.expression and expression.expression.kind == "attribute":
        return [{"attribute_id": expression.expression.attribute_id, "operator": "is_not_null" if expression.negated else "is_null", "value": None, "upper": None}]
    return []


def validate_ir(ir: SemanticQueryIR, plan: SemanticQueryPlan, version: SemanticMappingVersion) -> dict[str, Any]:
    plan_report = validate_plan(plan, version)
    errors = list(plan_report["errors"])
    warnings = list(plan_report["warnings"])
    entities, attributes, relationships, _ = _manifest_indexes(version)
    binding_entities: dict[str, str] = {}
    join_count = 0
    seen_scope_ids: set[int] = set()
    for scope in _walk_ir_nodes(ir):
        if id(scope) in seen_scope_ids:
            continue
        seen_scope_ids.add(id(scope))
        scope_bindings = {scope.from_entity.binding: scope.from_entity.entity_id}
        if scope.from_entity.entity_id not in plan.entity_ids:
            errors.append("IR 主实体不在查询计划中")
        for join in scope.joins:
            join_count += 1
            if join.entity_id not in plan.entity_ids:
                errors.append(f"IR Join 实体不在查询计划中：{join.entity_id}")
            if join.relationship_id not in plan.relationship_ids:
                errors.append(f"IR Join 关系不在查询计划中：{join.relationship_id}")
            relationship = relationships.get(join.relationship_id)
            if relationship:
                expected = {relationship["from_entity_id"], relationship["to_entity_id"]}
                actual = {scope_bindings.get(join.from_binding), join.entity_id}
                if actual != expected:
                    errors.append(f"IR Join 实体与关系端点不匹配：{join.relationship_id}")
            scope_bindings[join.binding] = join.entity_id
        for binding, entity_id in scope_bindings.items():
            if binding in binding_entities and binding_entities[binding] != entity_id:
                errors.append(f"不同查询作用域重复使用了含义冲突的实体绑定：{binding}")
            binding_entities[binding] = entity_id
    allowed_attributes = _plan_attribute_ids(plan)
    ir_filters = [item for scope in _walk_ir_nodes(ir) for item in _ir_filter_signatures(scope.where)]
    for planned_filter in plan.filters:
        expected = planned_filter.model_dump()
        if not any(all(candidate.get(key) == value for key, value in expected.items()) for candidate in ir_filters):
            errors.append(f"IR 缺少查询计划声明的筛选：{planned_filter.attribute_id}")
    for expression in _walk_ir_expressions(ir):
        if expression.kind == "attribute":
            attribute = attributes.get(str(expression.attribute_id))
            if attribute is None:
                errors.append(f"IR 引用了未知属性：{expression.attribute_id}")
            elif expression.attribute_id not in allowed_attributes:
                errors.append(f"IR 引用了计划未声明的属性：{expression.attribute_id}")
            elif binding_entities.get(str(expression.binding)) != attribute["entity_id"]:
                errors.append(f"属性与实体绑定不匹配：{expression.attribute_id}")
        elif expression.kind == "aggregate" and str(expression.function).casefold() not in AGGREGATE_FUNCTIONS:
            errors.append(f"聚合函数不受支持：{expression.function}")
        elif expression.kind == "function" and str(expression.function).casefold() not in SCALAR_FUNCTIONS:
            errors.append(f"函数不在白名单：{expression.function}")
        elif expression.kind == "window" and str(expression.function).casefold() not in WINDOW_FUNCTIONS:
            errors.append(f"窗口函数不在白名单：{expression.function}")
        elif expression.kind == "binary" and str(expression.operator).casefold() not in BINARY_OPERATORS:
            errors.append(f"运算符不在白名单：{expression.operator}")
        elif expression.kind == "cast" and str(expression.target_type).casefold() not in CAST_TYPES:
            errors.append(f"类型转换不在白名单：{expression.target_type}")
    if join_count > 8:
        errors.append("Join 数量超过安全上限")
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "plan_fingerprint": plan_report["plan_fingerprint"],
        "ir_fingerprint": fingerprint(ir.model_dump()),
    }


@dataclass
class CompiledStructuredQuery:
    dialect: str
    statement: Select
    sql_template: str
    parameters: dict[str, Any]
    parameter_summary: dict[str, Any]
    referenced_objects: list[str]
    referenced_columns: list[str]
    mapping_version_id: str
    schema_version_id: str
    query_fingerprint: str
    max_rows: int


class DeterministicCompiler:
    def __init__(
        self,
        dialect: str,
        version: SemanticMappingVersion,
        schema: DataSourceSchemaVersion,
        *,
        max_rows: int = 500,
    ):
        if dialect not in {"postgresql", "mysql"}:
            raise StructuredDataError("UNSUPPORTED_DIALECT", "仅支持 MySQL 和 PostgreSQL")
        self.dialect = dialect
        self.version = version
        self.schema = schema
        self.max_rows = max(1, min(int(max_rows), 10_000))
        self.entities, self.attributes, self.relationships, self.fragments = _manifest_indexes(version)
        self.catalog_objects = {item["id"]: item for item in (schema.catalog or {}).get("objects") or []}
        self.catalog_columns = {
            column_row["id"]: (object_row, column_row)
            for object_row in self.catalog_objects.values()
            for column_row in object_row.get("columns") or []
        }
        self.parameters: dict[str, Any] = {}
        self.referenced_objects: set[str] = set()
        self.referenced_columns: set[str] = set()

    def _primary_fragment(self, entity_id: str) -> dict[str, Any]:
        entity = self.entities.get(entity_id)
        if entity is None:
            raise StructuredDataError("UNKNOWN_ENTITY", f"未知业务实体：{entity_id}")
        fragments = [item for item in entity.get("fragments") or [] if item.get("role") == "primary"]
        if len(fragments) != 1:
            raise StructuredDataError("ENTITY_FRAGMENT_INVALID", f"实体没有唯一主数据片段：{entity_id}")
        return fragments[0]

    def _physical_table(self, object_id: str, binding: str):
        object_row = self.catalog_objects.get(object_id)
        if object_row is None:
            raise StructuredDataError("UNKNOWN_OBJECT", f"映射引用了未知数据对象：{object_id}")
        self.referenced_objects.add(object_id)
        return table(
            object_row["name"],
            *(column(item["name"]) for item in object_row.get("columns") or []),
            schema=object_row.get("schema"),
        ).alias(binding)

    def _attribute_column(self, attribute_id: str, binding: str, bindings: dict[str, dict[str, Any]]):
        attribute = self.attributes.get(attribute_id)
        if attribute is None:
            raise StructuredDataError("UNKNOWN_ATTRIBUTE", f"未知业务属性：{attribute_id}")
        binding_row = bindings.get(binding)
        if binding_row is None or binding_row["entity_id"] != attribute["entity_id"]:
            raise StructuredDataError("ATTRIBUTE_BINDING_INVALID", f"属性实体绑定不匹配：{attribute_id}")
        column_id = attribute["column_id"]
        physical = self.catalog_columns.get(column_id)
        if physical is None:
            raise StructuredDataError("UNKNOWN_COLUMN", f"映射字段不存在：{column_id}")
        self.referenced_columns.add(column_id)
        return binding_row["table"].c[physical[1]["name"]]

    def _attribute_semantic_type(self, attribute_id: str) -> str | None:
        attribute = self.attributes.get(attribute_id) or {}
        semantic_type = str(attribute.get("semantic_type") or "").casefold()
        if semantic_type and semantic_type != "unknown":
            return semantic_type
        physical = self.catalog_columns.get(str(attribute.get("column_id") or ""))
        return str((physical or ({}, {}))[1].get("type_family") or "").casefold() or None

    def _coerce_literal(self, value: Any, semantic_type: str | None) -> Any:
        if value is None or not semantic_type:
            return value
        try:
            if semantic_type == "date" and isinstance(value, str):
                return date.fromisoformat(value)
            if semantic_type == "datetime" and isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            if semantic_type == "integer" and not isinstance(value, bool):
                return int(value)
            if semantic_type == "number" and not isinstance(value, (int, float, Decimal)):
                return Decimal(str(value))
            if semantic_type == "boolean" and isinstance(value, str):
                normalized = value.strip().casefold()
                if normalized in {"true", "1", "yes"}:
                    return True
                if normalized in {"false", "0", "no"}:
                    return False
                raise ValueError("invalid boolean")
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise StructuredDataError(
                "LITERAL_TYPE_INVALID",
                f"筛选值与业务属性类型不兼容：{semantic_type}",
            ) from exc
        return value

    def _literal(self, value: Any, semantic_type: str | None = None):
        value = self._coerce_literal(value, semantic_type)
        name = f"p{len(self.parameters) + 1}"
        self.parameters[name] = value
        return bindparam(name, value=value)

    def _expression(
        self,
        expression: QueryExpression,
        bindings: dict[str, dict[str, Any]],
        *,
        literal_type: str | None = None,
    ):
        kind = expression.kind
        if kind == "attribute":
            return self._attribute_column(str(expression.attribute_id), str(expression.binding), bindings)
        if kind == "literal":
            return self._literal(expression.value, literal_type)
        if kind == "aggregate":
            function = str(expression.function).casefold()
            if function not in AGGREGATE_FUNCTIONS:
                raise StructuredDataError("FUNCTION_DENIED", f"聚合函数不受支持：{function}")
            argument = self._expression(expression.expression, bindings) if expression.expression else literal_column("1")
            if expression.distinct:
                argument = distinct(argument)
            return getattr(func, "avg" if function == "average" else function)(argument)
        if kind == "function":
            function = str(expression.function).casefold()
            if function not in SCALAR_FUNCTIONS:
                raise StructuredDataError("FUNCTION_DENIED", f"函数不在白名单：{function}")
            arguments = [self._expression(item, bindings) for item in expression.arguments]
            if function.startswith("extract_"):
                if len(arguments) != 1:
                    raise StructuredDataError("FUNCTION_ARGUMENT_INVALID", f"{function} 需要一个参数")
                part = function.removeprefix("extract_")
                return func.extract(part, arguments[0])
            return getattr(func, function)(*arguments)
        if kind == "binary":
            operator = str(expression.operator).casefold()
            if operator not in BINARY_OPERATORS:
                raise StructuredDataError("OPERATOR_DENIED", f"运算符不在白名单：{operator}")
            left_type = (
                self._attribute_semantic_type(str(expression.left.attribute_id))
                if expression.left and expression.left.kind == "attribute"
                else None
            )
            right_type = (
                self._attribute_semantic_type(str(expression.right.attribute_id))
                if expression.right and expression.right.kind == "attribute"
                else None
            )
            left = self._expression(expression.left, bindings, literal_type=right_type)
            right = self._expression(expression.right, bindings, literal_type=left_type)
            return {
                "=": lambda: left == right, "!=": lambda: left != right,
                ">": lambda: left > right, ">=": lambda: left >= right,
                "<": lambda: left < right, "<=": lambda: left <= right,
                "+": lambda: left + right, "-": lambda: left - right,
                "*": lambda: left * right, "/": lambda: left / right,
                "%": lambda: left % right, "like": lambda: left.like(right),
            }[operator]()
        if kind == "logical":
            values = [self._expression(item, bindings) for item in expression.operands]
            return and_(*values) if expression.operator == "and" else or_(*values)
        if kind == "not":
            return not_(self._expression(expression.expression, bindings))
        if kind == "between":
            target_type = (
                self._attribute_semantic_type(str(expression.expression.attribute_id))
                if expression.expression and expression.expression.kind == "attribute"
                else None
            )
            result = self._expression(expression.expression, bindings).between(
                self._expression(expression.lower, bindings, literal_type=target_type),
                self._expression(expression.upper, bindings, literal_type=target_type),
            )
            return not_(result) if expression.negated else result
        if kind == "in":
            target = self._expression(expression.expression, bindings)
            target_type = (
                self._attribute_semantic_type(str(expression.expression.attribute_id))
                if expression.expression and expression.expression.kind == "attribute"
                else None
            )
            if expression.query:
                options = self._compile_ir(expression.query, nested=True).scalar_subquery()
            else:
                options = [self._expression(item, bindings, literal_type=target_type) for item in expression.options]
            result = target.in_(options)
            return not_(result) if expression.negated else result
        if kind == "is_null":
            target = self._expression(expression.expression, bindings)
            return target.is_not(None) if expression.negated else target.is_(None)
        if kind == "case":
            branches = [
                (
                    self._expression(QueryExpression.model_validate(item["when"]), bindings),
                    self._expression(QueryExpression.model_validate(item["then"]), bindings),
                )
                for item in expression.whens
            ]
            else_value = self._expression(expression.else_expression, bindings) if expression.else_expression else None
            return case(*branches, else_=else_value)
        if kind == "cast":
            target_type = str(expression.target_type).casefold()
            if target_type not in CAST_TYPES:
                raise StructuredDataError("CAST_DENIED", f"类型转换不在白名单：{target_type}")
            return cast(self._expression(expression.expression, bindings), CAST_TYPES[target_type])
        if kind == "subquery":
            return self._compile_ir(expression.query, nested=True).scalar_subquery()
        if kind == "exists":
            result = exists(self._compile_ir(expression.query, nested=True))
            return not_(result) if expression.negated else result
        if kind == "window":
            function = str(expression.function).casefold()
            if function not in WINDOW_FUNCTIONS:
                raise StructuredDataError("FUNCTION_DENIED", f"窗口函数不在白名单：{function}")
            arguments = [self._expression(item, bindings) for item in expression.arguments]
            value = getattr(func, function)(*arguments)
            partitions = [self._expression(item, bindings) for item in expression.partition_by]
            orderings = []
            for item in expression.window_order_by:
                order_expression = self._expression(QueryExpression.model_validate(item["expression"]), bindings)
                orderings.append(order_expression.desc() if item.get("direction") == "desc" else order_expression.asc())
            return value.over(partition_by=partitions or None, order_by=orderings or None)
        raise StructuredDataError("EXPRESSION_UNSUPPORTED", f"表达式类型不受支持：{kind}")

    def _compile_ir(self, ir: SemanticQueryIR, *, nested: bool = False) -> Select:
        bindings: dict[str, dict[str, Any]] = {}
        fragment = self._primary_fragment(ir.from_entity.entity_id)
        root_table = self._physical_table(fragment["object_id"], ir.from_entity.binding)
        bindings[ir.from_entity.binding] = {
            "entity_id": ir.from_entity.entity_id,
            "object_id": fragment["object_id"],
            "table": root_table,
        }
        from_clause = root_table
        for join in ir.joins:
            relationship = self.relationships.get(join.relationship_id)
            if relationship is None:
                raise StructuredDataError("UNKNOWN_RELATIONSHIP", f"未知业务关系：{join.relationship_id}")
            target_fragment = self._primary_fragment(join.entity_id)
            target_table = self._physical_table(target_fragment["object_id"], join.binding)
            bindings[join.binding] = {
                "entity_id": join.entity_id,
                "object_id": target_fragment["object_id"],
                "table": target_table,
            }
            source_binding = bindings[join.from_binding]
            predicates = []
            for predicate in relationship.get("predicates") or []:
                left = predicate["left"]
                right = predicate["right"]
                physical_left = self.catalog_columns.get(left["column_id"])
                physical_right = self.catalog_columns.get(right["column_id"])
                if not physical_left or not physical_right:
                    raise StructuredDataError("RELATIONSHIP_COLUMN_INVALID", "关系映射字段已不存在")
                if left["object_id"] == source_binding["object_id"] and right["object_id"] == target_fragment["object_id"]:
                    source_column, target_column = physical_left[1]["name"], physical_right[1]["name"]
                elif right["object_id"] == source_binding["object_id"] and left["object_id"] == target_fragment["object_id"]:
                    source_column, target_column = physical_right[1]["name"], physical_left[1]["name"]
                else:
                    raise StructuredDataError("RELATIONSHIP_PATH_INVALID", "关系路径与实体绑定不一致")
                self.referenced_columns.update({left["column_id"], right["column_id"]})
                predicates.append(source_binding["table"].c[source_column] == target_table.c[target_column])
            condition = and_(*predicates)
            from_clause = from_clause.join(target_table, condition, isouter=join.join_type == "left")
        projections = []
        for item in ir.select:
            value = self._expression(item.expression, bindings)
            projections.append(value.label(item.alias) if item.alias else value)
        statement = select(*projections).select_from(from_clause)
        if ir.where:
            statement = statement.where(self._expression(ir.where, bindings))
        if ir.group_by:
            statement = statement.group_by(*(self._expression(item, bindings) for item in ir.group_by))
        if ir.having:
            statement = statement.having(self._expression(ir.having, bindings))
        if ir.order_by:
            orderings = []
            for item in ir.order_by:
                value = self._expression(item.expression, bindings)
                orderings.append(value.desc() if item.direction == "desc" else value.asc())
            statement = statement.order_by(*orderings)
        if ir.distinct:
            statement = statement.distinct()
        effective_limit = min(ir.limit, self.max_rows) if ir.limit else self.max_rows + (0 if nested else 1)
        statement = statement.limit(effective_limit)
        if ir.offset:
            statement = statement.offset(ir.offset)
        return statement

    def compile(self, ir: SemanticQueryIR) -> CompiledStructuredQuery:
        statement = self._compile_ir(ir)
        dialect_object = postgresql.dialect(paramstyle="named") if self.dialect == "postgresql" else mysql.dialect(paramstyle="named")
        compiled = statement.compile(dialect=dialect_object, compile_kwargs={"render_postcompile": True})
        parameters = dict(compiled.params)
        summary = {}
        for key, value in parameters.items():
            item: dict[str, Any] = {"type": type(value).__name__}
            if isinstance(value, (int, float, bool)) or value is None:
                item["value"] = value
            else:
                encoded = canonical_json(value).encode("utf-8")
                item["value_hash"] = hashlib.sha256(encoded).hexdigest()
                item["length"] = len(encoded)
            summary[key] = item
        query_fingerprint = fingerprint({
            "mapping_hash": self.version.mapping_hash,
            "schema_fingerprint": self.schema.schema_fingerprint,
            "dialect": self.dialect,
            "ir": ir.model_dump(),
        })
        return CompiledStructuredQuery(
            dialect=self.dialect,
            statement=statement,
            sql_template=str(compiled),
            parameters=parameters,
            parameter_summary=summary,
            referenced_objects=sorted(self.referenced_objects),
            referenced_columns=sorted(self.referenced_columns),
            mapping_version_id=self.version.id,
            schema_version_id=self.schema.id,
            query_fingerprint=query_fingerprint,
            max_rows=self.max_rows,
        )


class PostgreSQLCompiler(DeterministicCompiler):
    def __init__(self, version: SemanticMappingVersion, schema: DataSourceSchemaVersion, *, max_rows: int = 500):
        super().__init__("postgresql", version, schema, max_rows=max_rows)


class MySQLCompiler(DeterministicCompiler):
    def __init__(self, version: SemanticMappingVersion, schema: DataSourceSchemaVersion, *, max_rows: int = 500):
        super().__init__("mysql", version, schema, max_rows=max_rows)


def compile_structured_query(
    source: SourceConnector,
    version: SemanticMappingVersion,
    schema: DataSourceSchemaVersion,
    ir: SemanticQueryIR,
    *,
    max_rows: int = 500,
) -> CompiledStructuredQuery:
    dialect = str((source.config or {}).get("dialect") or "postgresql")
    compiler = PostgreSQLCompiler(version, schema, max_rows=max_rows) if dialect == "postgresql" else MySQLCompiler(version, schema, max_rows=max_rows)
    return compiler.compile(ir)


def execute_compiled_query(
    source: SourceConnector,
    compiled: CompiledStructuredQuery,
    *,
    timeout_seconds: int = 30,
    max_result_bytes: int = 5_000_000,
    run_id: str | None = None,
) -> dict[str, Any]:
    dialect, engine = create_source_engine(source, timeout_seconds=timeout_seconds)
    if dialect != compiled.dialect:
        engine.dispose()
        raise StructuredDataError("DIALECT_MISMATCH", "编译方言与数据源不一致")
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    result_bytes = 0
    truncated = False
    try:
        with readonly_connection(engine, dialect, timeout_seconds) as connection:
            if run_id:
                with _ACTIVE_QUERY_LOCK:
                    _ACTIVE_QUERY_CONNECTIONS[run_id] = connection
            try:
                result = connection.execute(compiled.statement)
                columns = list(result.keys())
                for raw in result.mappings():
                    row: dict[str, Any] = {}
                    for key, value in raw.items():
                        sensitivity, rule = sensitive_suggestion(str(key))
                        if sensitivity == "blocked":
                            row[key] = "***"
                        elif sensitivity == "masked":
                            row[key] = _mask(value, rule or "redact")
                        else:
                            row[key] = _json_value(value, max_text_length=2_000)
                    encoded = len(canonical_json(row).encode("utf-8"))
                    if len(rows) >= compiled.max_rows or result_bytes + encoded > max_result_bytes:
                        truncated = True
                        break
                    rows.append(row)
                    result_bytes += encoded
            finally:
                if run_id:
                    with _ACTIVE_QUERY_LOCK:
                        _ACTIVE_QUERY_CONNECTIONS.pop(run_id, None)
        return {
            "status": "succeeded",
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "result_bytes": result_bytes,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "query_time": datetime.now(timezone.utc).isoformat(),
            "warnings": ["结果已按安全上限截断"] if truncated else [],
        }
    except StructuredDataError:
        raise
    except Exception as exc:
        raise StructuredDataError("STRUCTURED_QUERY_FAILED", f"结构化查询执行失败：{type(exc).__name__}") from exc
    finally:
        engine.dispose()


def cancel_active_query(run_id: str) -> bool:
    with _ACTIVE_QUERY_LOCK:
        connection = _ACTIVE_QUERY_CONNECTIONS.get(run_id)
    if connection is None:
        return False
    try:
        driver_connection = connection.connection.driver_connection
        cancel = getattr(driver_connection, "cancel", None)
        if callable(cancel):
            cancel()
        else:
            driver_connection.close()
        return True
    except Exception:
        try:
            connection.invalidate()
            return True
        except Exception:
            return False
