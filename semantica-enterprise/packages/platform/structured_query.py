from __future__ import annotations

import hashlib
import json
import re
import time
import threading
from copy import deepcopy
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
WINDOW_FUNCTIONS = {
    "row_number", "rank", "dense_rank", "first_value", "last_value",
    "sum", "avg", "count", "min", "max",
}
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

    def normalize_expression(value: Any) -> Any:
        if isinstance(value, list):
            return [normalize_expression(item) for item in value]
        if not isinstance(value, dict):
            return value
        normalized = {key: normalize_expression(item) for key, item in value.items()}
        if normalized.get("kind") == "logical":
            operands = [item for item in normalized.get("operands") or [] if isinstance(item, dict)]
            if len(operands) == 1:
                return operands[0]
        if normalized.get("kind") == "binary":
            normalized["operator"] = {
                "eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
                "==": "=", "<>": "!=",
            }.get(normalized.get("operator"), normalized.get("operator"))
        if normalized.get("kind") == "function":
            function = str(normalized.get("function") or "").casefold()
            arguments = normalized.get("arguments") or []
            if function in {"divide", "ratio"} and len(arguments) == 2:
                return {
                    "kind": "binary",
                    "operator": "/",
                    "left": arguments[0],
                    "right": arguments[1],
                }
            if function in {"percent", "percentage"} and len(arguments) == 2:
                return {
                    "kind": "binary",
                    "operator": "*",
                    "left": {
                        "kind": "binary",
                        "operator": "/",
                        "left": arguments[0],
                        "right": arguments[1],
                    },
                    "right": {"kind": "literal", "value": 100},
                }
        if normalized.get("kind") == "exists" and normalized.get("query") is not None:
            relationship_fields = (
                normalized.get("relationship_id"),
                normalized.get("source_binding"),
                normalized.get("target_binding"),
                normalized.get("target_entity_id"),
            )
            nested = normalized.get("query") or {}
            # Providers sometimes emit both legal EXISTS variants at once.  A
            # one-hop nested query is exactly representable by the safer
            # relationship form: its WHERE predicates become target operands,
            # and physical correlation still comes only from the activated
            # relationship mapping.
            if all(relationship_fields) and not (nested.get("joins") or []):
                nested_binding = str((nested.get("from_entity") or {}).get("binding") or "")
                target_binding = str(normalized.get("target_binding") or "")

                def rebind(item: Any) -> Any:
                    if isinstance(item, list):
                        return [rebind(child) for child in item]
                    if not isinstance(item, dict):
                        return item
                    result = {key: rebind(child) for key, child in item.items()}
                    if result.get("kind") == "attribute" and result.get("binding") == nested_binding:
                        result["binding"] = target_binding
                    return result

                predicates = list(normalized.get("operands") or [])
                if isinstance(nested.get("where"), dict):
                    nested_where = rebind(nested["where"])
                    if nested_where.get("kind") == "logical" and nested_where.get("operator") == "and":
                        predicates.extend(nested_where.get("operands") or [])
                    else:
                        predicates.append(nested_where)
                normalized["operands"] = predicates
                normalized["query"] = None
        return normalized

    return {**raw, "plan": plan, "query_ir": normalize_expression(raw.get("query_ir"))}


def _align_generated_plan_scope(raw: Any, version: SemanticMappingVersion) -> Any:
    """Make the Plan explicitly declare every semantic binding used by strict IR.

    Providers occasionally construct a valid semantic Join but omit its ID or
    endpoint from the parallel Plan object.  Completing that declaration is a
    deterministic protocol normalization: unknown IDs still fail validation,
    and neither paths nor physical identifiers are invented here.
    """

    if not isinstance(raw, dict) or not isinstance(raw.get("plan"), dict) or not isinstance(raw.get("query_ir"), dict):
        return raw
    plan = dict(raw["plan"])
    query_ir = raw["query_ir"]
    entity_ids = list(plan.get("entity_ids") or [])
    relationship_ids = list(plan.get("relationship_ids") or [])

    def add_unique(values: list[str], value: Any) -> None:
        if isinstance(value, str) and value and value not in values:
            values.append(value)

    from_entity = query_ir.get("from_entity") or {}
    add_unique(entity_ids, from_entity.get("entity_id"))
    for join in query_ir.get("joins") or []:
        if not isinstance(join, dict):
            continue
        add_unique(entity_ids, join.get("entity_id"))
        add_unique(relationship_ids, join.get("relationship_id"))
    for expression in _raw_expression_nodes(query_ir.get("where")):
        if expression.get("kind") == "exists" and expression.get("relationship_id"):
            add_unique(relationship_ids, expression.get("relationship_id"))
            add_unique(entity_ids, expression.get("target_entity_id"))
    outputs = [dict(item) if isinstance(item, dict) else item for item in plan.get("outputs") or []]
    for index, projection in enumerate(query_ir.get("select") or []):
        if index >= len(outputs) or not isinstance(outputs[index], dict) or not isinstance(projection, dict):
            continue
        attribute_ids = list(outputs[index].get("attribute_ids") or [])
        for expression in _raw_expression_nodes(projection.get("expression")):
            if expression.get("kind") == "attribute":
                add_unique(attribute_ids, expression.get("attribute_id"))
        outputs[index]["attribute_ids"] = attribute_ids
    plan["outputs"] = outputs
    filters = list(plan.get("filters") or [])
    for candidate in _raw_filter_signatures(query_ir.get("where")):
        if candidate.get("attribute_id") and not any(_filter_matches(item, candidate) for item in filters):
            filters.append(candidate)
    plan["filters"] = filters
    group_by_attribute_ids = list(plan.get("group_by_attribute_ids") or [])
    for expression in query_ir.get("group_by") or []:
        for node in _raw_expression_nodes(expression):
            if node.get("kind") == "attribute":
                add_unique(group_by_attribute_ids, node.get("attribute_id"))
    plan["group_by_attribute_ids"] = group_by_attribute_ids
    relationship_index = {
        item.get("id"): item for item in (version.manifest or {}).get("relationships") or []
    }
    for relationship_id in relationship_ids:
        relationship = relationship_index.get(relationship_id) or {}
        add_unique(entity_ids, relationship.get("from_entity_id"))
        add_unique(entity_ids, relationship.get("to_entity_id"))
    plan["entity_ids"] = entity_ids
    plan["relationship_ids"] = relationship_ids
    return {**raw, "plan": plan}


