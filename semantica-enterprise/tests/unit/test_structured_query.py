from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from apps.api.structured_schemas import SemanticQueryIR, SemanticQueryPlan
from apps.api.agent_internal import _agent_citation_contract
from packages.platform.models import DataSourceSchemaVersion, SemanticMappingVersion, SourceConnector
from packages.platform.structured_query import (
    _canonicalize_generated_plan,
    _repair_generated_relationship_endpoints,
    apply_activated_metric_contracts,
    compile_structured_query,
    generate_semantic_plan_ir,
    semantic_catalog_for_planner,
    validate_ir,
    validate_plan,
)


def _context(dialect: str = "postgresql"):
    source = SourceConnector(
        id="source", tenant_id="tenant", space_id="space", name="经营库",
        source_type="database", config={"dialect": dialect},
    )
    schema_name = "public" if dialect == "postgresql" else None
    object_id = f"{schema_name}.sales" if schema_name else "sales"
    schema = DataSourceSchemaVersion(
        id="schema", tenant_id="tenant", space_id="space", source_id="source",
        version_number=1, schema_fingerprint="a" * 64, status="current",
        catalog={"objects": [{
            "id": object_id, "schema": schema_name, "name": "sales", "primary_key": ["id"],
            "columns": [
                {"id": f"{object_id}.id", "name": "id"},
                {"id": f"{object_id}.amount", "name": "amount"},
                {"id": f"{object_id}.year", "name": "year"},
            ],
        }]},
    )
    manifest = {
        "entities": [{
            "id": "sale", "fragments": [{"id": "sale-main", "object_id": object_id, "role": "primary"}],
        }],
        "attributes": [
            {"id": "sale-amount", "entity_id": "sale", "fragment_id": "sale-main", "column_id": f"{object_id}.amount"},
            {"id": "sale-year", "entity_id": "sale", "fragment_id": "sale-main", "column_id": f"{object_id}.year"},
        ],
        "relationships": [],
    }
    version = SemanticMappingVersion(
        id="mapping-version", tenant_id="tenant", space_id="space", source_id="source",
        mapping_set_id="mapping", schema_version_id="schema", schema_fingerprint="a" * 64,
        mapping_hash="b" * 64, version_number=1, manifest=manifest, status="active", created_by="user",
    )
    plan = SemanticQueryPlan.model_validate({
        "original_question": "2026 年销售总额是多少？",
        "intent": "计算 2026 年销售总额",
        "entity_ids": ["sale"],
        "outputs": [{"position": 1, "label": "销售总额", "kind": "metric", "attribute_ids": ["sale-amount"], "aggregate": "sum"}],
        "filters": [{"attribute_id": "sale-year", "operator": "eq", "value": 2026}],
        "expected_cardinality": "single_value",
        "result_grain": "全部销售记录",
    })
    ir = SemanticQueryIR.model_validate({
        "from_entity": {"binding": "sale", "entity_id": "sale"},
        "select": [{
            "alias": "total_sales",
            "expression": {
                "kind": "aggregate", "function": "sum",
                "expression": {"kind": "attribute", "attribute_id": "sale-amount", "binding": "sale"},
            },
        }],
        "where": {
            "kind": "binary", "operator": "=",
            "left": {"kind": "attribute", "attribute_id": "sale-year", "binding": "sale"},
            "right": {"kind": "literal", "value": 2026},
        },
    })
    return source, schema, version, plan, ir


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_deterministic_compiler_binds_values_for_both_dialects(dialect: str) -> None:
    source, schema, version, plan, ir = _context(dialect)
    report = validate_ir(ir, plan, version)
    assert report["ok"] is True
    compiled = compile_structured_query(source, version, schema, ir, max_rows=100)
    assert "2026" not in compiled.sql_template
    assert 2026 in compiled.parameters.values()
    assert "sum" in compiled.sql_template.casefold()
    if dialect == "postgresql":
        assert "public.sales" in compiled.sql_template
    else:
        assert "FROM sales" in compiled.sql_template


