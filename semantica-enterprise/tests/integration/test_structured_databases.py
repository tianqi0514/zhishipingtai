from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from apps.api.structured_schemas import SemanticQueryIR, SemanticQueryPlan
from packages.platform.models import (
    DataPreviewPolicy,
    DataSourceSchemaVersion,
    SemanticMappingVersion,
    SourceConnector,
)
from packages.platform.security import encrypt_secret
from packages.platform.structured_data import (
    StructuredDataError,
    create_source_engine,
    discover_catalog,
    exact_count,
    inspect_distinct_values,
    preview_live,
    schema_diff,
)
from packages.platform.structured_query import (
    compile_structured_query,
    execute_compiled_query,
    validate_ir,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_STRUCTURED_DB_TESTS") != "1",
    reason="requires the isolated MySQL/PostgreSQL fixture services",
)


def _source(dialect: str) -> SourceConnector:
    is_postgres = dialect == "postgresql"
    return SourceConnector(
        id=f"source-{dialect}",
        tenant_id="tenant",
        space_id="space",
        name=f"{dialect} fixture",
        source_type="database",
        config={
            "dialect": dialect,
            "host": "structured-postgres" if is_postgres else "structured-mysql",
            "port": 5432 if is_postgres else 3306,
            "database": "structured_fixture",
            "username": "structured_reader",
            **({"schema": "public"} if is_postgres else {}),
        },
        secret_encrypted=encrypt_secret("structured_fixture_password"),
    )


def _schema(source: SourceConnector, catalog: dict) -> DataSourceSchemaVersion:
    return DataSourceSchemaVersion(
        id=f"schema-{source.id}",
        tenant_id=source.tenant_id,
        space_id=source.space_id,
        source_id=source.id,
        version_number=1,
        schema_fingerprint=catalog["schema_fingerprint"],
        catalog=catalog,
        status="current",
    )


def _policy(source: SourceConnector) -> DataPreviewPolicy:
    return DataPreviewPolicy(
        id=f"policy-{source.id}",
        tenant_id=source.tenant_id,
        space_id=source.space_id,
        source_id=source.id,
        live_preview_enabled=True,
        allowed_objects=[],
        denied_objects=[],
        allowed_columns={},
        sensitive_columns={},
        masking_rules={},
        default_order={},
        default_page_size=20,
        max_page_size=100,
        max_text_length=500,
        allow_full_cell=False,
        allow_exact_count=False,
        query_timeout_seconds=15,
        max_filter_conditions=10,
        max_result_bytes=2_000_000,
    )