def _repair_generated_relationship_endpoints(
    raw: Any,
    version: SemanticMappingVersion,
) -> Any:
    """Resolve a relationship's target entity from its activated endpoints.

    Models choose semantic relationships, but occasionally repeat the source
    entity in ``join.entity_id`` or choose the endpoint on the wrong side.  The
    relationship mapping already determines the only valid counterpart.  This
    repair never invents a path and leaves ambiguous/self relationships for the
    strict validator to reject.
    """

    if not isinstance(raw, dict) or not isinstance(raw.get("query_ir"), dict):
        return raw
    relationships = {
        item.get("id"): item
        for item in (version.manifest or {}).get("relationships") or []
        if isinstance(item, dict)
    }

    def repair_ir(query_ir: Any) -> Any:
        if not isinstance(query_ir, dict):
            return query_ir
        result = dict(query_ir)
        from_entity = dict(result.get("from_entity") or {})
        bindings: dict[str, str] = {}
        if from_entity.get("binding") and from_entity.get("entity_id"):
            bindings[str(from_entity["binding"])] = str(from_entity["entity_id"])
        joins: list[Any] = []
        for candidate in result.get("joins") or []:
            if not isinstance(candidate, dict):
                joins.append(candidate)
                continue
            join = dict(candidate)
            relationship = relationships.get(join.get("relationship_id")) or {}
            source_entity = bindings.get(str(join.get("from_binding") or ""))
            endpoints = (
                relationship.get("from_entity_id"),
                relationship.get("to_entity_id"),
            )
            if source_entity and source_entity in endpoints and endpoints[0] != endpoints[1]:
                join["entity_id"] = endpoints[1] if source_entity == endpoints[0] else endpoints[0]
            if join.get("binding") and join.get("entity_id"):
                bindings[str(join["binding"])] = str(join["entity_id"])
            joins.append(join)
        result["joins"] = joins

        def repair_expression(value: Any, scoped_bindings: dict[str, str] | None = None) -> Any:
            scoped_bindings = scoped_bindings or bindings
            if isinstance(value, list):
                return [repair_expression(item, scoped_bindings) for item in value]
            if not isinstance(value, dict):
                return value
            expression = dict(value)
            if expression.get("kind") == "exists" and expression.get("relationship_id"):
                relationship = relationships.get(expression.get("relationship_id")) or {}
                source_entity = scoped_bindings.get(str(expression.get("source_binding") or ""))
                endpoints = (
                    relationship.get("from_entity_id"),
                    relationship.get("to_entity_id"),
                )
                if source_entity and source_entity in endpoints and endpoints[0] != endpoints[1]:
                    expression["target_entity_id"] = (
                        endpoints[1] if source_entity == endpoints[0] else endpoints[0]
                    )
                # A relationship EXISTS introduces its target binding only for
                # its operands.  Nested relationship paths must be repaired in
                # that extended scope; using only the root bindings silently
                # rewrites valid nested filters onto the wrong entity.
                operand_bindings = dict(scoped_bindings)
                if expression.get("target_binding") and expression.get("target_entity_id"):
                    operand_bindings[str(expression["target_binding"])] = str(expression["target_entity_id"])
                expression["operands"] = repair_expression(
                    expression.get("operands") or [], operand_bindings
                )
            for key, item in list(expression.items()):
                if key == "operands" and expression.get("kind") == "exists":
                    continue
                if key == "query" and item is not None:
                    expression[key] = repair_ir(item)
                else:
                    expression[key] = repair_expression(item, scoped_bindings)
            return expression

        for key in ("select", "where", "group_by", "having", "order_by"):
            if key in result:
                result[key] = repair_expression(result.get(key))
        return result

    return {**raw, "query_ir": repair_ir(raw["query_ir"])}


def _question_ngrams(value: str) -> set[str]:
    """Return deterministic lexical features for short Chinese business labels."""
    features: set[str] = set()
    for token in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", value.casefold()):
        features.add(token)
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            for size in range(2, min(6, len(token)) + 1):
                features.update(token[index:index + size] for index in range(len(token) - size + 1))
    return features


def _attribute_question_score(
    question: str,
    attribute: dict[str, Any],
    entities: dict[str, dict[str, Any]],
) -> int:
    """Score a semantic attribute without looking at physical schema identifiers."""
    entity_label = str((entities.get(attribute.get("entity_id")) or {}).get("label") or "").strip()
    labels = [
        str(attribute.get("label") or "").strip(),
        *(str(item).strip() for item in attribute.get("aliases") or []),
    ]
    question_folded = question.casefold()
    descriptor = " ".join([entity_label, *labels])
    overlap = _question_ngrams(question) & _question_ngrams(descriptor)
    score = sum(len(item) ** 2 for item in overlap)
    if entity_label and entity_label.casefold() in question_folded:
        score += 400 + len(entity_label) * 20
    for label in labels:
        if label and label.casefold() in question_folded:
            score += 800 + len(label) * 20
    return score


