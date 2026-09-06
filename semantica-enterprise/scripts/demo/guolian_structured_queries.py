"""SQL-free governed structured-query definitions for the Guolian demo.

The declarations below are semantic plans, not expected answers or SQL.  They
are sealed inside the activated mapping and run against live MySQL/PostgreSQL
through the regular strict Plan -> IR -> deterministic compiler path.
"""

from __future__ import annotations

from typing import Any, Sequence


def _attribute(attribute_id: str, binding: str) -> dict[str, Any]:
    return {"kind": "attribute", "attribute_id": attribute_id, "binding": binding}


def _literal(value: Any) -> dict[str, Any]:
    return {"kind": "literal", "value": value}


def _aggregate(function: str, expression: dict[str, Any] | None = None, *, distinct: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": "aggregate", "function": function, "distinct": distinct}
    if expression is not None:
        result["expression"] = expression
    return result


def _binary(operator: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "binary", "operator": operator, "left": left, "right": right}


def _eq(attribute_id: str, binding: str, value: Any) -> dict[str, Any]:
    return _binary("=", _attribute(attribute_id, binding), _literal(value))


def _in_values(attribute_id: str, binding: str, values: Sequence[Any]) -> dict[str, Any]:
    return {
        "kind": "in",
        "expression": _attribute(attribute_id, binding),
        "options": [_literal(value) for value in values],
    }


def _and(*expressions: dict[str, Any] | None) -> dict[str, Any] | None:
    values = [item for item in expressions if item]
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return {"kind": "logical", "operator": "and", "operands": values}


def _or(*expressions: dict[str, Any] | None) -> dict[str, Any] | None:
    values = [item for item in expressions if item]
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return {"kind": "logical", "operator": "or", "operands": values}


def _exists(
    relationship_id: str,
    source_binding: str,
    target_binding: str,
    target_entity_id: str,
    *operands: dict[str, Any] | None,
    negated: bool = False,
) -> dict[str, Any]:
    return {
        "kind": "exists",
        "relationship_id": relationship_id,
        "source_binding": source_binding,
        "target_binding": target_binding,
        "target_entity_id": target_entity_id,
        "operands": [item for item in operands if item],
        "negated": negated,
    }


def _year(attribute_id: str, binding: str, value: int = 2026) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        _binary(">=", _attribute(attribute_id, binding), _literal(f"{value:04d}-01-01")),
        _binary("<", _attribute(attribute_id, binding), _literal(f"{value + 1:04d}-01-01")),
    )


def _effective(binding: str, suffix: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        _in_values("order-status", binding, ["signed", "executing", "accepted"]),
        _exists(
            "approval-order", binding, f"approved_{suffix}", "approval-record",
            _eq("approval-decision", f"approved_{suffix}", "approved"),
        ),
    )


def _filter(attribute_id: str, operator: str, value: Any = None, upper: Any = None) -> dict[str, Any]:
    return {"attribute_id": attribute_id, "operator": operator, "value": value, "upper": upper}


EFFECTIVE_FILTERS = [
    _filter("order-status", "in", ["signed", "executing", "accepted"]),
    _filter("approval-decision", "eq", "approved"),
]
ORDER_YEAR_FILTERS = [
    _filter("order-date", "gte", "2026-01-01"),
    _filter("order-date", "lt", "2027-01-01"),
]
RISK_YEAR_FILTERS = [
    _filter("risk-date", "gte", "2026-01-01"),
    _filter("risk-date", "lt", "2027-01-01"),
]


def _output(position: int, label: str, kind: str, attributes: Sequence[str] = (), aggregate: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "position": position,
        "label": label,
        "kind": kind,
        "attribute_ids": list(attributes),
    }
    if aggregate:
        result["aggregate"] = aggregate
    return result