def test_plan_rejects_unknown_semantic_objects() -> None:
    _, _, version, plan, _ = _context()
    invalid = plan.model_copy(update={"entity_ids": ["physical_table_name"]})
    report = validate_plan(invalid, version)
    assert report["ok"] is False
    assert any("未知业务实体" in item for item in report["errors"])


def test_agent_search_citation_labels_are_immutable_rank_foreign_keys() -> None:
    result = _agent_citation_contract({
        "query_id": "query",
        "items": [
            {"rank": 1, "title": "治理办法", "chunk_id": "c1"},
            {"rank": 2, "title": "经营指标口径", "chunk_id": "c2"},
        ],
    })
    assert result["citation_policy"]["immutable"] is True
    assert result["items"][1]["citation_label"] == "[2]"
    assert result["items"][1]["citation_title"] == "经营指标口径"
    assert "不得重新编号" in result["items"][1]["citation_rule"]


def test_agent_search_citation_labels_continue_across_searches_in_one_turn() -> None:
    result = _agent_citation_contract({
        "query_id": "query-2",
        "items": [
            {"rank": 1, "title": "邮件证据", "chunk_id": "c11"},
            {"rank": 2, "title": "会议证据", "chunk_id": "c12"},
        ],
    }, first_citation_number=11)
    assert [item["citation_label"] for item in result["items"]] == ["[11]", "[12]"]
    assert result["citation_policy"]["allowed_citation_labels"] == ["[11]", "[12]"]
    assert [item["rank"] for item in result["items"]] == [1, 2]


def test_agent_search_bounds_model_context_and_keeps_full_fragment_route() -> None:
    long_text = "知识" * 900
    result = _agent_citation_contract({
        "query_id": "query",
        "items": [{"rank": 1, "title": "长文档", "chunk_id": "c1", "text": long_text}],
    })
    item = result["items"][0]
    assert item["text_truncated"] is True
    assert item["text_char_count"] == len(long_text)
    assert item["full_text_tool"] == "knowledge_get_fragment"
    assert len(item["text"]) <= 1201
    assert item["text"].endswith("…")


def test_ir_rejects_unlisted_function_and_sql_extension() -> None:
    _, _, version, plan, ir = _context()
    invalid = SemanticQueryIR.model_validate({**ir.model_dump(),
        "select": [{
            "expression": {"kind": "function", "function": "pg_read_file", "arguments": [{"kind": "literal", "value": "/etc/passwd"}]},
        }]
    })
    report = validate_ir(invalid, plan, version)
    assert report["ok"] is False
    assert any("函数不在白名单" in item for item in report["errors"])
    with pytest.raises(ValidationError):
        SemanticQueryIR.model_validate({
            **ir.model_dump(),
            "raw_sql": "UNION SELECT password FROM users",
        })


def test_provider_protocol_aliases_are_normalized_without_relaxing_ir_schema() -> None:
    raw = {
        "plan": {"filters": []},
        "query_ir": {
            "where": {"kind": "logical", "operator": "and", "operands": [{
                "kind": "binary", "operator": "eq",
                "left": {"kind": "literal", "value": 1},
                "right": {"kind": "literal", "value": 1},
            }]},
            "select": [{
                "expression": {
                    "kind": "function", "function": "percentage", "arguments": [
                        {"kind": "literal", "value": 4},
                        {"kind": "literal", "value": 5},
                    ],
                },
            }],
        },
    }

    normalized = _canonicalize_generated_plan(raw)

    assert normalized["query_ir"]["where"]["kind"] == "binary"
    percentage = normalized["query_ir"]["select"][0]["expression"]
    assert percentage["kind"] == "binary"
    assert percentage["operator"] == "*"
    assert percentage["left"]["operator"] == "/"