def _disambiguate_generated_filters(
    raw: Any,
    version: SemanticMappingVersion,
    question: str,
) -> Any:
    """Bind homonymous filters to the business entity explicitly named by the user.

    A mapping may legitimately expose the same ontology property on several
    entities (for example supplier risk level and risk-event level).  The model
    may select a type-compatible but semantically different property.  When the
    question explicitly names one entity and that entity is already bound in the
    IR, prefer its homologous attribute.  This changes semantic IDs only and
    never invents a physical field, join, or SQL fragment.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("plan"), dict) or not isinstance(raw.get("query_ir"), dict):
        return raw
    manifest = version.manifest or {}
    entities = {item.get("id"): item for item in manifest.get("entities") or []}
    attributes = {item.get("id"): item for item in manifest.get("attributes") or []}
    bindings: dict[str, str] = {}
    query_ir = raw["query_ir"]
    for binding in [query_ir.get("from_entity") or {}, *(query_ir.get("joins") or [])]:
        if binding.get("entity_id") and binding.get("binding"):
            bindings[str(binding["entity_id"])] = str(binding["binding"])

    replacements: dict[str, tuple[str, str]] = {}
    filters: list[Any] = []
    for item in raw["plan"].get("filters") or []:
        if not isinstance(item, dict):
            filters.append(item)
            continue
        selected = attributes.get(item.get("attribute_id"))
        if not selected or not selected.get("ontology_term_id"):
            filters.append(dict(item))
            continue
        candidates = [
            candidate for candidate in attributes.values()
            if candidate.get("ontology_term_id") == selected.get("ontology_term_id")
            and candidate.get("semantic_type") == selected.get("semantic_type")
            and candidate.get("entity_id") in bindings
        ]
        ranked = sorted(
            ((candidate, _attribute_question_score(question, candidate, entities)) for candidate in candidates),
            key=lambda row: (-row[1], str(row[0].get("id"))),
        )
        selected_score = _attribute_question_score(question, selected, entities)
        best, best_score = ranked[0] if ranked else (selected, selected_score)
        best_entity_label = str((entities.get(best.get("entity_id")) or {}).get("label") or "")
        if (
            best.get("id") != selected.get("id")
            and best_score >= selected_score + 100
            and best_entity_label
            and best_entity_label.casefold() in question.casefold()
        ):
            replacement = dict(item)
            replacement["attribute_id"] = best["id"]
            filters.append(replacement)
            replacements[str(selected["id"])] = (str(best["id"]), bindings[str(best["entity_id"])])
        else:
            filters.append(dict(item))
    if not replacements:
        return raw

    def rewrite_expression(value: Any) -> Any:
        if isinstance(value, list):
            return [rewrite_expression(child) for child in value]
        if not isinstance(value, dict):
            return value
        rewritten = {key: rewrite_expression(child) for key, child in value.items()}
        replacement = replacements.get(str(rewritten.get("attribute_id")))
        if rewritten.get("kind") == "attribute" and replacement:
            rewritten["attribute_id"], rewritten["binding"] = replacement
        return rewritten

    plan = dict(raw["plan"])
    plan["filters"] = filters
    query_ir = dict(query_ir)
    query_ir["where"] = rewrite_expression(query_ir.get("where"))
    return {**raw, "plan": plan, "query_ir": query_ir}


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


def _raw_expression_nodes(expression: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(expression, dict):
        return
    if expression.get("kind"):
        yield expression
    for key in (
        "arguments", "operands", "options", "partition_by",
    ):
        for item in expression.get(key) or []:
            yield from _raw_expression_nodes(item)
    for key in ("left", "right", "expression", "lower", "upper", "else_expression"):
        yield from _raw_expression_nodes(expression.get(key))
    for branch in expression.get("whens") or []:
        if isinstance(branch, dict):
            yield from _raw_expression_nodes(branch.get("when"))
            yield from _raw_expression_nodes(branch.get("then"))


def _raw_filter_signatures(expression: Any) -> list[dict[str, Any]]:
    if not isinstance(expression, dict):
        return []
    kind = expression.get("kind")
    if kind in {"logical", "exists"}:
        return [
            item
            for operand in expression.get("operands") or []
            for item in _raw_filter_signatures(operand)
        ]
    if kind == "not":
        return _raw_filter_signatures(expression.get("expression"))
    left, right = expression.get("left"), expression.get("right")
    if kind == "binary" and isinstance(left, dict) and isinstance(right, dict):
        if left.get("kind") == "literal" and right.get("kind") == "attribute":
            left, right = right, left
        operators = {"=": "eq", "!=": "ne", "<>": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
        if left.get("kind") == "attribute" and right.get("kind") == "literal" and expression.get("operator") in operators:
            return [{
                "attribute_id": left.get("attribute_id"),
                "operator": operators[expression["operator"]],
                "value": right.get("value"),
                "upper": None,
            }]
    target = expression.get("expression")
    if kind == "between" and isinstance(target, dict):
        lower, upper = expression.get("lower"), expression.get("upper")
        if target.get("kind") == "attribute" and isinstance(lower, dict) and isinstance(upper, dict) and lower.get("kind") == upper.get("kind") == "literal":
            return [{"attribute_id": target.get("attribute_id"), "operator": "between", "value": lower.get("value"), "upper": upper.get("value")}]
    if kind == "in" and isinstance(target, dict) and target.get("kind") == "attribute":
        options = expression.get("options") or []
        if options and all(isinstance(item, dict) and item.get("kind") == "literal" for item in options):
            return [{"attribute_id": target.get("attribute_id"), "operator": "in", "value": [item.get("value") for item in options], "upper": None}]
    if kind == "is_null" and isinstance(target, dict) and target.get("kind") == "attribute":
        return [{"attribute_id": target.get("attribute_id"), "operator": "is_not_null" if expression.get("negated") else "is_null", "value": None, "upper": None}]
    return []


def _filter_matches(candidate: dict[str, Any], required: dict[str, Any]) -> bool:
    return all(candidate.get(key) == value for key, value in required.items())


def _contains_relationship_requirement(
    where: Any,
    requirement: dict[str, Any],
    source_binding: str,
) -> bool:
    expected_negated = requirement.get("quantifier", "exists") == "not_exists"
    for expression in _raw_expression_nodes(where):
        if (
            expression.get("kind") != "exists"
            or expression.get("query") is not None
            or expression.get("relationship_id") != requirement.get("relationship_id")
            or expression.get("source_binding") != source_binding
            or expression.get("target_entity_id") != requirement.get("target_entity_id")
            or bool(expression.get("negated")) != expected_negated
        ):
            continue
        signatures = _raw_filter_signatures(expression)
        if all(any(_filter_matches(candidate, item) for candidate in signatures) for item in requirement.get("filters") or []):
            return True
    return False


def _used_bindings(query_ir: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for node in _raw_expression_nodes(query_ir.get("where")):
        for key in ("binding", "source_binding", "target_binding"):
            if node.get(key):
                values.add(str(node[key]))
    for item in [query_ir.get("from_entity") or {}, *(query_ir.get("joins") or [])]:
        for key in ("binding", "from_binding"):
            if item.get(key):
                values.add(str(item[key]))
    return values


def _contract_binding(relationship_id: str, used: set[str]) -> str:
    stem = re.sub(r"[^A-Za-z0-9_]", "_", f"metric_{relationship_id}")[:56]
    if not stem or not stem[0].isalpha():
        stem = f"metric_{stem}"
    candidate = stem
    suffix = 1
    while candidate in used:
        suffix += 1
        candidate = f"{stem[:58]}_{suffix}"
    used.add(candidate)
    return candidate


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
    manifest = version.manifest or {}
    attributes = {item.get("id"): item for item in manifest.get("attributes") or []}
    relationships = {item.get("id"): item for item in manifest.get("relationships") or []}
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
    entity_ids = list(plan.get("entity_ids") or [])
    relationship_ids = list(plan.get("relationship_ids") or [])
    metric_contract = dict(plan.get("metric_contract") or {})
    base_entity_ids = list(metric_contract.get("base_entity_ids") or [])
    base_relationship_ids = list(metric_contract.get("base_relationship_ids") or [])
    used_bindings = _used_bindings(query_ir)
    for output in plan.get("outputs") or []:
        for attribute_id in output.get("attribute_ids") or []:
            attribute = attributes.get(attribute_id) or {}
            if attribute.get("default_aggregate") and not output.get("aggregate"):
                output["aggregate"] = attribute["default_aggregate"]
            definition = attribute.get("business_definition")
            if definition and definition not in evidence:
                evidence.append(definition)
            for required_filter in attribute.get("required_filters") or []:
                if any(_filter_matches(candidate, required_filter) for candidate in filters):
                    continue
                filters.append(dict(required_filter))
                binding = bindings.get(attribute.get("entity_id"))
                predicate = _metric_filter_expression(required_filter, binding) if binding else None
                if predicate:
                    where = predicate if where is None else {"kind": "logical", "operator": "and", "operands": [where, predicate]}
            source_entity_id = attribute.get("entity_id")
            source_binding = bindings.get(source_entity_id)
            for requirement in attribute.get("required_relationships") or []:
                relationship_id = requirement.get("relationship_id")
                target_entity_id = requirement.get("target_entity_id")
                if target_entity_id and target_entity_id not in entity_ids:
                    entity_ids.append(target_entity_id)
                if relationship_id and relationship_id not in relationship_ids:
                    relationship_ids.append(relationship_id)
                for entity_id in (source_entity_id, target_entity_id):
                    if entity_id and entity_id not in base_entity_ids:
                        base_entity_ids.append(entity_id)
                if relationship_id and relationship_id not in base_relationship_ids:
                    base_relationship_ids.append(relationship_id)
                description = requirement.get("description")
                if description and description not in evidence:
                    evidence.append(description)
                for required_filter in requirement.get("filters") or []:
                    if not any(_filter_matches(candidate, required_filter) for candidate in filters):
                        filters.append(dict(required_filter))
                if (
                    not source_binding
                    or relationship_id not in relationships
                    or _contains_relationship_requirement(where, requirement, source_binding)
                ):
                    continue
                target_binding = _contract_binding(str(relationship_id), used_bindings)
                predicates = [
                    predicate
                    for predicate in (
                        _metric_filter_expression(required_filter, target_binding)
                        for required_filter in requirement.get("filters") or []
                    )
                    if predicate is not None
                ]
                relational_exists = {
                    "kind": "exists",
                    "relationship_id": relationship_id,
                    "source_binding": source_binding,
                    "target_binding": target_binding,
                    "target_entity_id": target_entity_id,
                    "operands": predicates,
                    "negated": requirement.get("quantifier", "exists") == "not_exists",
                }
                where = relational_exists if where is None else {
                    "kind": "logical", "operator": "and", "operands": [where, relational_exists],
                }
    used_relationship_ids = {
        join.get("relationship_id")
        for join in query_ir.get("joins") or []
        if isinstance(join, dict) and join.get("relationship_id")
    } | {
        expression.get("relationship_id")
        for expression in _raw_expression_nodes(where)
        if expression.get("kind") == "exists" and expression.get("relationship_id")
    }
    for relationship_id in relationship_ids:
        if relationship_id not in used_relationship_ids:
            continue
        relationship = relationships.get(relationship_id) or {}
        required_filters = relationship.get("required_filters") or []
        if required_filters and relationship.get("description") and relationship["description"] not in evidence:
            evidence.append(relationship["description"])
        for required_filter in required_filters:
            if not any(_filter_matches(candidate, required_filter) for candidate in filters):
                filters.append(dict(required_filter))
            if any(
                _filter_matches(candidate, required_filter)
                for candidate in _raw_filter_signatures(where)
            ):
                continue
            filter_attribute = attributes.get(required_filter.get("attribute_id")) or {}
            binding = bindings.get(filter_attribute.get("entity_id"))
            predicate = _metric_filter_expression(required_filter, binding) if binding else None
            if predicate:
                where = predicate if where is None else {
                    "kind": "logical", "operator": "and", "operands": [where, predicate],
                }
    plan["filters"] = filters
    plan["evidence_constraints"] = evidence
    plan["entity_ids"] = entity_ids
    plan["relationship_ids"] = relationship_ids
    metric_contract["base_entity_ids"] = base_entity_ids
    metric_contract["base_relationship_ids"] = base_relationship_ids
    plan["metric_contract"] = metric_contract
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
        "business_guidance": manifest.get("notes") or [],
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
            "required_relationships": item.get("required_relationships") or [],
        } for item in attributes],
        "relationships": [{
            "id": item.get("id"),
            "label": item.get("label"),
            "description": item.get("description") or "",
            "from_entity_id": item.get("from_entity_id"),
            "to_entity_id": item.get("to_entity_id"),
            "cardinality": item.get("cardinality"),
            "required_filters": item.get("required_filters") or [],
        } for item in relationships],
        "derived_metrics": manifest.get("derived_metrics") or [],
        "record_sets": manifest.get("record_sets") or [],
        "governed_queries": [{
            "id": item.get("id"),
            "label": item.get("label"),
            "aliases": item.get("aliases") or [],
            "description": item.get("description") or "",
        } for item in manifest.get("governed_queries") or []],
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
        "decision", "result", "状态", "类型", "类别", "分类", "地区", "区域", "等级",
        "结论", "结果", "审批",
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


def _governed_semantic_match(question: str, definitions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match an explicitly governed business concept, never an answer value."""

    folded = question.casefold()
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for definition in definitions:
        labels = [definition.get("label"), *(definition.get("aliases") or [])]
        matches = [str(label).strip() for label in labels if str(label or "").strip() and str(label).strip().casefold() in folded]
        if matches:
            best = max(matches, key=len)
            scored.append((len(best), str(definition.get("id") or ""), definition))
    return sorted(scored, key=lambda item: (-item[0], item[1]))[0][2] if scored else None


