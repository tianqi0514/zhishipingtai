"""Deterministic, side-effect-free rules for the Guolian demo fixture.

演示数据，不代表国联集团真实经营数据。
This file is parsed as a code knowledge asset. It does not connect to a
database, access the network, read secrets, or write files.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal


DISCLAIMER = "演示数据，不代表国联集团真实经营数据"
VALID_ORDER_STATUSES = frozenset({"已签约", "执行中", "已验收"})
APPROVED_STATUS = "已通过"

# These declarations are documentation fixtures for the three rules created
# through the platform API.  Semantica remains the execution engine; this file
# neither evaluates nor publishes inferred facts.
POLICY_SCOPE_RULE = {
    "if": [
        ("制度", "适用于", "上级组织"),
        ("上级组织", "管理", "下属单位"),
    ],
    "then": ("制度", "适用于", "下属单位"),
}
SUPPLIER_RISK_RULE = {
    "if": [
        ("供应商", "供应", "产品"),
        ("产品", "用于", "项目"),
        ("供应商", "存在风险", "风险事件"),
    ],
    "then": ("项目", "受到影响", "风险事件"),
}
SYSTEM_DEPENDENCY_RULE = {
    "if": [
        ("业务系统", "依赖", "被依赖系统"),
        ("被依赖系统", "使用", "基础组件"),
        ("基础组件", "存在风险", "维护事件"),
    ],
    "then": ("业务系统", "受到影响", "维护事件"),
}


def is_valid_procurement_order(order: Mapping[str, object]) -> bool:
    """Return whether an order belongs to the 2026 valid procurement total."""

    order_date = str(order.get("order_date") or "")
    return (
        "2026-01-01" <= order_date < "2027-01-01"
        and order.get("status") in VALID_ORDER_STATUSES
        and order.get("approval_status") == APPROVED_STATUS
    )


def valid_procurement_total(orders: Iterable[Mapping[str, object]]) -> Decimal:
    """Sum order-header amounts once after applying the policy filters."""

    seen_order_ids: set[str] = set()
    total = Decimal("0")
    for order in orders:
        order_id = str(order.get("order_id") or "")
        if not order_id or order_id in seen_order_ids:
            continue
        seen_order_ids.add(order_id)
        if is_valid_procurement_order(order):
            total += Decimal(str(order.get("contract_amount") or 0))
    return total


def risk_impact_path(
    supplier: str,
    product: str,
    project: str,
    risk: str,
) -> list[tuple[str, str, str]]:
    """Build the auditable premises expected by the Semantica Datalog rule."""

    return [
        (supplier, "供应", product),
        (product, "用于", project),
        (supplier, "存在风险", risk),
        (project, "受到影响", risk),
    ]