def test_mixed_exists_protocol_is_safely_reduced_to_mapped_relationship_form() -> None:
    raw = {
        "plan": {},
        "query_ir": {
            "where": {
                "kind": "exists",
                "relationship_id": "approval-sale",
                "source_binding": "sale",
                "target_binding": "approval",
                "target_entity_id": "approval",
                "query": {
                    "from_entity": {"binding": "nested", "entity_id": "approval"},
                    "select": [{"expression": {"kind": "literal", "value": 1}}],
                    "where": {
                        "kind": "binary", "operator": "=",
                        "left": {"kind": "attribute", "attribute_id": "approval-decision", "binding": "nested"},
                        "right": {"kind": "literal", "value": "pending"},
                    },
                },
            },
        },
    }

    normalized = _canonicalize_generated_plan(raw)
    exists_expression = normalized["query_ir"]["where"]

    assert exists_expression["query"] is None
    assert exists_expression["operands"][0]["left"]["binding"] == "approval"


def test_join_target_is_resolved_from_activated_relationship_endpoints() -> None:
    _, _, version, _, _ = _context()
    version.manifest["entities"].append({
        "id": "target", "fragments": [{"id": "target-main", "object_id": "public.targets", "role": "primary"}],
    })
    version.manifest["relationships"].append({
        "id": "target-sale", "from_entity_id": "target", "to_entity_id": "sale", "predicates": [],
    })
    raw = {
        "plan": {},
        "query_ir": {
            "from_entity": {"binding": "sale", "entity_id": "sale"},
            "joins": [{
                "binding": "target", "entity_id": "sale",
                "relationship_id": "target-sale", "from_binding": "sale",
            }],
        },
    }

    normalized = _repair_generated_relationship_endpoints(raw, version)

    assert normalized["query_ir"]["joins"][0]["entity_id"] == "target"


def test_ir_must_implement_every_filter_declared_by_the_plan() -> None:
    _, _, version, plan, ir = _context()
    report = validate_ir(ir.model_copy(update={"where": None}), plan, version)
    assert report["ok"] is False
    assert any("IR 缺少查询计划声明的筛选" in item for item in report["errors"])


def test_measure_contract_enforces_default_aggregate_and_fixed_filters() -> None:
    _, _, version, plan, ir = _context()
    manifest = version.manifest.copy()
    manifest["attributes"] = [dict(item) for item in manifest["attributes"]]
    manifest["attributes"][0].update({
        "is_measure": True,
        "label": "销售额",
        "default_aggregate": "sum",
        "required_filters": [{"attribute_id": "sale-year", "operator": "eq", "value": 2026}],
    })
    version.manifest = manifest

    missing_filter = plan.model_copy(update={"filters": []})
    report = validate_ir(ir.model_copy(update={"where": None}), missing_filter, version)
    assert report["ok"] is False
    assert any("缺少固定口径筛选" in item for item in report["errors"])

    wrong_aggregate = plan.model_copy(update={
        "outputs": [plan.outputs[0].model_copy(update={"aggregate": "average"})],
    })
    report = validate_ir(ir, wrong_aggregate, version)
    assert report["ok"] is False
    assert any("必须使用 sum 聚合" in item for item in report["errors"])

    assert validate_ir(ir, plan, version)["ok"] is True


def test_model_planner_deterministically_applies_activated_metric_contract() -> None:
    _, _, version, plan, ir = _context()
    manifest = version.manifest.copy()
    manifest["attributes"] = [dict(item) for item in manifest["attributes"]]
    manifest["attributes"][0].update({
        "is_measure": True,
        "label": "销售额",
        "default_aggregate": "sum",
        "business_definition": "仅统计 2026 年有效销售记录",
        "required_filters": [{"attribute_id": "sale-year", "operator": "eq", "value": 2026}],
    })
    version.manifest = manifest
    raw_plan = plan.model_copy(update={"filters": [], "evidence_constraints": []}).model_dump()
    raw_ir = ir.model_copy(update={"where": None}).model_dump()
    calls = 0

    def generator(_prompt: str):
        nonlocal calls
        calls += 1
        return {"plan": raw_plan, "query_ir": raw_ir}

    generated_plan, generated_ir = generate_semantic_plan_ir(
        plan.original_question,
        version,
        api_key="not-used",
        model="fixture-model",
        base_url=None,
        generator=generator,
    )

    assert calls == 1
    assert generated_plan.filters[0].attribute_id == "sale-year"
    assert "仅统计 2026 年有效销售记录" in generated_plan.evidence_constraints
    assert validate_ir(generated_ir, generated_plan, version)["ok"] is True