def _plan(
    question: str,
    *,
    entities: Sequence[str],
    relationships: Sequence[str],
    outputs: Sequence[dict[str, Any]],
    expected: str,
    grain: str,
    filters: Sequence[dict[str, Any]] = (),
    group_by: Sequence[str] = (),
    ordering: Sequence[dict[str, Any]] = (),
    distinct_policy: str = "",
    calculations: Sequence[dict[str, Any]] = (),
    result_step_id: str | None = None,
) -> dict[str, Any]:
    return {
        "original_question": question,
        "intent": question,
        "entity_ids": list(entities),
        "relationship_ids": list(relationships),
        "outputs": list(outputs),
        "filters": list(filters),
        "group_by_attribute_ids": list(group_by),
        "ordering": list(ordering),
        "expected_cardinality": expected,
        "result_grain": grain,
        "distinct_policy": distinct_policy,
        "time_range": "2026" if any("2026" in str(item) for item in filters) else "",
        "calculation_steps": list(calculations),
        "result_step_id": result_step_id,
        "evidence_constraints": ["结果必须来自当前数据库，并遵守已激活语义映射和有效采购口径。"],
    }


def _template(
    template_id: str,
    label: str,
    aliases: Sequence[str],
    plan: dict[str, Any],
    query_ir: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": template_id,
        "label": label,
        "aliases": list(aliases),
        "default_year": 2026,
        "plan": plan,
        "query_ir": query_ir,
        "description": "由管理员随语义映射发布的受管理查询；不保存 SQL 或预计算答案。",
    }