def _object_id(dialect: str, name: str) -> str:
    return f"public.{name}" if dialect == "postgresql" else name


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_real_schema_discovery_and_preview_are_safe(dialect: str) -> None:
    source = _source(dialect)
    catalog = discover_catalog(source)
    schema = _schema(source, catalog)
    policy = _policy(source)

    objects = {item["id"]: item for item in catalog["objects"]}
    customers_id = _object_id(dialect, "customers")
    orders_id = _object_id(dialect, "orders")
    assert customers_id in objects
    assert _object_id(dialect, "completed_orders") in objects
    assert objects[customers_id]["primary_key"] == ["id"]
    assert any(
        item["referred_table"] == "customers"
        for item in objects[orders_id]["foreign_keys"]
    )

    customer_columns = {item["name"]: item for item in objects[customers_id]["columns"]}
    assert customer_columns["password"]["sensitivity"] == "blocked"
    assert customer_columns["email"]["sensitivity"] == "masked"
    assert customer_columns["mobile"]["sensitivity"] == "masked"
    assert customer_columns["password"]["sample_values"] == []

    preview = preview_live(
        source,
        schema,
        policy,
        object_id=customers_id,
        page=1,
        page_size=2,
        order_by=f"{customers_id}.id",
        order_direction="asc",
        filters=[{
            "column_id": f"{customers_id}.customer_name",
            "operator": "contains",
            "value": "集团",
        }],
    )
    assert preview["mode"] == "live"
    assert preview["current_page_rows"] == 2
    assert preview["has_next"] is True
    assert all(item is not None for item in preview["row_keys"])
    assert "password" not in {item["name"] for item in preview["columns"]}
    assert preview["rows"][0]["email"].endswith("@example.com")
    assert preview["rows"][0]["mobile"] == "138****2222"
    assert "never-return-this" not in repr(preview)

    with pytest.raises(StructuredDataError) as blocked_filter:
        preview_live(
            source,
            schema,
            policy,
            object_id=customers_id,
            page=1,
            page_size=20,
            order_by=None,
            order_direction="asc",
            filters=[{
                "column_id": f"{customers_id}.email",
                "operator": "contains",
                "value": "example",
            }],
        )
    assert blocked_filter.value.code == "FILTER_COLUMN_DENIED"

    orders_preview = preview_live(
        source,
        schema,
        policy,
        object_id=orders_id,
        page=1,
        page_size=20,
        order_by=f"{orders_id}.order_date",
        order_direction="asc",
        filters=[
            {
                "column_id": f"{orders_id}.order_date",
                "operator": "between",
                "value": "2026-01-01",
                "upper": "2026-03-31",
            },
            {
                "column_id": f"{orders_id}.sales_amount",
                "operator": "gte",
                "value": 250000,
            },
        ],
    )
    assert [row["id"] for row in orders_preview["rows"]] == [2, 3, 4]

    no_key_id = _object_id(dialect, "activity_log")
    no_key_preview = preview_live(
        source,
        schema,
        policy,
        object_id=no_key_id,
        page=1,
        page_size=20,
        order_by=None,
        order_direction="asc",
        filters=[],
    )
    assert no_key_preview["current_page_rows"] == 2
    assert all(item is None for item in no_key_preview["row_keys"])
    assert no_key_preview["warnings"]

    policy.allow_exact_count = True
    count = exact_count(
        source,
        schema,
        policy,
        object_id=orders_id,
        filters=[{
            "column_id": f"{orders_id}.status",
            "operator": "eq",
            "value": "completed",
        }],
    )
    assert count["count"] == 4

    values = inspect_distinct_values(
        source,
        schema,
        policy,
        object_id=orders_id,
        column_id=f"{orders_id}.status",
        limit=20,
    )
    assert values["values"] == ["cancelled", "completed"]
    matched = inspect_distinct_values(
        source,
        schema,
        policy,
        object_id=orders_id,
        column_id=f"{orders_id}.status",
        search="comp",
        limit=20,
    )
    assert matched["values"] == ["completed"]

    # Object and column identifiers can only come from the discovered catalog.
    # SQL-looking values remain bound parameters and cannot escape the filter.
    with pytest.raises(StructuredDataError) as system_table:
        preview_live(
            source, schema, policy,
            object_id="information_schema.tables",
            page=1, page_size=20, order_by=None, order_direction="asc", filters=[],
        )
    assert system_table.value.code == "OBJECT_NOT_FOUND"

    with pytest.raises(StructuredDataError) as blocked_order:
        preview_live(
            source, schema, policy,
            object_id=customers_id,
            page=1, page_size=20,
            order_by=f"{customers_id}.password",
            order_direction="asc", filters=[],
        )
    assert blocked_order.value.code == "ORDER_COLUMN_DENIED"

    injected = preview_live(
        source, schema, policy,
        object_id=customers_id,
        page=1, page_size=20,
        order_by=f"{customers_id}.id",
        order_direction="asc",
        filters=[{
            "column_id": f"{customers_id}.customer_name",
            "operator": "eq",
            "value": "' OR 1=1; DROP TABLE customers; --",
        }],
    )
    assert injected["rows"] == []
    assert customers_id in {item["id"] for item in discover_catalog(source)["objects"]}

    with pytest.raises(StructuredDataError) as blocked_values:
        inspect_distinct_values(
            source, schema, policy,
            object_id=customers_id,
            column_id=f"{customers_id}.email",
            limit=20,
        )
    assert blocked_values.value.code == "VALUE_INSPECTION_DENIED"


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_fixture_credentials_are_database_enforced_read_only(dialect: str) -> None:
    source = _source(dialect)
    _, engine = create_source_engine(source)
    try:
        with engine.connect() as connection:
            with pytest.raises(Exception):
                connection.execute(text("UPDATE orders SET status = 'tampered' WHERE id = 1"))
        with engine.connect() as connection:
            with pytest.raises(Exception):
                connection.execute(text("CREATE TABLE forbidden_write_probe (id INTEGER)"))
    finally:
        engine.dispose()


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_real_schema_drift_is_discovered_and_classified(dialect: str) -> None:
    source = _source(dialect)
    before = discover_catalog(source)
    if dialect == "postgresql":
        password = os.environ.get("STRUCTURED_FIXTURE_PG_ADMIN_PASSWORD")
        url = f"postgresql+psycopg://structured_admin:{password}@structured-postgres:5432/structured_fixture"
        add_sql = "ALTER TABLE orders ADD COLUMN drift_probe VARCHAR(30)"
        drop_sql = "ALTER TABLE orders DROP COLUMN drift_probe"
        column_id = "public.orders.drift_probe"
    else:
        password = os.environ.get("STRUCTURED_FIXTURE_MYSQL_ADMIN_PASSWORD")
        url = f"mysql+pymysql://root:{password}@structured-mysql:3306/structured_fixture?charset=utf8mb4"
        add_sql = "ALTER TABLE orders ADD COLUMN drift_probe VARCHAR(30)"
        drop_sql = "ALTER TABLE orders DROP COLUMN drift_probe"
        column_id = "orders.drift_probe"
    if not password:
        pytest.skip("isolated fixture administrator credential is not configured")
    admin = create_engine(url, pool_pre_ping=True)
    try:
        with admin.begin() as connection:
            connection.execute(text(add_sql))
        changed = discover_catalog(source)
        added = schema_diff(before, changed)
        assert added["added_columns"] == [column_id]
        assert added["breaking"] is False
        with admin.begin() as connection:
            connection.execute(text(drop_sql))
        restored = discover_catalog(source)
        removed = schema_diff(changed, restored)
        assert removed["removed_columns"] == [column_id]
        assert removed["breaking"] is True
        assert restored["schema_fingerprint"] == before["schema_fingerprint"]
    finally:
        # Keep the shared fixture deterministic even if an assertion fails.
        try:
            with admin.begin() as connection:
                connection.execute(text(drop_sql))
        except Exception:
            pass
        admin.dispose()