def test_agent_boundary_reapplies_metric_contract_without_physical_identifiers() -> None:
    _, _, version, plan, ir = _context()
    manifest = version.manifest.copy()
    manifest["attributes"] = [dict(item) for item in manifest["attributes"]]
    manifest["attributes"][0].update({
        "is_measure": True,
        "label": "销售额",
        "default_aggregate": "sum",
        "business_definition": "仅统计已完成订单",
        "required_filters": [{"attribute_id": "sale-year", "operator": "eq", "value": 2026}],
    })
    version.manifest = manifest

    effective_plan, effective_ir = apply_activated_metric_contracts(
        plan.model_copy(update={"filters": [], "evidence_constraints": []}),
        ir.model_copy(update={"where": None}),
        version,
    )

    assert effective_plan.filters[0].attribute_id == "sale-year"
    assert "仅统计已完成订单" in effective_plan.evidence_constraints
    assert validate_ir(effective_ir, effective_plan, version)["ok"] is True

    safe_catalog = semantic_catalog_for_planner(version)
    serialized = str(safe_catalog)
    assert "column_id" not in serialized
    assert "fragment_id" not in serialized
    assert "object_id" not in serialized
    metric = next(item for item in safe_catalog["attributes"] if item["id"] == "sale-amount")
    assert metric["business_definition"] == "仅统计已完成订单"
    assert metric["required_filters"][0]["attribute_id"] == "sale-year"