def _semantic_filter_expression(required_filter: dict[str, Any], binding: str) -> dict[str, Any]:
    result = _metric_filter_expression(required_filter, binding)
    if result is None:
        raise StructuredDataError(
            "SEMANTIC_CONTRACT_INVALID",
            f"受管理语义口径包含不支持的筛选：{required_filter.get('operator')}",
        )
    return result


def _governed_relationship_expression(
    requirement: dict[str, Any],
    *,
    source_binding: str,
    suffix: str,
) -> dict[str, Any]:
    target_binding = re.sub(
        r"[^A-Za-z0-9_]", "_", f"governed_{requirement['relationship_id']}_{suffix}"
    )[:63]
    return {
        "kind": "exists",
        "relationship_id": requirement["relationship_id"],
        "source_binding": source_binding,
        "target_binding": target_binding,
        "target_entity_id": requirement["target_entity_id"],
        "operands": [
            _semantic_filter_expression(item, target_binding)
            for item in requirement.get("filters") or []
        ],
        "negated": requirement.get("quantifier", "exists") == "not_exists",
    }


def _logical_and(expressions: list[dict[str, Any]]) -> dict[str, Any] | None:
    expressions = [item for item in expressions if item]
    if not expressions:
        return None
    if len(expressions) == 1:
        return expressions[0]
    return {"kind": "logical", "operator": "and", "operands": expressions}


def _year_from_question(question: str, *, default: int | None = None) -> int | None:
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", question)
    return int(match.group(1)) if match else default


def _governed_metric_subquery(
    *,
    attribute: dict[str, Any],
    binding: str,
    suffix: str,
    time_attribute_id: str | None,
    period_attribute_id: str | None,
    period: int | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[str]]:
    filters = [dict(item) for item in attribute.get("required_filters") or []]
    if period is not None and time_attribute_id:
        filters.extend([
            {"attribute_id": time_attribute_id, "operator": "gte", "value": f"{period:04d}-01-01"},
            {"attribute_id": time_attribute_id, "operator": "lt", "value": f"{period + 1:04d}-01-01"},
        ])
    if period is not None and period_attribute_id:
        filters.append({"attribute_id": period_attribute_id, "operator": "eq", "value": period})
    relationships = [dict(item) for item in attribute.get("required_relationships") or []]
    predicates = [_semantic_filter_expression(item, binding) for item in filters]
    predicates.extend(
        _governed_relationship_expression(item, source_binding=binding, suffix=f"{suffix}_{index}")
        for index, item in enumerate(relationships, start=1)
    )
    query = {
        "from_entity": {"binding": binding, "entity_id": attribute["entity_id"]},
        "select": [{
            "alias": f"metric_{suffix}",
            "expression": {
                "kind": "aggregate",
                "function": attribute.get("default_aggregate") or "sum",
                "expression": {"kind": "attribute", "attribute_id": attribute["id"], "binding": binding},
            },
        }],
        "where": _logical_and(predicates),
    }
    entity_ids = [attribute["entity_id"], *(item["target_entity_id"] for item in relationships)]
    relationship_ids = [item["relationship_id"] for item in relationships]
    return query, filters, entity_ids, relationship_ids


def _ratio_expression(
    numerator: dict[str, Any],
    denominator: dict[str, Any],
    scale: float,
) -> dict[str, Any]:
    return {
        "kind": "case",
        "whens": [{
            "when": {
                "kind": "binary", "operator": "!=",
                "left": denominator, "right": {"kind": "literal", "value": 0},
            },
            "then": {
                "kind": "binary", "operator": "/",
                "left": {
                    "kind": "binary", "operator": "*",
                    "left": numerator, "right": {"kind": "literal", "value": scale},
                },
                "right": denominator,
            },
        }],
        "else_expression": {"kind": "literal", "value": None},
    }