def _sales_mapping(source: SourceConnector, schema: DataSourceSchemaVersion, dialect: str) -> SemanticMappingVersion:
    orders = _object_id(dialect, "orders")
    manifest = {
        "manifest_version": "chuanshen.semantic-mapping/v1",
        "source_id": source.id,
        "ontology_id": "ontology",
        "schema_version_id": schema.id,
        "entities": [{
            "id": "order",
            "ontology_term_id": "term-order",
            "label": "订单",
            "description": "经营订单",
            "fragments": [{
                "id": "order-main",
                "object_id": orders,
                "role": "primary",
                "identity_column_ids": [f"{orders}.id"],
                "display_column_id": f"{orders}.order_no",
            }],
        }],
        "attributes": [
            {
                "id": "order-sales-amount", "ontology_term_id": "term-sales-amount",
                "entity_id": "order", "fragment_id": "order-main",
                "column_id": f"{orders}.sales_amount", "label": "销售额", "semantic_type": "number",
                "is_measure": True,
            },
            {
                "id": "order-date", "ontology_term_id": "term-order-date",
                "entity_id": "order", "fragment_id": "order-main",
                "column_id": f"{orders}.order_date", "label": "订单日期", "semantic_type": "date",
            },
            {
                "id": "order-status", "ontology_term_id": "term-order-status",
                "entity_id": "order", "fragment_id": "order-main",
                "column_id": f"{orders}.status", "label": "订单状态", "semantic_type": "string",
            },
        ],
        "relationships": [],
    }
    return SemanticMappingVersion(
        id=f"mapping-{dialect}", tenant_id=source.tenant_id, space_id=source.space_id,
        source_id=source.id, mapping_set_id=f"set-{dialect}", schema_version_id=schema.id,
        schema_fingerprint=schema.schema_fingerprint, mapping_hash="b" * 64,
        version_number=1, manifest=manifest, status="active", created_by="user",
    )