def test_two_semantic_metrics_can_share_a_column_without_leaking_contracts() -> None:
    _, _, version, plan, ir = _context()
    manifest = copy.deepcopy(version.manifest)
    manifest["attributes"][0].update({
        "is_measure": True,
        "label": "有效销售额",
        "default_aggregate": "sum",
        "required_filters": [{"attribute_id": "sale-year", "operator": "eq", "value": 2026}],
    })
    manifest["attributes"].append({
        **manifest["attributes"][0],
        "id": "sale-gross-amount",
        "label": "原始销售额",
        "required_filters": [{"attribute_id": "sale-year", "operator": "ne", "value": 2025}],
        "required_relationships": [],
    })
    version.manifest = manifest
    gross_plan = plan.model_copy(update={
        "outputs": [plan.outputs[0].model_copy(update={"attribute_ids": ["sale-gross-amount"]})],
        "filters": [],
        "evidence_constraints": [],
    })
    gross_ir = SemanticQueryIR.model_validate({**ir.model_dump(),
        "select": [{
            "alias": "gross_sales",
            "expression": {
                "kind": "aggregate",
                "function": "sum",
                "expression": {
                    "kind": "attribute",
                    "attribute_id": "sale-gross-amount",
                    "binding": "sale",
                },
            },
        }],
        "where": None,
    })

    effective_plan, effective_ir = apply_activated_metric_contracts(gross_plan, gross_ir, version)

    assert [(item.attribute_id, item.operator, item.value) for item in effective_plan.filters] == [
        ("sale-year", "ne", 2025),
    ]
    assert effective_plan.metric_contract.base_relationship_ids == []
    assert not any(item.value == 2026 for item in effective_plan.filters)
    assert validate_ir(effective_ir, effective_plan, version)["ok"] is True


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_metric_relationship_contract_compiles_correlated_exists_without_duplicate_sum(dialect: str) -> None:
    source, schema, version, plan, ir = _context(dialect)
    object_prefix = "public." if dialect == "postgresql" else ""
    sale_object = f"{object_prefix}sales"
    approval_object = f"{object_prefix}approvals"
    schema.catalog = copy.deepcopy(schema.catalog)
    schema.catalog["objects"].append({
        "id": approval_object,
        "schema": "public" if dialect == "postgresql" else None,
        "name": "approvals",
        "primary_key": ["id"],
        "columns": [
            {"id": f"{approval_object}.id", "name": "id"},
            {"id": f"{approval_object}.sale_id", "name": "sale_id"},
            {"id": f"{approval_object}.decision", "name": "decision"},
        ],
    })
    manifest = copy.deepcopy(version.manifest)
    manifest["entities"].append({
        "id": "approval",
        "fragments": [{"id": "approval-main", "object_id": approval_object, "role": "primary"}],
    })
    manifest["attributes"].append({
        "id": "approval-decision",
        "entity_id": "approval",
        "fragment_id": "approval-main",
        "column_id": f"{approval_object}.decision",
    })
    manifest["relationships"].append({
        "id": "approval-sale",
        "from_entity_id": "approval",
        "to_entity_id": "sale",
        "predicates": [{
            "left": {"object_id": approval_object, "column_id": f"{approval_object}.sale_id"},
            "operator": "=",
            "right": {"object_id": sale_object, "column_id": f"{sale_object}.id"},
        }],
    })
    manifest["attributes"][0].update({
        "is_measure": True,
        "label": "有效销售额",
        "default_aggregate": "sum",
        "required_relationships": [{
            "relationship_id": "approval-sale",
            "target_entity_id": "approval",
            "quantifier": "exists",
            "filters": [{"attribute_id": "approval-decision", "operator": "eq", "value": "approved"}],
            "description": "必须存在已通过的审批记录",
        }],
    })
    version.manifest = manifest

    effective_plan, effective_ir = apply_activated_metric_contracts(plan, ir, version)
    # Applying the trusted boundary twice must remain idempotent.
    effective_plan, effective_ir = apply_activated_metric_contracts(effective_plan, effective_ir, version)
    assert effective_plan.entity_ids == ["sale", "approval"]
    assert effective_plan.relationship_ids == ["approval-sale"]
    assert [item.attribute_id for item in effective_plan.filters].count("approval-decision") == 1
    assert effective_plan.metric_contract.base_relationship_ids == ["approval-sale"]
    assert validate_ir(effective_ir, effective_plan, version)["ok"] is True

    def relationship_exists_count(value) -> int:
        if not isinstance(value, dict):
            return 0
        own = int(value.get("kind") == "exists" and value.get("relationship_id") == "approval-sale")
        return own + sum(relationship_exists_count(item) for item in value.values() if isinstance(item, dict)) + sum(
            relationship_exists_count(child)
            for item in value.values() if isinstance(item, list)
            for child in item
        )

    assert relationship_exists_count(effective_ir.model_dump()) == 1
    compiled = compile_structured_query(source, version, schema, effective_ir, max_rows=100)
    assert "EXISTS" in compiled.sql_template.upper()
    assert "approvals" in compiled.sql_template
    assert "approved" not in compiled.sql_template
    assert "approved" in compiled.parameters.values()
    assert f"{approval_object}.decision" in compiled.referenced_columns