def _governed_derived_metric_plan(
    question: str,
    version: SemanticMappingVersion,
) -> dict[str, Any] | None:
    """Compile an activated derived-metric definition into strict semantic IR.

    The mapping supplies semantic IDs and business grain. Literal answers and
    physical schema identifiers are intentionally impossible in this contract.
    """

    manifest = version.manifest or {}
    definition = _governed_semantic_match(question, manifest.get("derived_metrics") or [])
    if definition is None:
        return None
    attributes = {item.get("id"): item for item in manifest.get("attributes") or []}
    relationships = {item.get("id"): item for item in manifest.get("relationships") or []}
    numerator = attributes.get(definition.get("numerator_attribute_id"))
    denominator = attributes.get(definition.get("denominator_attribute_id"))
    if not numerator or not denominator:
        raise StructuredDataError("SEMANTIC_CONTRACT_INVALID", "派生指标引用了未知的分子或分母")
    period = _year_from_question(question, default=definition.get("default_period"))
    if period is None and (
        definition.get("numerator_time_attribute_id")
        or definition.get("denominator_period_attribute_id")
    ):
        raise StructuredDataError("SEMANTIC_PERIOD_REQUIRED", "该指标需要明确统计年份")
    scale = float(definition.get("scale") or 100)
    dimensions = definition.get("dimensions") or []
    dimension = next(
        (
            item for item in dimensions
            if any(
                str(alias).strip() and str(alias).strip().casefold() in question.casefold()
                for alias in [item.get("label"), *(item.get("aliases") or [])]
            )
        ),
        None,
    )
    definition_request = "分子" in question and "分母" in question

    actual_query, actual_filters, actual_entities, actual_relationships = _governed_metric_subquery(
        attribute=numerator,
        binding="actual_metric",
        suffix="actual",
        time_attribute_id=definition.get("numerator_time_attribute_id"),
        period_attribute_id=None,
        period=period,
    )
    target_query, target_filters, target_entities, target_relationships = _governed_metric_subquery(
        attribute=denominator,
        binding="target_metric",
        suffix="target",
        time_attribute_id=None,
        period_attribute_id=definition.get("denominator_period_attribute_id"),
        period=period,
    )
    all_filters = [
        *actual_filters,
        *target_filters,
        *(
            dict(item)
            for requirement in numerator.get("required_relationships") or []
            for item in requirement.get("filters") or []
        ),
        *(
            dict(item)
            for requirement in denominator.get("required_relationships") or []
            for item in requirement.get("filters") or []
        ),
    ]
    entity_ids = list(dict.fromkeys([*actual_entities, *target_entities]))
    relationship_ids = list(dict.fromkeys([*actual_relationships, *target_relationships]))
    evidence = [
        item for item in (
            numerator.get("business_definition"),
            denominator.get("business_definition"),
            definition.get("description"),
        ) if item
    ]

    if dimension is None:
        # Each scalar expression gets a distinct binding namespace so strict
        # scope validation and correlated EXISTS auditing remain unambiguous.
        def scalar(attribute: dict[str, Any], prefix: str) -> dict[str, Any]:
            query, _, _, _ = _governed_metric_subquery(
                attribute=attribute,
                binding=f"{prefix}_metric",
                suffix=prefix,
                time_attribute_id=(definition.get("numerator_time_attribute_id") if attribute is numerator else None),
                period_attribute_id=(definition.get("denominator_period_attribute_id") if attribute is denominator else None),
                period=period,
            )
            return {"kind": "subquery", "query": query}

        projections: list[dict[str, Any]]
        outputs: list[dict[str, Any]]
        if definition_request:
            numerator_name = definition.get("numerator_display_label") or numerator.get("label")
            denominator_name = definition.get("denominator_display_label") or denominator.get("label")
            numerator_label = f"{period}年{numerator_name}" if period else str(numerator_name)
            denominator_label = f"{period}年{denominator_name}" if period else str(denominator_name)
            projections = [
                {"alias": "numerator_label", "expression": {"kind": "literal", "value": numerator_label},},
                {"alias": "numerator", "expression": scalar(numerator, "definition_actual")},
                {"alias": "denominator_label", "expression": {"kind": "literal", "value": denominator_label},},
                {"alias": "denominator", "expression": scalar(denominator, "definition_target")},
            ]
            outputs = [
                {"position": 1, "label": "分子名称", "kind": "derived", "attribute_ids": []},
                {"position": 2, "label": "分子", "kind": "metric", "attribute_ids": [numerator["id"]], "aggregate": numerator.get("default_aggregate") or "sum"},
                {"position": 3, "label": "分母名称", "kind": "derived", "attribute_ids": []},
                {"position": 4, "label": "分母", "kind": "metric", "attribute_ids": [denominator["id"]], "aggregate": denominator.get("default_aggregate") or "sum"},
            ]
        else:
            actual_value = scalar(numerator, "output_actual")
            target_value = scalar(denominator, "output_target")
            percent_actual = scalar(numerator, "ratio_actual")
            percent_target = scalar(denominator, "ratio_target")
            projections = [
                {"alias": "numerator", "expression": actual_value},
                {"alias": "denominator", "expression": target_value},
                {"alias": "percent", "expression": _ratio_expression(percent_actual, percent_target, scale)},
            ]
            outputs = [
                {"position": 1, "label": "分子", "kind": "metric", "attribute_ids": [numerator["id"]], "aggregate": numerator.get("default_aggregate") or "sum"},
                {"position": 2, "label": "分母", "kind": "metric", "attribute_ids": [denominator["id"]], "aggregate": denominator.get("default_aggregate") or "sum"},
                {"position": 3, "label": definition["label"], "kind": "derived", "attribute_ids": [numerator["id"], denominator["id"]], "aggregate": "sum"},
            ]
        query_ir = {
            "from_entity": {"binding": "metric_scope", "entity_id": numerator["entity_id"]},
            "select": projections,
            "limit": 1,
        }
        expected_cardinality = "single_row"
        result_grain = "集团汇总"
    else:
        dimension_entity_id = dimension.get("entity_id")
        dimension_attribute_id = dimension.get("attribute_id")
        dimension_attribute = attributes.get(dimension_attribute_id)
        numerator_relationship = relationships.get(dimension.get("numerator_relationship_id"))
        denominator_relationship = relationships.get(dimension.get("denominator_relationship_id"))
        if not all((dimension_entity_id, dimension_attribute, numerator_relationship, denominator_relationship)):
            raise StructuredDataError("SEMANTIC_CONTRACT_INVALID", "派生指标分组维度配置不完整")
        entity_ids = list(dict.fromkeys([
            dimension_entity_id, numerator["entity_id"], denominator["entity_id"], *entity_ids,
        ]))
        relationship_ids = list(dict.fromkeys([
            dimension["numerator_relationship_id"],
            dimension["denominator_relationship_id"],
            *relationship_ids,
        ]))
        actual_binding, target_binding, dimension_binding = "actual", "target", "dimension"
        predicates = [
            *(_semantic_filter_expression(item, actual_binding) for item in actual_filters),
            *(_semantic_filter_expression(item, target_binding) for item in target_filters),
            *(
                _governed_relationship_expression(item, source_binding=actual_binding, suffix=f"group_{index}")
                for index, item in enumerate(numerator.get("required_relationships") or [], start=1)
            ),
        ]
        actual_sum = {
            "kind": "aggregate", "function": numerator.get("default_aggregate") or "sum",
            "expression": {"kind": "attribute", "attribute_id": numerator["id"], "binding": actual_binding},
        }
        # One target is unique per dimension and period. DISTINCT prevents the
        # target from multiplying when the numerator has multiple records.
        target_sum = {
            "kind": "aggregate", "function": denominator.get("default_aggregate") or "sum", "distinct": True,
            "expression": {"kind": "attribute", "attribute_id": denominator["id"], "binding": target_binding},
        }
        query_ir = {
            "from_entity": {"binding": dimension_binding, "entity_id": dimension_entity_id},
            "joins": [
                {"binding": actual_binding, "entity_id": numerator["entity_id"], "relationship_id": dimension["numerator_relationship_id"], "from_binding": dimension_binding, "join_type": "inner"},
                {"binding": target_binding, "entity_id": denominator["entity_id"], "relationship_id": dimension["denominator_relationship_id"], "from_binding": dimension_binding, "join_type": "inner"},
            ],
            "select": [
                {"alias": "org_unit", "expression": {"kind": "attribute", "attribute_id": dimension_attribute_id, "binding": dimension_binding}},
                {"alias": "actual", "expression": actual_sum},
                {"alias": "target", "expression": target_sum},
                {"alias": "percent", "expression": _ratio_expression(actual_sum, target_sum, scale)},
            ],
            "where": _logical_and(predicates),
            "group_by": [{"kind": "attribute", "attribute_id": dimension_attribute_id, "binding": dimension_binding}],
            "order_by": [{"expression": actual_sum, "direction": "desc"}],
        }
        outputs = [
            {"position": 1, "label": dimension.get("label") or "分组", "kind": "attribute", "attribute_ids": [dimension_attribute_id]},
            {"position": 2, "label": "实际值", "kind": "metric", "attribute_ids": [numerator["id"]], "aggregate": numerator.get("default_aggregate") or "sum"},
            {"position": 3, "label": "目标值", "kind": "metric", "attribute_ids": [denominator["id"]], "aggregate": denominator.get("default_aggregate") or "sum"},
            {"position": 4, "label": definition["label"], "kind": "derived", "attribute_ids": [numerator["id"], denominator["id"]], "aggregate": "sum"},
        ]
        expected_cardinality = "multiple_rows"
        result_grain = dimension.get("label") or "分组"

    return {
        "plan": {
            "original_question": question,
            "intent": f"按已激活语义口径计算{definition['label']}",
            "entity_ids": entity_ids,
            "relationship_ids": relationship_ids,
            "outputs": outputs,
            "filters": all_filters,
            "group_by_attribute_ids": ([dimension.get("attribute_id")] if dimension else []),
            "expected_cardinality": expected_cardinality,
            "result_grain": result_grain,
            "numerator": numerator.get("label") or numerator["id"],
            "denominator": denominator.get("label") or denominator["id"],
            "time_range": str(period or ""),
            "null_policy": "分母为零时返回空值",
            "calculation_steps": [
                {"step_id": "numerator", "kind": "aggregate", "operation": "aggregate", "entity_ids": [numerator["entity_id"]], "attribute_ids": [numerator["id"]]},
                {"step_id": "denominator", "kind": "aggregate", "operation": "aggregate", "entity_ids": [denominator["entity_id"]], "attribute_ids": [denominator["id"]]},
                {"step_id": "ratio", "kind": "derive", "operation": "divide_and_scale", "input_step_ids": ["numerator", "denominator"], "attribute_ids": [numerator["id"], denominator["id"]]},
            ],
            "result_step_id": "ratio",
            "evidence_constraints": evidence,
            "metric_contract": {
                "kind": "percentage", "numerator": numerator["id"], "denominator": denominator["id"],
                "scale": scale, "aggregation_grain": result_grain,
                "base_entity_ids": entity_ids, "base_relationship_ids": relationship_ids,
            },
        },
        "query_ir": query_ir,
    }