def _sales_plan_ir() -> tuple[SemanticQueryPlan, SemanticQueryIR]:
    plan = SemanticQueryPlan.model_validate({
        "original_question": "2026 年已完成订单的销售总额是多少？",
        "intent": "计算指定年份已完成订单销售额",
        "entity_ids": ["order"],
        "outputs": [{
            "position": 1, "label": "销售总额", "kind": "metric",
            "attribute_ids": ["order-sales-amount"], "aggregate": "sum",
        }],
        "filters": [
            {"attribute_id": "order-date", "operator": "between", "value": "2026-01-01", "upper": "2026-12-31"},
            {"attribute_id": "order-status", "operator": "eq", "value": "completed"},
        ],
        "expected_cardinality": "single_value",
        "result_grain": "2026 年已完成订单",
        "time_range": "2026-01-01 至 2026-12-31",
        "null_policy": "忽略销售额为空的订单",
        "metric_contract": {
            "kind": "sum", "aggregation_grain": "订单", "base_entity_ids": ["order"],
        },
    })
    date_expression = {"kind": "attribute", "attribute_id": "order-date", "binding": "o"}
    ir = SemanticQueryIR.model_validate({
        "from_entity": {"binding": "o", "entity_id": "order"},
        "select": [{
            "alias": "total_sales",
            "expression": {
                "kind": "aggregate", "function": "sum",
                "expression": {"kind": "attribute", "attribute_id": "order-sales-amount", "binding": "o"},
            },
        }],
        "where": {
            "kind": "logical", "operator": "and", "operands": [
                {
                    "kind": "between", "expression": date_expression,
                    "lower": {"kind": "literal", "value": "2026-01-01"},
                    "upper": {"kind": "literal", "value": "2026-12-31"},
                },
                {
                    "kind": "binary", "operator": "=",
                    "left": {"kind": "attribute", "attribute_id": "order-status", "binding": "o"},
                    "right": {"kind": "literal", "value": "completed"},
                },
            ],
        },
    })
    return plan, ir


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_deterministic_query_executes_real_aggregate_for_both_dialects(dialect: str) -> None:
    source = _source(dialect)
    schema = _schema(source, discover_catalog(source))
    mapping = _sales_mapping(source, schema, dialect)
    plan, ir = _sales_plan_ir()

    report = validate_ir(ir, plan, mapping)
    assert report["ok"] is True, report
    compiled = compile_structured_query(source, mapping, schema, ir, max_rows=20)
    assert "completed" not in compiled.sql_template
    assert "2026-01-01" not in compiled.sql_template
    assert "completed" in compiled.parameters.values()
    result = execute_compiled_query(source, compiled, timeout_seconds=15)
    assert result["status"] == "succeeded"
    assert result["row_count"] == 1
    assert result["rows"] == [{"total_sales": "910000.00"}]
    assert result["truncated"] is False