def test_relationship_exists_correlates_every_referenced_outer_binding() -> None:
    source = SourceConnector(
        id="source", tenant_id="tenant", space_id="space", name="经营库",
        source_type="database", config={"dialect": "postgresql"},
    )
    orders = "public.orders"
    suppliers = "public.suppliers"
    schema = DataSourceSchemaVersion(
        id="schema", tenant_id="tenant", space_id="space", source_id="source",
        version_number=1, schema_fingerprint="a" * 64, status="current",
        catalog={"objects": [
            {
                "id": orders, "schema": "public", "name": "orders", "primary_key": ["id"],
                "columns": [
                    {"id": f"{orders}.id", "name": "id"},
                    {"id": f"{orders}.supplier_id", "name": "supplier_id"},
                    {"id": f"{orders}.order_date", "name": "order_date", "type_family": "date"},
                ],
            },
            {
                "id": suppliers, "schema": "public", "name": "suppliers", "primary_key": ["id"],
                "columns": [
                    {"id": f"{suppliers}.id", "name": "id"},
                    {"id": f"{suppliers}.name", "name": "name"},
                ],
            },
        ]},
    )
    version = SemanticMappingVersion(
        id="mapping-version", tenant_id="tenant", space_id="space", source_id="source",
        mapping_set_id="mapping", schema_version_id="schema", schema_fingerprint="a" * 64,
        mapping_hash="b" * 64, version_number=1, status="active", created_by="user",
        manifest={
            "entities": [
                {"id": "order", "fragments": [{"id": "order-main", "object_id": orders, "role": "primary"}]},
                {"id": "supplier", "fragments": [{"id": "supplier-main", "object_id": suppliers, "role": "primary"}]},
            ],
            "attributes": [
                {"id": "order-date", "entity_id": "order", "fragment_id": "order-main", "column_id": f"{orders}.order_date"},
                {"id": "supplier-name", "entity_id": "supplier", "fragment_id": "supplier-main", "column_id": f"{suppliers}.name"},
            ],
            "relationships": [{
                "id": "order-supplier", "from_entity_id": "order", "to_entity_id": "supplier",
                "predicates": [{
                    "left": {"object_id": orders, "column_id": f"{orders}.supplier_id"},
                    "operator": "=",
                    "right": {"object_id": suppliers, "column_id": f"{suppliers}.id"},
                }],
            }],
        },
    )
    ir = SemanticQueryIR.model_validate({
        "from_entity": {"binding": "current_order", "entity_id": "order"},
        "joins": [{
            "binding": "current_supplier", "entity_id": "supplier",
            "relationship_id": "order-supplier", "from_binding": "current_order",
        }],
        "select": [{
            "alias": "supplier",
            "expression": {"kind": "attribute", "attribute_id": "supplier-name", "binding": "current_supplier"},
        }],
        "where": {
            "kind": "exists", "relationship_id": "order-supplier",
            "source_binding": "current_supplier", "target_binding": "newer_order",
            "target_entity_id": "order", "negated": True,
            "operands": [{
                "kind": "binary", "operator": ">",
                "left": {"kind": "attribute", "attribute_id": "order-date", "binding": "newer_order"},
                "right": {"kind": "attribute", "attribute_id": "order-date", "binding": "current_order"},
            }],
        },
    })

    compiled = compile_structured_query(source, version, schema, ir, max_rows=100)
    subquery = compiled.sql_template.split("EXISTS (", 1)[1]
    assert "FROM public.orders AS newer_order" in subquery
    assert "newer_order.order_date > current_order.order_date" in subquery
    assert "FROM public.orders AS newer_order, public.orders AS current_order" not in subquery


def test_metric_relationship_contract_cannot_be_replaced_by_an_unrelated_exists() -> None:
    _, schema, version, plan, ir = _context()
    approval_object = "public.approvals"
    schema.catalog["objects"].append({
        "id": approval_object, "schema": "public", "name": "approvals", "primary_key": ["id"],
        "columns": [
            {"id": f"{approval_object}.id", "name": "id"},
            {"id": f"{approval_object}.sale_id", "name": "sale_id"},
            {"id": f"{approval_object}.decision", "name": "decision"},
        ],
    })
    version.manifest["entities"].append({
        "id": "approval", "fragments": [{"id": "approval-main", "object_id": approval_object, "role": "primary"}],
    })
    version.manifest["attributes"].append({
        "id": "approval-decision", "entity_id": "approval", "fragment_id": "approval-main",
        "column_id": f"{approval_object}.decision",
    })
    version.manifest["relationships"].append({
        "id": "approval-sale", "from_entity_id": "approval", "to_entity_id": "sale",
        "predicates": [{
            "left": {"object_id": approval_object, "column_id": f"{approval_object}.sale_id"},
            "operator": "=",
            "right": {"object_id": "public.sales", "column_id": "public.sales.id"},
        }],
    })
    version.manifest["attributes"][0].update({
        "is_measure": True, "default_aggregate": "sum",
        "required_relationships": [{
            "relationship_id": "approval-sale", "target_entity_id": "approval", "quantifier": "exists",
            "filters": [{"attribute_id": "approval-decision", "operator": "eq", "value": "approved"}],
        }],
    })
    effective_plan, effective_ir = apply_activated_metric_contracts(plan, ir, version)
    tampered = effective_ir.model_dump()
    relational = next(
        item for item in tampered["where"]["operands"]
        if item.get("kind") == "exists"
    )
    relational["target_entity_id"] = "sale"
    invalid_ir = SemanticQueryIR.model_validate(tampered)
    report = validate_ir(invalid_ir, effective_plan, version)
    assert report["ok"] is False
    assert any("关系端点不匹配" in item or "缺少可审计" in item for item in report["errors"])