def guolian_governed_query_templates() -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []

    total_status, total_approval = _effective("effective_total_orders", "effective_total")
    total_start, total_end = _year("order-date", "effective_total_orders")
    templates.append(_template(
        "effective-procurement-total", "集团演示有效采购总额",
        ["集团演示有效采购总额是多少", "集团有效采购总额"],
        _plan(
            "2026 年集团演示有效采购总额是多少？",
            entities=["purchase-order", "approval-record"],
            relationships=["approval-order"],
            outputs=[_output(1, "有效采购总额", "metric", ["order-amount"], "sum")],
            filters=[*EFFECTIVE_FILTERS, *ORDER_YEAR_FILTERS],
            expected="single_value",
            grain="集团汇总",
            distinct_policy="按订单头汇总，审批条件使用 EXISTS，避免重复累计",
        ),
        {
            "from_entity": {"binding": "effective_total_orders", "entity_id": "purchase-order"},
            "select": [{
                "alias": "total",
                "expression": _aggregate(
                    "sum", _attribute("order-amount", "effective_total_orders"),
                ),
            }],
            "where": _and(total_status, total_approval, total_start, total_end),
        },
    ))

    top_status, top_approval = _effective("top_supplier_orders", "top_supplier")
    top_start, top_end = _year("order-date", "top_supplier_orders")
    top_amount = _aggregate("sum", _attribute("order-amount", "top_supplier_orders"))
    templates.append(_template(
        "highest-effective-procurement-supplier", "有效采购金额最高的供应商",
        ["有效采购金额最高的供应商是谁", "有效采购金额最高供应商"],
        _plan(
            "有效采购金额最高的供应商是谁？",
            entities=["purchase-order", "supplier", "approval-record"],
            relationships=["order-supplier", "approval-order"],
            outputs=[
                _output(1, "供应商", "attribute", ["supplier-name"]),
                _output(2, "有效采购金额", "metric", ["order-amount"], "sum"),
            ],
            filters=[*EFFECTIVE_FILTERS, *ORDER_YEAR_FILTERS],
            group_by=["supplier-name"],
            ordering=[{"attribute_ids": ["order-amount"], "direction": "desc"}],
            expected="single_row",
            grain="供应商",
        ),
        {
            "from_entity": {"binding": "top_supplier_orders", "entity_id": "purchase-order"},
            "joins": [{
                "binding": "top_supplier", "entity_id": "supplier",
                "relationship_id": "order-supplier", "from_binding": "top_supplier_orders",
            }],
            "select": [
                {
                    "alias": "supplier",
                    "expression": _attribute("supplier-name", "top_supplier"),
                },
                {"alias": "amount", "expression": top_amount},
            ],
            "where": _and(top_status, top_approval, top_start, top_end),
            "group_by": [_attribute("supplier-name", "top_supplier")],
            "order_by": [{"expression": top_amount, "direction": "desc"}],
            "limit": 1,
        },
    ))

    risk_count_start, risk_count_end = _year("risk-date", "high_risk_count")
    templates.append(_template(
        "high-risk-event-count", "高风险供应商事件数量",
        ["高风险供应商事件共有多少起", "高风险事件共有多少起"],
        _plan(
            "2026 年高风险供应商事件共有多少起？",
            entities=["risk-event"],
            relationships=[],
            outputs=[_output(1, "高风险事件数量", "metric", ["risk-type"], "count")],
            filters=[_filter("risk-event-level", "eq", "high"), *RISK_YEAR_FILTERS],
            expected="single_value",
            grain="风险事件",
        ),
        {
            "from_entity": {"binding": "high_risk_count", "entity_id": "risk-event"},
            "select": [{
                "alias": "count",
                "expression": _aggregate("count", _attribute("risk-type", "high_risk_count")),
            }],
            "where": _and(
                _eq("risk-event-level", "high_risk_count", "high"),
                risk_count_start,
                risk_count_end,
            ),
        },
    ))

    gross_start, gross_end = _year("order-date", "gross_orders")
    templates.append(_template(
        "gross-procurement-excluding-cancelled", "只排除已取消订单的采购金额",
        ["只排除已取消订单、暂不应用审批条件", "只排除已取消订单"],
        _plan(
            "只排除已取消订单、暂不应用审批条件时，2026 年订单金额是多少？",
            entities=["purchase-order"],
            relationships=[],
            outputs=[
                _output(1, "未应用审批口径的订单金额", "metric", ["order-gross-amount"], "sum"),
            ],
            filters=[
                _filter("order-status", "ne", "cancelled"),
                *ORDER_YEAR_FILTERS,
            ],
            expected="single_value",
            grain="集团订单头汇总",
        ),
        {
            "from_entity": {"binding": "gross_orders", "entity_id": "purchase-order"},
            "select": [{
                "alias": "total",
                "expression": _aggregate(
                    "sum", _attribute("order-gross-amount", "gross_orders"),
                ),
            }],
            "where": _and(
                _binary(
                    "!=",
                    _attribute("order-status", "gross_orders"),
                    _literal("cancelled"),
                ),
                gross_start,
                gross_end,
            ),
        },
    ))

    status, approval = _effective("orders_by_org", "by_org")
    start, end = _year("order-date", "orders_by_org")
    amount = _aggregate("sum", _attribute("order-amount", "orders_by_org"))
    templates.append(_template(
        "effective-procurement-by-org", "各单位有效采购金额",
        ["各单位有效采购金额分别是多少", "按单位统计有效采购金额"],
        _plan(
            "2026 年各单位有效采购金额分别是多少？",
            entities=["purchase-order", "org-unit", "approval-record"],
            relationships=["order-org", "approval-order"],
            outputs=[_output(1, "单位", "attribute", ["org-name"]), _output(2, "有效采购金额", "metric", ["order-amount"], "sum")],
            filters=[*EFFECTIVE_FILTERS, *ORDER_YEAR_FILTERS], group_by=["org-name"],
            expected="multiple_rows", grain="单位",
        ),
        {
            "from_entity": {"binding": "orders_by_org", "entity_id": "purchase-order"},
            "joins": [{"binding": "org", "entity_id": "org-unit", "relationship_id": "order-org", "from_binding": "orders_by_org"}],
            "select": [{"alias": "org_unit", "expression": _attribute("org-name", "org")}, {"alias": "amount", "expression": amount}],
            "where": _and(status, approval, start, end),
            "group_by": [_attribute("org-name", "org")],
            "order_by": [{"expression": amount, "direction": "desc"}],
        },
    ))

    status, approval = _effective("product_orders", "product")
    start, end = _year("order-date", "product_orders")
    product_path = _exists(
        "item-order", "product_orders", "product_item", "purchase-item",
        _exists("item-product", "product_item", "selected_product", "product", _eq("product-name", "selected_product", "NexusOne")),
    )
    templates.append(_template(
        "nexusone-effective-procurement", "NexusOne 有效采购金额",
        ["NexusOne 有效采购金额是多少", "NexusOne 采购金额"],
        _plan(
            "2026 年 NexusOne 有效采购金额是多少？",
            entities=["purchase-order", "approval-record", "purchase-item", "product"],
            relationships=["approval-order", "item-order", "item-product"],
            outputs=[_output(1, "产品", "attribute", ["product-name"]), _output(2, "有效采购金额", "metric", ["order-amount"], "sum")],
            filters=[*EFFECTIVE_FILTERS, *ORDER_YEAR_FILTERS, _filter("product-name", "eq", "NexusOne")],
            expected="single_row", grain="产品",
        ),
        {
            "from_entity": {"binding": "product_orders", "entity_id": "purchase-order"},
            "select": [{"alias": "product", "expression": _literal("NexusOne")}, {"alias": "amount", "expression": _aggregate("sum", _attribute("order-amount", "product_orders"))}],
            "where": _and(status, approval, start, end, product_path),
        },
    ))

    risk_start, risk_end = _year("risk-date", "high_risk_event")
    high_risk = _exists(
        "risk-supplier", "risk_supplier", "high_risk_event", "risk-event",
        _eq("risk-event-level", "high_risk_event", "high"), risk_start, risk_end,
    )
    templates.append(_template(
        "high-risk-supplier-count", "高风险供应商去重数量",
        ["高风险供应商去重后有多少家", "高风险供应商有多少家"],
        _plan(
            "高风险供应商去重后有多少家？", entities=["supplier", "risk-event"], relationships=["risk-supplier"],
            outputs=[_output(1, "高风险供应商数量", "metric", ["supplier-name"], "count")],
            filters=[_filter("risk-event-level", "eq", "high"), *RISK_YEAR_FILTERS], expected="single_value", grain="供应商", distinct_policy="按供应商去重",
        ),
        {
            "from_entity": {"binding": "risk_supplier", "entity_id": "supplier"},
            "select": [{"alias": "count_distinct", "expression": _aggregate("count", _attribute("supplier-name", "risk_supplier"), distinct=True)}],
            "where": high_risk,
        },
    ))

    risk_start, risk_end = _year("risk-date", "impact_risk")
    supplier_filter = _exists("risk-supplier", "impact_risk", "impact_supplier", "supplier", _eq("supplier-name", "impact_supplier", "东方智造"))
    risk_filter = _exists(
        "risk-product", "impact_product", "impact_risk", "risk-event",
        _eq("risk-event-level", "impact_risk", "high"), risk_start, risk_end, supplier_filter,
    )
    product_filter = _exists("project-product-product", "impact_bridge", "impact_product", "product", risk_filter)
    impact_path = _exists(
        "project-product-project", "impact_project", "impact_bridge", "project-product",
        _eq("project-product-active", "impact_bridge", True), product_filter,
    )
    templates.append(_template(
        "supplier-high-risk-affected-project-count", "供应商高风险事件影响项目数量",
        ["东方智造的高风险事件影响多少个项目", "高风险事件影响多少个项目"],
        _plan(
            "东方智造的高风险事件影响多少个项目？",
            entities=["project", "project-product", "product", "risk-event", "supplier"],
            relationships=["project-product-project", "project-product-product", "risk-product", "risk-supplier"],
            outputs=[_output(1, "受影响项目数量", "metric", ["project-name"], "count")],
            filters=[_filter("project-product-active", "eq", True), _filter("risk-event-level", "eq", "high"), *RISK_YEAR_FILTERS, _filter("supplier-name", "eq", "东方智造")],
            expected="single_value", grain="项目", distinct_policy="按项目去重",
        ),
        {
            "from_entity": {"binding": "impact_project", "entity_id": "project"},
            "select": [{"alias": "count_distinct", "expression": _aggregate("count", _attribute("project-name", "impact_project"), distinct=True)}],
            "where": impact_path,
        },
    ))

    status, approval = _effective("monthly_orders", "monthly")
    start, end = _year("order-date", "monthly_orders")
    year_value = {"kind": "function", "function": "extract_year", "arguments": [_attribute("order-date", "monthly_orders")]}
    month_value = {"kind": "function", "function": "extract_month", "arguments": [_attribute("order-date", "monthly_orders")]}
    monthly_amount = _aggregate("sum", _attribute("order-amount", "monthly_orders"))
    templates.append(_template(
        "monthly-effective-procurement", "按月统计有效采购金额",
        ["按月份统计 2026 年有效采购金额", "按月统计有效采购金额"],
        _plan(
            "按月份统计 2026 年有效采购金额。", entities=["purchase-order", "approval-record"], relationships=["approval-order"],
            outputs=[_output(1, "年份", "derived", ["order-date"]), _output(2, "月份", "derived", ["order-date"]), _output(3, "有效采购金额", "metric", ["order-amount"], "sum")],
            filters=[*EFFECTIVE_FILTERS, *ORDER_YEAR_FILTERS], group_by=["order-date"], expected="multiple_rows", grain="自然月",
        ),
        {
            "from_entity": {"binding": "monthly_orders", "entity_id": "purchase-order"},
            "select": [{"alias": "year_num", "expression": year_value}, {"alias": "month_num", "expression": month_value}, {"alias": "amount", "expression": monthly_amount}],
            "where": _and(status, approval, start, end),
            "group_by": [year_value, month_value],
            "order_by": [{"expression": year_value, "direction": "asc"}, {"expression": month_value, "direction": "asc"}],
        },
    ))

    status, approval = _effective("latest_order", "latest")
    start, end = _year("order-date", "latest_order")
    newer_status, newer_approval = _effective("newer_order", "newer")
    newer_start, newer_end = _year("order-date", "newer_order")
    supplier = _attribute("supplier-name", "latest_supplier")
    current_date = _attribute("order-date", "latest_order")
    current_number = _attribute("order-no", "latest_order")
    newer_date = _attribute("order-date", "newer_order")
    newer_number = _attribute("order-no", "newer_order")
    has_newer_effective_order = _exists(
        "order-supplier",
        "latest_supplier",
        "newer_order",
        "purchase-order",
        newer_status,
        newer_approval,
        newer_start,
        newer_end,
        _or(
            _binary(">", newer_date, current_date),
            _and(
                _binary("=", newer_date, current_date),
                _binary(">", newer_number, current_number),
            ),
        ),
        negated=True,
    )
    templates.append(_template(
        "latest-effective-order-per-supplier", "每个供应商最近一笔有效订单",
        ["每个供应商最近一笔符合制度口径的订单是什么", "每家供应商最近有效订单"],
        _plan(
            "每个供应商最近一笔符合制度口径的订单是什么？",
            entities=["purchase-order", "supplier", "approval-record"], relationships=["order-supplier", "approval-order"],
            outputs=[_output(1, "供应商", "attribute", ["supplier-name"]), _output(2, "订单编号", "attribute", ["order-no"]), _output(3, "订单日期", "attribute", ["order-date"])],
            filters=[*EFFECTIVE_FILTERS, *ORDER_YEAR_FILTERS],
            ordering=[{"attribute_ids": ["supplier-code"], "direction": "asc"}],
            expected="multiple_rows", grain="每个供应商", distinct_policy="每个供应商按采购日期倒序取第一笔",
        ),
        {
            "from_entity": {"binding": "latest_order", "entity_id": "purchase-order"},
            "joins": [{"binding": "latest_supplier", "entity_id": "supplier", "relationship_id": "order-supplier", "from_binding": "latest_order"}],
            "select": [{"alias": "supplier", "expression": supplier}, {"alias": "order_id", "expression": current_number}, {"alias": "order_date", "expression": current_date}],
            "where": _and(status, approval, start, end, has_newer_effective_order),
            "order_by": [{"expression": _attribute("supplier-code", "latest_supplier"), "direction": "asc"}],
        },
    ))

    missing_status, missing_approval = _effective("missing_order", "missing")
    no_order = _exists(
        "order-supplier", "missing_supplier", "missing_order", "purchase-order",
        missing_status, missing_approval, negated=True,
    )
    templates.append(_template(
        "suppliers-without-effective-orders", "没有有效采购订单的供应商",
        ["哪些供应商没有符合制度口径的采购订单", "没有有效订单的供应商"],
        _plan(
            "哪些供应商没有符合制度口径的采购订单？",
            entities=["supplier", "purchase-order", "approval-record"], relationships=["order-supplier", "approval-order"],
            outputs=[_output(1, "供应商", "attribute", ["supplier-name"])], filters=EFFECTIVE_FILTERS,
            expected="multiple_rows", grain="供应商",
        ),
        {
            "from_entity": {"binding": "missing_supplier", "entity_id": "supplier"},
            "select": [{"alias": "supplier", "expression": _attribute("supplier-name", "missing_supplier")}],
            "where": no_order,
            "order_by": [{"expression": _attribute("supplier-name", "missing_supplier"), "direction": "asc"}],
        },
    ))

    def uses_product(product_name: str, suffix: str) -> dict[str, Any]:
        bridge = f"bridge_{suffix}"
        product = f"product_{suffix}"
        return _exists(
            "project-product-project", "intersection_project", bridge, "project-product",
            _eq("project-product-active", bridge, True),
            _exists("project-product-product", bridge, product, "product", _eq("product-name", product, product_name)),
        )

    templates.append(_template(
        "projects-using-both-products", "同时使用 NexusOne 和智慧流程引擎的项目",
        ["哪些项目同时使用 NexusOne 和智慧流程引擎", "同时使用两个产品的项目"],
        _plan(
            "哪些项目同时使用 NexusOne 和智慧流程引擎？",
            entities=["project", "project-product", "product"], relationships=["project-product-project", "project-product-product"],
            outputs=[_output(1, "项目", "attribute", ["project-name"])],
            filters=[_filter("project-product-active", "eq", True), _filter("product-name", "eq", "NexusOne"), _filter("product-name", "eq", "智慧流程引擎")],
            expected="multiple_rows", grain="项目", distinct_policy="项目必须分别存在两条产品关联",
        ),
        {
            "from_entity": {"binding": "intersection_project", "entity_id": "project"},
            "select": [{"alias": "project", "expression": _attribute("project-name", "intersection_project")}],
            "where": _and(uses_product("NexusOne", "nexusone"), uses_product("智慧流程引擎", "workflow")),
            "distinct": True,
        },
    ))

    status, approval = _effective("amount_count_orders", "amount_count")
    start, end = _year("order-date", "amount_count_orders")
    templates.append(_template(
        "effective-amount-and-order-count", "有效采购金额与有效订单数量",
        ["有效采购金额与有效订单数量分别是多少", "有效金额和有效订单数"],
        _plan(
            "有效采购金额与有效订单数量分别是多少？",
            entities=["purchase-order", "approval-record"], relationships=["approval-order"],
            outputs=[_output(1, "有效采购金额", "metric", ["order-amount"], "sum"), _output(2, "有效订单数量", "metric", ["order-no"], "count")],
            filters=[*EFFECTIVE_FILTERS, *ORDER_YEAR_FILTERS], expected="single_row", grain="集团汇总", distinct_policy="订单数量按订单编号去重",
        ),
        {
            "from_entity": {"binding": "amount_count_orders", "entity_id": "purchase-order"},
            "select": [
                {"alias": "amount_sum", "expression": _aggregate("sum", _attribute("order-amount", "amount_count_orders"))},
                {"alias": "order_count", "expression": _aggregate("count", _attribute("order-no", "amount_count_orders"), distinct=True)},
            ],
            "where": _and(status, approval, start, end),
        },
    ))

    status, approval = _effective("effective_order_count", "effective_count")
    start, end = _year("order-date", "effective_order_count")
    templates.append(_template(
        "effective-order-and-supplier-count", "有效订单数与去重供应商数",
        [
            "符合制度口径的订单有多少笔，涉及多少家供应商",
            "有效订单有多少笔，涉及多少家供应商",
        ],
        _plan(
            "符合制度口径的订单有多少笔，涉及多少家供应商？",
            entities=["purchase-order", "supplier", "approval-record"],
            relationships=["order-supplier", "approval-order"],
            outputs=[
                _output(1, "有效订单数量", "metric", ["order-no"], "count"),
                _output(2, "供应商数量", "metric", ["supplier-code"], "count"),
            ],
            filters=[*EFFECTIVE_FILTERS, *ORDER_YEAR_FILTERS],
            expected="single_row",
            grain="集团汇总",
            distinct_policy="订单按订单编号去重，供应商按稳定供应商标识去重",
        ),
        {
            "from_entity": {"binding": "effective_order_count", "entity_id": "purchase-order"},
            "joins": [{
                "binding": "effective_order_supplier",
                "entity_id": "supplier",
                "relationship_id": "order-supplier",
                "from_binding": "effective_order_count",
            }],
            "select": [
                {
                    "alias": "order_count",
                    "expression": _aggregate(
                        "count", _attribute("order-no", "effective_order_count"), distinct=True,
                    ),
                },
                {
                    "alias": "supplier_count_distinct",
                    "expression": _aggregate(
                        "count", _attribute("supplier-code", "effective_order_supplier"), distinct=True,
                    ),
                },
            ],
            "where": _and(status, approval, start, end),
        },
    ))

    status, approval = _effective("dedup_orders", "dedup")
    start, end = _year("order-date", "dedup_orders")
    has_item = _exists("item-order", "dedup_orders", "dedup_item", "purchase-item")
    templates.append(_template(
        "deduplicated-order-header-total", "关联明细后避免重复累计订单金额",
        ["关联订单明细后，如何避免重复累计订单金额", "订单明细去重累计订单金额"],
        _plan(
            "关联订单明细后，如何避免重复累计订单金额？",
            entities=["purchase-order", "purchase-item", "approval-record"], relationships=["item-order", "approval-order"],
            outputs=[_output(1, "去重订单总额", "metric", ["order-amount"], "sum")],
            filters=[*EFFECTIVE_FILTERS, *ORDER_YEAR_FILTERS], expected="single_value", grain="订单头", distinct_policy="明细仅用 EXISTS 限定，订单头每笔只累计一次",
        ),
        {
            "from_entity": {"binding": "dedup_orders", "entity_id": "purchase-order"},
            "select": [{"alias": "deduplicated_order_total", "expression": _aggregate("sum", _attribute("order-amount", "dedup_orders"))}],
            "where": _and(status, approval, start, end, has_item),
        },
    ))

    def ranking_total(suffix: str) -> dict[str, Any]:
        ranking_start, ranking_end = _year("order-date", "ranking_order")
        ranking_approval = _exists(
            "approval-order", "ranking_order", f"ranking_approval_{suffix}", "approval-record",
            _eq("approval-decision", f"ranking_approval_{suffix}", "approved"),
        )
        valid_amount = {
            "kind": "case",
            "whens": [{
                "when": _and(
                    _in_values("order-status", "ranking_order", ["signed", "executing", "accepted"]),
                    ranking_approval, ranking_start, ranking_end,
                ),
                "then": _attribute("order-amount", "ranking_order"),
            }],
            "else_expression": _literal(0),
        }
        return _aggregate("sum", valid_amount)

    select_total = ranking_total("select")
    window_total = ranking_total("window")
    order_total = ranking_total("order")
    ranking = {
        "kind": "window", "function": "dense_rank", "arguments": [],
        "window_order_by": [{"expression": window_total, "direction": "desc"}],
    }
    templates.append(_template(
        "top-suppliers-including-zero", "按有效采购金额列出前五名供应商，包括没有有效订单的供应商",
        ["前五名供应商，包括没有有效订单的供应商", "供应商有效采购金额排名"],
        _plan(
            "按有效采购金额列出前五名供应商，包括没有有效订单的供应商。",
            entities=["supplier", "purchase-order", "approval-record"], relationships=["order-supplier", "approval-order"],
            outputs=[_output(1, "排名", "derived"), _output(2, "供应商", "attribute", ["supplier-name"]), _output(3, "有效采购金额", "derived")],
            expected="multiple_rows", grain="供应商", distinct_policy="供应商全集左连接订单，未命中金额计零",
            calculations=[
                {"step_id": "population", "kind": "filter", "operation": "left_population", "entity_ids": ["supplier", "purchase-order", "approval-record"], "relationship_ids": ["order-supplier", "approval-order"], "attribute_ids": ["supplier-name", "order-status", "order-date", "approval-decision"]},
                {"step_id": "amount", "kind": "aggregate", "operation": "conditional_sum", "input_step_ids": ["population"], "entity_ids": ["purchase-order"], "attribute_ids": ["order-amount"], "group_by_attribute_ids": ["supplier-name"]},
                {"step_id": "rank", "kind": "rank", "operation": "dense_rank", "input_step_ids": ["amount"], "entity_ids": ["supplier"], "attribute_ids": ["supplier-name", "order-amount"]},
            ],
            result_step_id="rank",
        ),
        {
            "from_entity": {"binding": "ranking_supplier", "entity_id": "supplier"},
            "joins": [{"binding": "ranking_order", "entity_id": "purchase-order", "relationship_id": "order-supplier", "from_binding": "ranking_supplier", "join_type": "left"}],
            "select": [{"alias": "ranking", "expression": ranking}, {"alias": "supplier", "expression": _attribute("supplier-name", "ranking_supplier")}, {"alias": "amount", "expression": select_total}],
            "group_by": [_attribute("supplier-name", "ranking_supplier")],
            "order_by": [{"expression": order_total, "direction": "desc"}, {"expression": _attribute("supplier-name", "ranking_supplier"), "direction": "asc"}],
            "limit": 5,
        },
    ))

    return templates
