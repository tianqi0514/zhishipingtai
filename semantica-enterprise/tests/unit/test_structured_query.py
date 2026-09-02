from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.structured_schemas import SemanticQueryIR, SemanticQueryPlan
from packages.platform.models import DataSourceSchemaVersion, SemanticMappingVersion, SourceConnector
from packages.platform.structured_query import (
    compile_structured_query,
    generate_semantic_plan_ir,
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