def _full_mapping(source: SourceConnector, schema: DataSourceSchemaVersion, dialect: str) -> SemanticMappingVersion:
    def oid(name: str) -> str:
        return _object_id(dialect, name)

    entity_specs = {
        "customer": ("customers", "customer_name", "客户"),
        "order": ("orders", "order_no", "订单"),
        "item": ("order_items", "id", "订单明细"),
        "product": ("products", "product_name", "产品"),
        "target": ("sales_targets", "id", "销售目标"),
        "supplier": ("suppliers", "supplier_name", "供应商"),
        "risk": ("risk_events", "id", "风险事件"),
    }
    entities = []
    for entity_id, (table_name, display_name, label) in entity_specs.items():
        object_id = oid(table_name)
        entities.append({
            "id": entity_id, "ontology_term_id": f"term-{entity_id}", "label": label,
            "fragments": [{
                "id": f"{entity_id}-main", "object_id": object_id, "role": "primary",
                "identity_column_ids": [f"{object_id}.id"],
                "display_column_id": f"{object_id}.{display_name}",
            }],
        })
    attribute_specs = {
        "customer-id": ("customer", "customers", "id", "integer"),
        "customer-name": ("customer", "customers", "customer_name", "string"),
        "order-id": ("order", "orders", "id", "integer"),
        "order-date": ("order", "orders", "order_date", "date"),
        "order-status": ("order", "orders", "status", "string"),
        "order-region": ("order", "orders", "region", "string"),
        "order-sales": ("order", "orders", "sales_amount", "number"),
        "item-id": ("item", "order_items", "id", "integer"),
        "item-order-id": ("item", "order_items", "order_id", "integer"),
        "item-product-id": ("item", "order_items", "product_id", "integer"),
        "item-quantity": ("item", "order_items", "quantity", "integer"),
        "item-price": ("item", "order_items", "unit_price", "number"),
        "product-name": ("product", "products", "product_name", "string"),
        "target-amount": ("target", "sales_targets", "target_amount", "number"),
        "target-year": ("target", "sales_targets", "target_year", "integer"),
        "risk-id": ("risk", "risk_events", "id", "integer"),
    }
    attributes = [{
        "id": attribute_id,
        "ontology_term_id": f"term-{attribute_id}",
        "entity_id": entity_id,
        "fragment_id": f"{entity_id}-main",
        "column_id": f"{oid(table_name)}.{column_name}",
        "label": attribute_id,
        "semantic_type": semantic_type,
        "is_measure": semantic_type == "number",
    } for attribute_id, (entity_id, table_name, column_name, semantic_type) in attribute_specs.items()]
    relationships = [{
        "id": relationship_id,
        "ontology_term_id": f"term-{relationship_id}",
        "label": relationship_id,
        "from_entity_id": from_entity,
        "to_entity_id": to_entity,
        "cardinality": cardinality,
        "predicates": [{
            "left": {"object_id": oid(left_table), "column_id": f"{oid(left_table)}.{left_column}"},
            "operator": "=",
            "right": {"object_id": oid(right_table), "column_id": f"{oid(right_table)}.{right_column}"},
        }],
    } for relationship_id, from_entity, to_entity, cardinality, left_table, left_column, right_table, right_column in [
        ("order-customer", "order", "customer", "many_to_one", "orders", "customer_id", "customers", "id"),
        ("item-order", "item", "order", "many_to_one", "order_items", "order_id", "orders", "id"),
        ("item-product", "item", "product", "many_to_one", "order_items", "product_id", "products", "id"),
        ("risk-supplier", "risk", "supplier", "many_to_one", "risk_events", "supplier_id", "suppliers", "id"),
    ]]
    return SemanticMappingVersion(
        id=f"full-mapping-{dialect}", tenant_id=source.tenant_id, space_id=source.space_id,
        source_id=source.id, mapping_set_id=f"full-set-{dialect}", schema_version_id=schema.id,
        schema_fingerprint=schema.schema_fingerprint, mapping_hash="c" * 64,
        version_number=1,
        manifest={
            "manifest_version": "chuanshen.semantic-mapping/v1", "source_id": source.id,
            "ontology_id": "ontology", "schema_version_id": schema.id,
            "entities": entities, "attributes": attributes, "relationships": relationships,
        },
        status="active", created_by="user",
    )


def _plan(
    question: str,
    entity_ids: list[str],
    relationship_ids: list[str],
    attribute_ids: list[str],
    output_count: int,
    *,
    complex_calculation: bool = False,
) -> SemanticQueryPlan:
    payload = {
        "original_question": question,
        "intent": question,
        "entity_ids": entity_ids,
        "relationship_ids": relationship_ids,
        "outputs": [{
            "position": index + 1,
            "label": f"结果{index + 1}",
            "kind": "metric" if index == output_count - 1 else "attribute",
            "attribute_ids": attribute_ids,
            "aggregate": "sum" if index == output_count - 1 else None,
        } for index in range(output_count)],
        "expected_cardinality": "single_value" if output_count == 1 else "multiple_rows",
        "result_grain": "按问题声明的业务粒度",
        "metric_contract": {"kind": "sum", "base_entity_ids": entity_ids, "base_relationship_ids": relationship_ids},
    }
    if complex_calculation:
        payload["calculation_steps"] = [{
            "step_id": "calculate", "kind": "derive", "operation": "business_calculation",
            "entity_ids": entity_ids, "relationship_ids": relationship_ids,
            "attribute_ids": attribute_ids, "description": "按明确口径完成计算",
        }]
        payload["result_step_id"] = "calculate"
    return SemanticQueryPlan.model_validate(payload)