def _governed_record_set_plan(
    question: str,
    version: SemanticMappingVersion,
) -> dict[str, Any] | None:
    manifest = version.manifest or {}
    definition = _governed_semantic_match(question, manifest.get("record_sets") or [])
    if definition is None:
        return None
    attributes = {item.get("id"): item for item in manifest.get("attributes") or []}
    identity = attributes.get(definition.get("identity_attribute_id"))
    if identity is None or identity.get("entity_id") != definition.get("base_entity_id"):
        raise StructuredDataError("SEMANTIC_CONTRACT_INVALID", "受管理记录集缺少有效的稳定标识")
    binding = "record"
    filters = [dict(item) for item in definition.get("filters") or []]
    relationships = [dict(item) for item in definition.get("relationship_constraints") or []]
    predicates = [_semantic_filter_expression(item, binding) for item in filters]
    predicates.extend(
        _governed_relationship_expression(item, source_binding=binding, suffix=f"record_{index}")
        for index, item in enumerate(relationships, start=1)
    )
    entity_ids = list(dict.fromkeys([
        definition["base_entity_id"], *(item["target_entity_id"] for item in relationships),
    ]))
    relationship_ids = list(dict.fromkeys(item["relationship_id"] for item in relationships))
    filters.extend(
        dict(item)
        for requirement in relationships
        for item in requirement.get("filters") or []
    )
    return {
        "plan": {
            "original_question": question,
            "intent": f"统计受管理业务集合：{definition['label']}",
            "entity_ids": entity_ids,
            "relationship_ids": relationship_ids,
            "outputs": [{
                "position": 1, "label": "记录数量", "kind": "metric",
                "attribute_ids": [identity["id"]], "aggregate": "count",
            }],
            "filters": filters,
            "distinct": True,
            "expected_cardinality": "single_value",
            "result_grain": definition.get("label") or definition["id"],
            "distinct_policy": f"按 {identity.get('label') or identity['id']} 去重",
            "evidence_constraints": [definition.get("description") or definition["label"]],
            "metric_contract": {
                "kind": "count", "aggregation_grain": definition.get("label") or definition["id"],
                "distinct_policy": f"按 {identity['id']} 去重",
                "base_entity_ids": entity_ids, "base_relationship_ids": relationship_ids,
            },
        },
        "query_ir": {
            "from_entity": {"binding": binding, "entity_id": definition["base_entity_id"]},
            "select": [{
                "alias": "count",
                "expression": {
                    "kind": "aggregate", "function": "count", "distinct": True,
                    "expression": {"kind": "attribute", "attribute_id": identity["id"], "binding": binding},
                },
            }],
            "where": _logical_and(predicates),
        },
    }