def test_model_planner_repairs_an_invalid_first_response_without_relaxing_schema() -> None:
    _, _, version, expected_plan, expected_ir = _context()
    responses = iter([
        {
            "plan": {"question": expected_plan.original_question, "entities": ["sale"]},
            "query_ir": {"raw_sql": "SELECT * FROM sales"},
        },
        {"plan": expected_plan.model_dump(), "query_ir": expected_ir.model_dump()},
    ])
    prompts: list[str] = []

    def generator(prompt: str):
        prompts.append(prompt)
        return next(responses)

    plan, query_ir = generate_semantic_plan_ir(
        expected_plan.original_question,
        version,
        api_key="not-used",
        model="fixture-model",
        base_url=None,
        generator=generator,
    )

    assert plan == expected_plan
    assert query_ir == expected_ir
    assert len(prompts) == 2
    assert "校验错误" in prompts[1]
    assert "raw_sql" in prompts[1]


def test_model_planner_repairs_semantically_unknown_ids() -> None:
    _, _, version, expected_plan, expected_ir = _context()
    unknown_plan = expected_plan.model_copy(update={"entity_ids": ["physical_table"]})
    responses = iter([
        {"plan": unknown_plan.model_dump(), "query_ir": expected_ir.model_dump()},
        {"plan": expected_plan.model_dump(), "query_ir": expected_ir.model_dump()},
    ])
    prompts: list[str] = []

    def generator(prompt: str):
        prompts.append(prompt)
        return next(responses)

    plan, query_ir = generate_semantic_plan_ir(
        expected_plan.original_question,
        version,
        api_key="not-used",
        model="fixture-model",
        base_url=None,
        generator=generator,
    )

    assert plan == expected_plan
    assert query_ir == expected_ir
    assert "未知业务实体" in prompts[1]


def test_model_planner_deterministically_declares_ir_root_in_plan_scope() -> None:
    _, _, version, expected_plan, expected_ir = _context()
    raw_plan = expected_plan.model_dump()
    raw_plan["entity_ids"] = []
    raw_plan["outputs"][0]["attribute_ids"] = []
    raw_plan["filters"] = []
    calls = 0

    def generator(_prompt: str):
        nonlocal calls
        calls += 1
        return {"plan": raw_plan, "query_ir": expected_ir.model_dump()}

    plan, query_ir = generate_semantic_plan_ir(
        expected_plan.original_question,
        version,
        api_key="not-used",
        model="fixture-model",
        base_url=None,
        generator=generator,
    )

    assert calls == 1
    assert plan.entity_ids == ["sale"]
    assert plan.outputs[0].attribute_ids == ["sale-amount"]
    assert plan.filters == expected_plan.filters
    assert query_ir == expected_ir


def test_model_planner_canonicalizes_only_known_plan_operator_aliases() -> None:
    _, _, version, expected_plan, expected_ir = _context()
    raw_plan = expected_plan.model_dump()
    raw_plan["filters"][0]["operator"] = "="
    calls = 0

    def generator(_prompt: str):
        nonlocal calls
        calls += 1
        return {"plan": raw_plan, "query_ir": expected_ir.model_dump()}

    plan, query_ir = generate_semantic_plan_ir(
        expected_plan.original_question,
        version,
        api_key="not-used",
        model="fixture-model",
        base_url=None,
        generator=generator,
    )

    assert plan.filters[0].operator == "eq"
    assert query_ir == expected_ir
    assert calls == 1