def _attr(attribute_id: str, binding: str) -> dict:
    return {"kind": "attribute", "attribute_id": attribute_id, "binding": binding}


def _literal(value) -> dict:
    return {"kind": "literal", "value": value}


def _aggregate(function: str, expression: dict | None = None, *, distinct: bool = False) -> dict:
    result = {"kind": "aggregate", "function": function, "distinct": distinct}
    if expression is not None:
        result["expression"] = expression
    return result


def _binary(operator: str, left: dict, right: dict) -> dict:
    return {"kind": "binary", "operator": operator, "left": left, "right": right}


def _and(*operands: dict) -> dict:
    return {"kind": "logical", "operator": "and", "operands": list(operands)}


def _date_range(binding: str = "o") -> dict:
    return {
        "kind": "between", "expression": _attr("order-date", binding),
        "lower": _literal("2026-01-01"), "upper": _literal("2026-12-31"),
    }


def _completed(binding: str = "o") -> dict:
    return _binary("=", _attr("order-status", binding), _literal("completed"))


def _execute_case(
    source: SourceConnector,
    schema: DataSourceSchemaVersion,
    mapping: SemanticMappingVersion,
    plan: SemanticQueryPlan,
    ir_payload: dict,
) -> list[dict]:
    ir = SemanticQueryIR.model_validate(ir_payload)
    report = validate_ir(ir, plan, mapping)
    assert report["ok"] is True, report
    compiled = compile_structured_query(source, mapping, schema, ir, max_rows=100)
    return execute_compiled_query(source, compiled, timeout_seconds=15)["rows"]


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_real_numeric_query_matrix(dialect: str) -> None:
    source = _source(dialect)
    schema = _schema(source, discover_catalog(source))
    mapping = _full_mapping(source, schema, dialect)

    rows = _execute_case(
        source, schema, mapping,
        _plan("2026 年各区域销售总额", ["order"], [], ["order-region", "order-sales", "order-date", "order-status"], 2),
        {
            "from_entity": {"binding": "o", "entity_id": "order"},
            "select": [
                {"alias": "region", "expression": _attr("order-region", "o")},
                {"alias": "sales", "expression": _aggregate("sum", _attr("order-sales", "o"))},
            ],
            "where": _and(_date_range(), _completed()),
            "group_by": [_attr("order-region", "o")],
            "order_by": [{"expression": _attr("order-region", "o"), "direction": "asc"}],
        },
    )
    assert rows == [
        {"region": "华东", "sales": "300000.00"},
        {"region": "华北", "sales": "360000.00"},
        {"region": "华南", "sales": "250000.00"},
    ]

    customer_sales = _aggregate("sum", _attr("order-sales", "o"))
    rows = _execute_case(
        source, schema, mapping,
        _plan("2026 年销售额最高客户", ["order", "customer"], ["order-customer"], ["customer-name", "order-sales", "order-date", "order-status"], 2, complex_calculation=True),
        {
            "from_entity": {"binding": "o", "entity_id": "order"},
            "joins": [{"binding": "c", "entity_id": "customer", "relationship_id": "order-customer", "from_binding": "o"}],
            "select": [
                {"alias": "customer", "expression": _attr("customer-name", "c")},
                {"alias": "sales", "expression": customer_sales},
            ],
            "where": _and(_date_range(), _completed()),
            "group_by": [_attr("customer-name", "c")],
            "order_by": [{"expression": customer_sales, "direction": "desc"}],
            "limit": 1,
        },
    )
    assert rows == [{"customer": "江北能源集团", "sales": "360000.00"}]

    item_sales = _binary("*", _attr("item-quantity", "i"), _attr("item-price", "i"))
    product_sales = _aggregate("sum", item_sales)
    rows = _execute_case(
        source, schema, mapping,
        _plan("2026 年 Top 5 产品", ["item", "product", "order"], ["item-product", "item-order"], ["product-name", "item-quantity", "item-price", "order-date", "order-status"], 2, complex_calculation=True),
        {
            "from_entity": {"binding": "i", "entity_id": "item"},
            "joins": [
                {"binding": "p", "entity_id": "product", "relationship_id": "item-product", "from_binding": "i"},
                {"binding": "o", "entity_id": "order", "relationship_id": "item-order", "from_binding": "i"},
            ],
            "select": [
                {"alias": "product", "expression": _attr("product-name", "p")},
                {"alias": "sales", "expression": product_sales},
            ],
            "where": _and(_date_range(), _completed()),
            "group_by": [_attr("product-name", "p")],
            "order_by": [{"expression": product_sales, "direction": "desc"}],
            "limit": 5,
        },
    )
    assert rows == [
        {"product": "NexusOne", "sales": "400000.00"},
        {"product": "传神智库", "sales": "360000.00"},
        {"product": "传神智能体", "sales": "150000.00"},
    ]

    rows = _execute_case(
        source, schema, mapping,
        _plan("2026 年 NexusOne 销售额", ["item", "product", "order"], ["item-product", "item-order"], ["product-name", "item-quantity", "item-price", "order-date", "order-status"], 1),
        {
            "from_entity": {"binding": "i", "entity_id": "item"},
            "joins": [
                {"binding": "p", "entity_id": "product", "relationship_id": "item-product", "from_binding": "i"},
                {"binding": "o", "entity_id": "order", "relationship_id": "item-order", "from_binding": "i"},
            ],
            "select": [{"alias": "nexusone_sales", "expression": product_sales}],
            "where": _and(
                _date_range(), _completed(),
                _binary("=", _attr("product-name", "p"), _literal("NexusOne")),
            ),
        },
    )
    assert rows == [{"nexusone_sales": "400000.00"}]

    rows = _execute_case(
        source, schema, mapping,
        _plan("供应商风险事件数量", ["risk"], [], ["risk-id"], 1),
        {
            "from_entity": {"binding": "r", "entity_id": "risk"},
            "select": [{"alias": "risk_count", "expression": _aggregate("count", _attr("risk-id", "r"))}],
        },
    )
    assert rows == [{"risk_count": 3}]

    rows = _execute_case(
        source, schema, mapping,
        _plan("没有订单的客户", ["customer", "order"], ["order-customer"], ["customer-name", "order-id"], 1),
        {
            "from_entity": {"binding": "c", "entity_id": "customer"},
            "joins": [{"binding": "o", "entity_id": "order", "relationship_id": "order-customer", "from_binding": "c", "join_type": "left"}],
            "select": [{"alias": "customer", "expression": _attr("customer-name", "c")}],
            "where": {"kind": "is_null", "expression": _attr("order-id", "o")},
        },
    )
    assert rows == [{"customer": "尚未成交客户"}]

    rows = _execute_case(
        source, schema, mapping,
        _plan("订单明细数与订单去重数", ["item"], [], ["item-id", "item-order-id"], 2),
        {
            "from_entity": {"binding": "i", "entity_id": "item"},
            "select": [
                {"alias": "item_count", "expression": _aggregate("count", _attr("item-id", "i"))},
                {"alias": "order_count", "expression": _aggregate("count", _attr("item-order-id", "i"), distinct=True)},
            ],
        },
    )
    assert rows == [{"item_count": 6, "order_count": 5}]

    distinct_products = _aggregate("count", _attr("item-product-id", "i"), distinct=True)
    rows = _execute_case(
        source, schema, mapping,
        _plan("同时购买两种以上产品的客户", ["item", "order", "customer"], ["item-order", "order-customer"], ["customer-name", "item-product-id", "order-status"], 2, complex_calculation=True),
        {
            "from_entity": {"binding": "i", "entity_id": "item"},
            "joins": [
                {"binding": "o", "entity_id": "order", "relationship_id": "item-order", "from_binding": "i"},
                {"binding": "c", "entity_id": "customer", "relationship_id": "order-customer", "from_binding": "o"},
            ],
            "select": [
                {"alias": "customer", "expression": _attr("customer-name", "c")},
                {"alias": "product_count", "expression": distinct_products},
            ],
            "where": _completed(),
            "group_by": [_attr("customer-name", "c")],
            "having": _binary(">=", distinct_products, _literal(2)),
        },
    )
    assert rows == [{"customer": "南方交通集团", "product_count": 2}]

    rows = _execute_case(
        source, schema, mapping,
        _plan("每个客户最近订单日期", ["order", "customer"], ["order-customer"], ["customer-name", "order-date"], 2),
        {
            "from_entity": {"binding": "o", "entity_id": "order"},
            "joins": [{"binding": "c", "entity_id": "customer", "relationship_id": "order-customer", "from_binding": "o"}],
            "select": [
                {"alias": "customer", "expression": _attr("customer-name", "c")},
                {"alias": "latest_order", "expression": _aggregate("max", _attr("order-date", "o"))},
            ],
            "group_by": [_attr("customer-name", "c")],
            "order_by": [{"expression": _attr("customer-name", "c"), "direction": "asc"}],
        },
    )
    assert {row["customer"]: row["latest_order"] for row in rows} == {
        "华东制造集团": "2026-04-22",
        "江北能源集团": "2026-02-18",
        "南方交通集团": "2026-03-09",
    }

    month = {"kind": "function", "function": "extract_month", "arguments": [_attr("order-date", "o")]}
    monthly_sales = _aggregate("sum", _attr("order-sales", "o"))
    rows = _execute_case(
        source, schema, mapping,
        _plan("2026 年按月累计销售额", ["order"], [], ["order-date", "order-sales", "order-status"], 3, complex_calculation=True),
        {
            "from_entity": {"binding": "o", "entity_id": "order"},
            "select": [
                {"alias": "month", "expression": month},
                {"alias": "monthly_sales", "expression": monthly_sales},
                {"alias": "cumulative_sales", "expression": {
                    "kind": "window", "function": "sum", "arguments": [monthly_sales],
                    "window_order_by": [{"expression": month, "direction": "asc"}],
                }},
            ],
            "where": _and(_date_range(), _completed()),
            "group_by": [month],
            "order_by": [{"expression": month, "direction": "asc"}],
        },
    )
    assert [(int(row["month"]), Decimal(row["cumulative_sales"])) for row in rows] == [
        (1, Decimal("300000.00")), (2, Decimal("660000.00")), (3, Decimal("910000.00")),
    ]

    year = {"kind": "function", "function": "extract_year", "arguments": [_attr("order-date", "o")]}
    def year_sales(target_year: int) -> dict:
        return _aggregate("sum", {
            "kind": "case",
            "whens": [{"when": _binary("=", year, _literal(target_year)), "then": _attr("order-sales", "o")}],
            "else_expression": _literal(0),
        })
    growth = _binary("*", _binary("/", _binary("-", year_sales(2026), year_sales(2025)), year_sales(2025)), _literal(100))
    rows = _execute_case(
        source, schema, mapping,
        _plan("2026 年销售额同比增长率", ["order"], [], ["order-date", "order-sales", "order-status"], 1, complex_calculation=True),
        {
            "from_entity": {"binding": "o", "entity_id": "order"},
            "select": [{"alias": "growth_rate", "expression": growth}],
            "where": _completed(),
        },
    )
    assert Decimal(rows[0]["growth_rate"]).quantize(Decimal("0.01")) == Decimal("355.00")

    order_subquery = {
        "from_entity": {"binding": "sub_order", "entity_id": "order"},
        "select": [{"expression": _aggregate("sum", _attr("order-sales", "sub_order"))}],
        "where": _and(_date_range("sub_order"), _completed("sub_order")),
    }
    completion_rate = _binary(
        "*",
        _binary(
            "/",
            {"kind": "subquery", "query": order_subquery},
            _aggregate("sum", _attr("target-amount", "t")),
        ),
        _literal(100),
    )
    rows = _execute_case(
        source, schema, mapping,
        _plan("2026 年销售目标完成率", ["target", "order"], [], ["target-year", "target-amount", "order-date", "order-status", "order-sales"], 1, complex_calculation=True),
        {
            "from_entity": {"binding": "t", "entity_id": "target"},
            "select": [{"alias": "completion_rate", "expression": completion_rate}],
            "where": _binary("=", _attr("target-year", "t"), _literal(2026)),
        },
    )
    assert Decimal(rows[0]["completion_rate"]).quantize(Decimal("0.01")) == Decimal("65.00")