def _replace_governed_year(value: Any, default_year: int, requested_year: int) -> Any:
    """Replace the declared period in a governed plan without touching IDs.

    Only literal values and descriptive strings containing the standalone
    configured year are affected.  The template never contains physical
    identifiers or SQL, and the materialized result is validated again before
    compilation.
    """

    if isinstance(value, list):
        return [_replace_governed_year(item, default_year, requested_year) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_governed_year(item, default_year, requested_year)
            for key, item in value.items()
        }
    if isinstance(value, int) and not isinstance(value, bool) and value == default_year:
        return requested_year
    if isinstance(value, str):
        return re.sub(
            rf"(?<!\d){re.escape(str(default_year))}(?!\d)",
            str(requested_year),
            value,
        )
    return value


def _governed_query_plan(
    question: str,
    version: SemanticMappingVersion,
) -> dict[str, Any] | None:
    """Materialize an activated, SQL-free semantic query definition."""

    definition = _governed_semantic_match(
        question, (version.manifest or {}).get("governed_queries") or []
    )
    if definition is None:
        return None
    raw = {
        "plan": deepcopy(definition.get("plan") or {}),
        "query_ir": deepcopy(definition.get("query_ir") or {}),
    }
    default_year = definition.get("default_year")
    requested_year = _year_from_question(question, default=default_year)
    if default_year is not None and requested_year is not None and requested_year != default_year:
        raw = _replace_governed_year(raw, int(default_year), int(requested_year))
    raw["plan"]["original_question"] = question
    raw["plan"]["intent"] = definition.get("label") or raw["plan"].get("intent")
    return raw


