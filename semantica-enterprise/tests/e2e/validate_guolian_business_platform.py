#!/usr/bin/env python3
"""Validate the persistent 国联集团 acceptance dataset through public APIs.

The script intentionally checks business outcomes rather than only HTTP status
codes.  Credentials come from the environment and are never printed.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

import httpx


API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
USER_PASSWORD = os.environ["GUOLIAN_ACCEPTANCE_USER_PASSWORD"]


class Client:
    def __init__(self, username: str, password: str) -> None:
        self.http = httpx.Client(base_url=API, timeout=httpx.Timeout(30, read=240))
        login = self.call("POST", "/auth/login", json={"username": username, "password": password})
        self.user = login["user"]
        self.http.headers["Authorization"] = f"Bearer {login['access_token']}"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.http.request(method, path, **kwargs)
        if not response.is_success:
            raise AssertionError(f"{method} {path}: {response.status_code} {response.text[:1600]}")
        return response.json() if response.content else {}


def attr(attribute_id: str, binding: str) -> dict[str, Any]:
    return {"kind": "attribute", "attribute_id": attribute_id, "binding": binding}


def literal(value: Any) -> dict[str, Any]:
    return {"kind": "literal", "value": value}


def binary(operator: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "binary", "operator": operator, "left": left, "right": right}


def logical_and(*operands: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "logical", "operator": "and", "operands": list(operands)}


def aggregate(function: str, expression: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "aggregate", "function": function, "expression": expression}


def date_range(binding: str = "o") -> dict[str, Any]:
    return {
        "kind": "between",
        "expression": attr("order-date", binding),
        "lower": literal("2026-01-01"),
        "upper": literal("2026-12-31"),
    }


def completed(binding: str = "o") -> dict[str, Any]:
    return binary("=", attr("order-status", binding), literal("completed"))


def plan(question: str, entities: list[str], relationships: list[str], attributes: list[str]) -> dict[str, Any]:
    return {
        "version": "chuanshen.semantic-query-plan/v1",
        "original_question": question,
        "intent": question,
        "entity_ids": entities,
        "relationship_ids": relationships,
        "outputs": [{
            "position": 1,
            "label": "计算结果",
            "kind": "metric",
            "attribute_ids": attributes,
            "aggregate": "sum",
        }],
        "expected_cardinality": "single_value",
        "result_grain": "按集团经营指标口径计算",
        "metric_contract": {
            "kind": "sum",
            "base_entity_ids": entities,
            "base_relationship_ids": relationships,
        },
    }


def execute(admin: Client, mapping_version_id: str, question: str, plan_payload: dict[str, Any], ir: dict[str, Any]) -> dict[str, Any]:
    validated = admin.call("POST", "/structured-query/ir/validate", json={
        "mapping_version_id": mapping_version_id,
        "plan": plan_payload,
        "query_ir": ir,
    })
    assert validated["ok"] is True, validated
    compiled = admin.call("POST", "/structured-query/compile", json={
        "mapping_version_id": mapping_version_id,
        "plan": plan_payload,
        "query_ir": ir,
        "max_rows": 20,
    })
    assert "2026-01-01" not in compiled["sql_template"]
    assert "completed" not in compiled["sql_template"]
    result = admin.call("POST", "/structured-query/execute", json={
        "mapping_version_id": mapping_version_id,
        "plan": plan_payload,
        "query_ir": ir,
        "max_rows": 20,
    })
    assert result["status"] == "succeeded", (question, result)
    assert result["source_citations"], question
    return result


def main() -> int:
    admin = Client("admin", ADMIN_PASSWORD)
    spaces = {row["code"]: row for row in admin.call("GET", "/spaces")}
    required_spaces = {
        "gl-policy-acceptance", "gl-product-acceptance", "gl-procurement-acceptance",
        "gl-structured-acceptance", "gl-private-acceptance",
    }
    assert required_spaces <= set(spaces)
    assert len(admin.call("GET", "/org-units")) >= 14
    assert len(admin.call("GET", "/users")) >= 8

    models = admin.call("GET", "/model-configs")
    assert any(row["model_kind"] == "llm" and row["last_test_status"] == "success" for row in models)
    assert any(row["model_kind"] == "embedding" and row["provider"] == "fastembed" for row in models)
    assert all("api_key_encrypted" not in row and "api_key" not in row for row in models)

    sources = {row["name"]: row for row in admin.call("GET", "/sources")}
    postgres = sources["集团经营数据库（PostgreSQL）"]
    mysql = sources["集团经营数据库（MySQL 灾备）"]
    assert postgres["config"]["graph_materialization_enabled"] is True
    assert mysql["config"]["graph_materialization_enabled"] is False
    for source, object_id, order_id in (
        (postgres, "public.customers", "public.customers.id"),
        (mysql, "customers", "customers.id"),
    ):
        objects = admin.call("GET", f"/sources/{source['id']}/data-objects")["objects"]
        assert any(row["id"] == object_id for row in objects)
        preview = admin.call("POST", f"/sources/{source['id']}/data-preview", json={
            "object_id": object_id,
            "mode": "live",
            "page": 1,
            "page_size": 20,
            "order_by": order_id,
            "order_direction": "asc",
            "filters": [],
        })
        assert preview["current_page_rows"] == 4
        assert "password" not in {column["name"] for column in preview["columns"]}
        assert preview["rows"][0]["mobile"] == "138****2222"
        assert "never-return-this" not in repr(preview)

    mapping = next(
        row for row in admin.call("GET", f"/semantic-mappings?source_id={postgres['id']}")["items"]
        if row["name"] == "结构化经营全量映射"
    )
    mapping_version_id = mapping["active_version_id"]
    assert mapping_version_id

    graph_entities = admin.call(
        "GET", f"/knowledge/entities?space_id={spaces['gl-structured-acceptance']['id']}&limit=500"
    )["items"]
    materialized_entities = [
        row for row in graph_entities
        if (row.get("properties") or {}).get("source_id") == postgres["id"]
        and (row.get("properties") or {}).get("materialization") == "database_mapping"
    ]
    assert materialized_entities
    materialized_ids = {row["id"] for row in materialized_entities}
    graph_facts = admin.call(
        "GET", f"/knowledge/facts?space_id={spaces['gl-structured-acceptance']['id']}&limit=500"
    )["items"]
    materialized_facts = [
        row for row in graph_facts
        if row.get("subject_entity_id") in materialized_ids and row.get("source_chunk_id")
    ]
    assert materialized_facts

    total_ir = {
        "version": "chuanshen.query-ir/v1",
        "from_entity": {"binding": "o", "entity_id": "order"},
        "select": [{"alias": "total_sales", "expression": aggregate("sum", attr("order-sales", "o"))}],
        "where": logical_and(date_range(), completed()),
    }
    total_result = execute(
        admin,
        mapping_version_id,
        "2026 年已完成订单销售总额",
        plan("2026 年已完成订单销售总额", ["order"], [], ["order-sales", "order-date", "order-status"]),
        total_ir,
    )
    assert Decimal(total_result["rows"][0]["total_sales"]) == Decimal("910000.00")

    item_sales = binary("*", attr("item-quantity", "i"), attr("item-price", "i"))
    nexus_ir = {
        "version": "chuanshen.query-ir/v1",
        "from_entity": {"binding": "i", "entity_id": "item"},
        "joins": [
            {"binding": "p", "entity_id": "product", "relationship_id": "item-product", "from_binding": "i"},
            {"binding": "o", "entity_id": "order", "relationship_id": "item-order", "from_binding": "i"},
        ],
        "select": [{"alias": "nexusone_sales", "expression": aggregate("sum", item_sales)}],
        "where": logical_and(
            date_range(), completed(), binary("=", attr("product-name", "p"), literal("NexusOne")),
        ),
    }
    nexus_result = execute(
        admin,
        mapping_version_id,
        "2026 年 NexusOne 已完成订单销售额",
        plan(
            "2026 年 NexusOne 已完成订单销售额",
            ["item", "product", "order"],
            ["item-product", "item-order"],
            ["item-quantity", "item-price", "product-name", "order-date", "order-status"],
        ),
        nexus_ir,
    )
    assert Decimal(nexus_result["rows"][0]["nexusone_sales"]) == Decimal("400000.00")

    employee = Client("gl_employee", USER_PASSWORD)
    employee_spaces = {row["code"] for row in employee.call("GET", "/spaces")}
    assert {"gl-policy-acceptance", "gl-product-acceptance"} <= employee_spaces
    assert "gl-private-acceptance" not in employee_spaces
    denied = employee.http.post("/search", json={
        "query": "私有测试知识",
        "space_ids": [spaces["gl-private-acceptance"]["id"]],
        "use_reranker": False,
    })
    assert denied.status_code == 403

    documents = admin.call("GET", "/documents")
    acceptance_documents = [row for row in documents if row["space_id"] in {
        spaces["gl-policy-acceptance"]["id"],
        spaces["gl-product-acceptance"]["id"],
        spaces["gl-procurement-acceptance"]["id"],
    }]
    expected_titles = {
        "集团经营指标口径.md", "国联集团知识管理办法V1.0.docx",
        "国联集团人工智能十五五规划.pdf", "集团数据治理管理办法.docx",
        "供应商分级管理制度.docx", "国联集团采购管理办法.pdf",
        "重大风险事件报告制度.docx", "信息系统安全管理规范.docx",
        "人工智能十五五规划宣贯材料.pptx", "国联集团知识管理办法V2.0盖章扫描版.pdf",
        "NexusOne产品手册V2.0.pdf", "NexusOne技术参数与数据源.xlsx",
        "NexusOne产品介绍.pptx", "NexusOne常见问题FAQ.md", "NexusOne售后服务说明.docx",
        "NexusOne产品架构图.png", "NexusOne培训录音.wav", "NexusOne培训录音.mp3",
        "NexusOne部署演示.mp4", "NexusOne销售方案邮件.eml", "NexusOne交付资料包.zip",
        "关键器件采购框架协议.docx", "华星核心器件供应商准入材料.docx",
        "2026供应商评分表.xlsx", "华星核心器件风险处置会议纪要.docx",
        "华星核心器件产品目录.pdf", "供应商资质审查扫描件.pdf",
        "供应商风险评估报告.md", "供应商风险处置通知.eml",
    }
    by_title = {row["title"]: row for row in acceptance_documents}
    assert expected_titles <= set(by_title)
    for title in expected_titles:
        detail = admin.call("GET", f"/documents/{by_title[title]['id']}")
        assert detail["status"] == "ready", title
        assert detail.get("profile"), title
        current = next(row for row in detail["versions"] if row["id"] == detail["current_version_id"])
        assert (current.get("parse_summary") or {}).get("knowledge_status") == "published", title

    search = employee.call("POST", "/search", json={
        "query": "NexusOne 的产品定位和核心能力是什么？",
        "space_ids": [spaces["gl-product-acceptance"]["id"]],
        "top_k": 8,
        "use_keyword": True,
        "use_vector": True,
        "use_graph": True,
        "use_reranker": False,
        "filters": {},
    })
    assert search["items"]
    assert any("NexusOne" in item["text"] for item in search["items"])
    assert all(item["rank"] == index for index, item in enumerate(search["items"], start=1))

    print(json.dumps({
        "status": "passed",
        "spaces": len(required_spaces),
        "acceptance_documents": len(expected_titles),
        "postgres_preview_rows": 4,
        "mysql_preview_rows": 4,
        "materialized_entities": len(materialized_entities),
        "materialized_facts": len(materialized_facts),
        "total_sales_2026": str(total_result["rows"][0]["total_sales"]),
        "nexusone_sales_2026": str(nexus_result["rows"][0]["nexusone_sales"]),
        "search_results": len(search["items"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