def _governed_semantic_plan(question: str, version: SemanticMappingVersion) -> dict[str, Any] | None:
    return (
        _governed_query_plan(question, version)
        or _governed_derived_metric_plan(question, version)
        or _governed_record_set_plan(question, version)
    )


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
除法和比例必须使用 binary 的 / 与 *，禁止创建 ratio、divide、percent 或 percentage 函数。logical 必须含至少两个 operands；只有一个条件时直接使用该条件。
EXISTS 有且只有两种形式：关系 EXISTS 使用 relationship_id/source_binding/target_binding/target_entity_id/operands 且 query 必须为空；子查询 EXISTS 使用 query 且所有关系字段必须为空。
同比、环比、比例、排名必须在 plan.calculation_steps 中明确计算步骤；简单汇总可以不填 calculation_steps。
如果属性提供 allowed_values，Plan 和 IR 的过滤值必须使用其中的真实值；不要翻译或改写数据库枚举值。
如果指标提供 default_aggregate、business_definition、required_filters 或 required_relationships，必须严格采用该业务口径；平台会在受信边界自动注入固定筛选和去重安全的关联 EXISTS 约束。对 required_relationships 不要自行在 IR joins 中加入目标实体，也不要手工生成该 EXISTS，否则可能导致重复累加；不能以用户未明确说明为由省略。
关系 description 是受管理的业务路径约束。询问“影响、依赖、适用”等关系时，必须沿明确表达该业务含义的关系路径；主体归属、历史订单或其他可连接路径不能替代目标业务关系。
语义目录 business_guidance 是已激活映射的一部分，涉及相应业务问题时必须使用其中指定的关系路径，不得改走其他可连接路径。
关系的 required_filters 是该关系自身的有效范围；平台会在受信边界强制注入，不能省略或改写。
Plan.entity_ids 必须包含 IR 主实体和所有 Join 实体；Plan.relationship_ids 必须包含 IR 使用的每一条关系，并同时包含这些关系的两个端点实体。
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
    def normalize_generated(raw_value: Any) -> Any:
        return _apply_metric_contracts(
            _disambiguate_generated_filters(
                _align_generated_plan_scope(
                    _repair_generated_relationship_endpoints(
                        _canonicalize_generated_plan(raw_value), version,
                    ),
                    version,
                ),
                version,
                question,
            ),
            version,
        )

    governed = _governed_semantic_plan(question, version)
    # Governed definitions are already strict semantic Plan/IR stored in the
    # activated mapping.  Provider-repair heuristics (especially homonymous
    # attribute disambiguation) must never rewrite this administrator-owned
    # contract.  It is still validated below and compiled through the exact
    # same deterministic safety boundary.
    raw = governed if governed is not None else normalize_generated(generator(prompt))
    validation_error: Exception | None = None
    for attempt in range(3):
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
        if attempt < 2 and governed is None:
            repair_prompt = f"""上一次结构化查询计划不符合传神智库严格协议。只输出修正后的 JSON 对象，键必须是 plan 和 query_ir，不要解释、不要 Markdown、不要 SQL。
校验错误：{str(validation_error)[:6000]}
上一次输出：{json.dumps(raw, ensure_ascii=False)[:14000]}
严格 Schema：{json.dumps(schema_contract, ensure_ascii=False)}
语义目录：{json.dumps(catalog, ensure_ascii=False)}
原始问题：{question}
指标的 required_relationships 由平台注入为关联 EXISTS，不要在 IR joins 或 IR where 中手工重复实现。
除法和比例只能用 binary / 与 *，不能使用 ratio、divide、percent 或 percentage 函数。logical 只能用于两个及以上 operands；单个条件直接输出该条件。
关系 EXISTS 与子查询 EXISTS 不能混用字段：关系形式不得带 query，子查询形式不得带 relationship_id/source_binding/target_binding/target_entity_id。
修复时保持关系 description 指定的业务路径；不能用主体归属、历史订单等可连接但语义不同的路径替代“影响、依赖、适用”等关系。
必须遵守语义目录 business_guidance 中与问题相符的受控关系路径。
Plan.entity_ids 必须覆盖 IR 主实体和所有 Join 实体；Plan.relationship_ids 必须覆盖 IR 的全部关系及其两个端点实体。
必须使用 Schema 中的原字段名，删除所有 extra 字段。"""
            raw = normalize_generated(generator(repair_prompt))
        else:
            break
    raise StructuredDataError(
        "SEMANTIC_PLANNER_INVALID",
        f"模型查询计划多次未通过严格 Schema：{validation_error}",
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
            required_relationships = attribute.get("required_relationships") or []
            if not attribute.get("is_measure") and not required_filters and not required_relationships:
                continue
            default_aggregate = attribute.get("default_aggregate")
            if default_aggregate and output.aggregate != default_aggregate:
                errors.append(f"指标 {attribute.get('label') or attribute_id} 必须使用 {default_aggregate} 聚合")
            for required_filter in required_filters:
                if not any(_filter_matches(candidate, required_filter) for candidate in plan_filters):
                    errors.append(f"指标 {attribute.get('label') or attribute_id} 缺少固定口径筛选：{required_filter.get('attribute_id')}")
            for requirement in required_relationships:
                relationship_id = requirement.get("relationship_id")
                target_entity_id = requirement.get("target_entity_id")
                if relationship_id not in plan.relationship_ids:
                    errors.append(f"指标 {attribute.get('label') or attribute_id} 缺少固定口径关系：{relationship_id}")
                if target_entity_id not in plan.entity_ids:
                    errors.append(f"指标 {attribute.get('label') or attribute_id} 缺少关联口径实体：{target_entity_id}")
                for required_filter in requirement.get("filters") or []:
                    if not any(_filter_matches(candidate, required_filter) for candidate in plan_filters):
                        errors.append(
                            f"指标 {attribute.get('label') or attribute_id} 缺少关联口径筛选：{required_filter.get('attribute_id')}"
                        )
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
    if expression.kind in {"logical", "exists"} and expression.operands:
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
    relationship_exists: list[QueryExpression] = []
    for expression in _walk_ir_expressions(ir):
        if expression.kind != "exists" or expression.relationship_id is None:
            continue
        relationship_exists.append(expression)
        join_count += 1
        relationship = relationships.get(expression.relationship_id)
        if relationship is None:
            errors.append(f"IR 关联 EXISTS 引用了未知关系：{expression.relationship_id}")
            continue
        if expression.relationship_id not in plan.relationship_ids:
            errors.append(f"IR 关联 EXISTS 引用了计划外关系：{expression.relationship_id}")
        source_entity_id = binding_entities.get(str(expression.source_binding))
        if source_entity_id is None:
            errors.append(f"IR 关联 EXISTS 的来源绑定不存在：{expression.source_binding}")
        expected_endpoints = {relationship["from_entity_id"], relationship["to_entity_id"]}
        if {source_entity_id, expression.target_entity_id} != expected_endpoints:
            errors.append(f"IR 关联 EXISTS 的实体与关系端点不匹配：{expression.relationship_id}")
        if expression.target_entity_id not in plan.entity_ids:
            errors.append(f"IR 关联 EXISTS 的目标实体不在计划中：{expression.target_entity_id}")
        target_binding = str(expression.target_binding)
        if target_binding in binding_entities:
            errors.append(f"IR 关联 EXISTS 重复使用了实体绑定：{target_binding}")
        else:
            binding_entities[target_binding] = str(expression.target_entity_id)
    allowed_attributes = _plan_attribute_ids(plan)
    ir_filters = [item for scope in _walk_ir_nodes(ir) for item in _ir_filter_signatures(scope.where)]
    ir_join_relationships = {
        join.relationship_id for scope in _walk_ir_nodes(ir) for join in scope.joins
    }
    used_relationship_ids = ir_join_relationships | {
        str(expression.relationship_id) for expression in relationship_exists
    }
    for relationship_id in used_relationship_ids:
        relationship = relationships.get(relationship_id) or {}
        for required_filter in relationship.get("required_filters") or []:
            if not any(_filter_matches(item.model_dump(), required_filter) for item in plan.filters):
                errors.append(
                    f"关系 {relationship.get('label') or relationship_id} 缺少固定口径筛选："
                    f"{required_filter.get('attribute_id')}"
                )
            if not any(_filter_matches(item, required_filter) for item in ir_filters):
                errors.append(
                    f"IR 缺少关系固定口径筛选：{required_filter.get('attribute_id')}"
                )
    for planned_filter in plan.filters:
        expected = planned_filter.model_dump()
        if not any(all(candidate.get(key) == value for key, value in expected.items()) for candidate in ir_filters):
            errors.append(f"IR 缺少查询计划声明的筛选：{planned_filter.attribute_id}")
    for output in plan.outputs:
        for attribute_id in output.attribute_ids:
            attribute = attributes.get(attribute_id) or {}
            for requirement in attribute.get("required_relationships") or []:
                if requirement.get("relationship_id") in ir_join_relationships:
                    errors.append(
                        f"指标 {attribute.get('label') or attribute_id} 的固定关联口径必须使用 EXISTS，不能直接 Join：{requirement.get('relationship_id')}"
                    )
                expected_negated = requirement.get("quantifier", "exists") == "not_exists"
                matched = False
                for expression in relationship_exists:
                    if (
                        expression.relationship_id != requirement.get("relationship_id")
                        or expression.target_entity_id != requirement.get("target_entity_id")
                        or expression.negated != expected_negated
                        or binding_entities.get(str(expression.source_binding)) != attribute.get("entity_id")
                    ):
                        continue
                    signatures = _ir_filter_signatures(expression)
                    if all(
                        any(_filter_matches(candidate, required_filter) for candidate in signatures)
                        for required_filter in requirement.get("filters") or []
                    ):
                        matched = True
                        break
                if not matched:
                    errors.append(
                        f"指标 {attribute.get('label') or attribute_id} 缺少可审计的关联 EXISTS 口径：{requirement.get('relationship_id')}"
                    )
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

    def _relationship_exists(
        self,
        expression: QueryExpression,
        bindings: dict[str, dict[str, Any]],
    ):
        """Compile a correlated EXISTS from semantic relationship metadata.

        The caller supplies no table names, columns or SQL.  Both correlation
        columns come from the activated relationship mapping and all values are
        compiled through the regular parameter-binding path.
        """
        relationship = self.relationships.get(str(expression.relationship_id))
        if relationship is None:
            raise StructuredDataError(
                "UNKNOWN_RELATIONSHIP",
                f"未知业务关系：{expression.relationship_id}",
            )
        source_binding = bindings.get(str(expression.source_binding))
        if source_binding is None:
            raise StructuredDataError(
                "RELATIONSHIP_SOURCE_BINDING_INVALID",
                f"关联 EXISTS 来源绑定不存在：{expression.source_binding}",
            )
        target_entity_id = str(expression.target_entity_id)
        endpoints = {relationship.get("from_entity_id"), relationship.get("to_entity_id")}
        if {source_binding["entity_id"], target_entity_id} != endpoints:
            raise StructuredDataError(
                "RELATIONSHIP_PATH_INVALID",
                f"关联 EXISTS 实体与关系端点不匹配：{expression.relationship_id}",
            )
        target_fragment = self._primary_fragment(target_entity_id)
        target_table = self._physical_table(target_fragment["object_id"], str(expression.target_binding))
        related_bindings = dict(bindings)
        if str(expression.target_binding) in related_bindings:
            raise StructuredDataError(
                "RELATIONSHIP_TARGET_BINDING_INVALID",
                f"关联 EXISTS 目标绑定重复：{expression.target_binding}",
            )
        related_bindings[str(expression.target_binding)] = {
            "entity_id": target_entity_id,
            "object_id": target_fragment["object_id"],
            "table": target_table,
        }
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
                raise StructuredDataError("RELATIONSHIP_PATH_INVALID", "关系路径与关联 EXISTS 绑定不一致")
            self.referenced_columns.update({left["column_id"], right["column_id"]})
            predicates.append(source_binding["table"].c[source_column] == target_table.c[target_column])
        predicates.extend(self._expression(item, related_bindings) for item in expression.operands)
        # Correlate every binding that belongs to the enclosing query.  EXISTS
        # operands may compare the related row with a second outer binding
        # (for example, an anti-join that selects the latest order for each
        # already-joined supplier).  Correlating only ``source_binding`` makes
        # SQLAlchemy pull that second outer table into the subquery and silently
        # changes the business meaning from "newer than this row" to "newer
        # than any row".
        correlated_tables = []
        seen_table_ids: set[int] = set()
        for binding in bindings.values():
            table = binding["table"]
            table_id = id(table)
            if table_id not in seen_table_ids:
                seen_table_ids.add(table_id)
                correlated_tables.append(table)
        statement = (
            select(literal_column("1"))
            .select_from(target_table)
            .where(and_(*predicates))
            .correlate(*correlated_tables)
        )
        return exists(statement)

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
            result = (
                self._relationship_exists(expression, bindings)
                if expression.relationship_id is not None
                else exists(self._compile_ir(expression.query, nested=True))
            )
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
