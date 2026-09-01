#!/usr/bin/env python3
"""Create an idempotent structured-data acceptance space and full semantic mapping.

The fixture password is read from the environment and is never printed.  The
script is intended for the isolated services from compose.structured-test.yaml.
"""

from __future__ import annotations

import json
import os

import httpx


BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:8080/api/v1").rstrip("/")
ADMIN_USERNAME = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "Admin@123456")
FIXTURE_PASSWORD = os.environ["STRUCTURED_FIXTURE_PASSWORD"]


def main() -> int:
    with httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(30, read=180)) as client:
        def call(method: str, path: str, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None

        login = call("POST", "/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        client.headers["Authorization"] = f"Bearer {login['access_token']}"

        spaces = call("GET", "/spaces")
        space = next((item for item in spaces if item["code"] == "structured-acceptance"), None)
        if space is None:
            space = call("POST", "/spaces", json={
                "code": "structured-acceptance",
                "name": "结构化经营数据验收库",
                "description": "MySQL/PostgreSQL 预览、语义映射、Text-to-SQL 与 DSH 多轮问答验收",
            })

        source_config = {
            "dialect": "postgresql",
            "host": "structured-postgres",
            "port": 5432,
            "database": "structured_fixture",
            "username": "structured_reader",
            "schema": "public",
            "include_tables": [
                "companies", "suppliers", "products", "customers", "orders",
                "order_items", "sales_targets", "risk_events", "activity_log",
            ],
            "knowledge_index_enabled": True,
            "realtime_query_enabled": True,
            "graph_materialization_enabled": False,
        }
        sources = call("GET", "/sources")
        source = next((item for item in sources if item["name"] == "结构化经营数据验收库"), None)
        if source is None:
            source = call("POST", "/sources", json={
                "space_id": space["id"],
                "name": "结构化经营数据验收库",
                "source_type": "database",
                "config": source_config,
                "secret": FIXTURE_PASSWORD,
            })
        else:
            source = call("PUT", f"/sources/{source['id']}", json={
                "name": source["name"],
                "space_id": space["id"],
                "config": source_config,
                "enabled": True,
            })

        tested = call("POST", "/sources/test", json={
            "source_id": source["id"], "source_type": "database", "config": source_config,
        })
        if tested["status"] != "success":
            raise RuntimeError("fixture database connection test did not succeed")
        schema = call("POST", f"/sources/{source['id']}/schema/discover")
        object_ids = {item["id"] for item in (schema.get("catalog") or {}).get("objects") or []}
        required_objects = {
            "public.customers", "public.orders", "public.order_items", "public.products",
            "public.sales_targets", "public.suppliers", "public.risk_events",
        }
        if not required_objects <= object_ids:
            raise RuntimeError(f"fixture schema is incomplete: {sorted(required_objects - object_ids)}")

        ontologies = call("GET", "/ontologies")
        ontology = next((item for item in ontologies if item["code"] == "structured-business"), None)
        if ontology is None:
            ontology = call("POST", "/ontologies", json={
                "space_id": space["id"],
                "code": "structured-business",
                "name": "结构化经营业务本体",
                "namespace": "urn:chuanshen:structured-business",
            })

        term_specs = {
            "customer": ("客户", "class"),
            "order": ("订单", "class"),
            "order_item": ("订单明细", "class"),
            "product": ("产品", "class"),
            "sales_target": ("销售目标", "class"),
            "supplier": ("供应商", "class"),
            "risk_event": ("风险事件", "class"),
            "customer_id": ("客户编号", "property"),
            "customer_name": ("客户名称", "property"),
            "order_id": ("订单编号", "property"),
            "order_date": ("订单日期", "property"),
            "order_status": ("订单状态", "property"),
            "order_region": ("销售区域", "property"),
            "sales_amount": ("销售额", "property"),
            "item_id": ("明细编号", "property"),
            "item_order_id": ("明细订单编号", "property"),
            "item_product_id": ("明细产品编号", "property"),
            "item_quantity": ("产品数量", "property"),
            "item_price": ("产品单价", "property"),
            "product_name": ("产品名称", "property"),
            "target_amount": ("目标金额", "property"),
            "target_year": ("目标年份", "property"),
            "risk_id": ("风险事件编号", "property"),
            "order_customer": ("订单所属客户", "relation"),
            "item_order": ("明细所属订单", "relation"),
            "item_product": ("明细对应产品", "relation"),
            "risk_supplier": ("风险涉及供应商", "relation"),
        }
        existing_terms = call("GET", f"/ontologies/{ontology['id']}/terms")
        terms = {item["code"]: item for item in existing_terms}
        for code, (label, term_type) in term_specs.items():
            if code not in terms:
                terms[code] = call("POST", f"/ontologies/{ontology['id']}/terms", json={
                    "code": code, "label": label, "term_type": term_type,
                })

        def oid(name: str) -> str:
            return f"public.{name}"

        entity_specs = {
            "customer": ("customers", "customer_name", "客户"),
            "order": ("orders", "order_no", "订单"),
            "item": ("order_items", "id", "订单明细"),
            "product": ("products", "product_name", "产品"),
            "target": ("sales_targets", "id", "销售目标"),
            "supplier": ("suppliers", "supplier_name", "供应商"),
            "risk": ("risk_events", "id", "风险事件"),
        }
        entity_term_codes = {
            "customer": "customer", "order": "order", "item": "order_item",
            "product": "product", "target": "sales_target", "supplier": "supplier",
            "risk": "risk_event",
        }
        entities = [{
            "id": entity_id,
            "ontology_term_id": terms[entity_term_codes[entity_id]]["id"],
            "label": label,
            "description": f"{label}表每行对应一个稳定业务实体",
            "fragments": [{
                "id": f"{entity_id}-main",
                "object_id": oid(table_name),
                "role": "primary",
                "identity_column_ids": [f"{oid(table_name)}.id"],
                "display_column_id": f"{oid(table_name)}.{display_name}",
                "grain": label,
            }],
        } for entity_id, (table_name, display_name, label) in entity_specs.items()]

        attribute_specs = {
            "customer-id": ("customer_id", "customer", "customers", "id", "integer", False),
            "customer-name": ("customer_name", "customer", "customers", "customer_name", "string", False),
            "order-id": ("order_id", "order", "orders", "id", "integer", False),
            "order-date": ("order_date", "order", "orders", "order_date", "date", False),
            "order-status": ("order_status", "order", "orders", "status", "string", False),
            "order-region": ("order_region", "order", "orders", "region", "string", False),
            "order-sales": ("sales_amount", "order", "orders", "sales_amount", "number", True),
            "item-id": ("item_id", "item", "order_items", "id", "integer", False),
            "item-order-id": ("item_order_id", "item", "order_items", "order_id", "integer", False),
            "item-product-id": ("item_product_id", "item", "order_items", "product_id", "integer", False),
            "item-quantity": ("item_quantity", "item", "order_items", "quantity", "integer", False),
            "item-price": ("item_price", "item", "order_items", "unit_price", "number", True),
            "product-name": ("product_name", "product", "products", "product_name", "string", False),
            "target-amount": ("target_amount", "target", "sales_targets", "target_amount", "number", True),
            "target-year": ("target_year", "target", "sales_targets", "target_year", "integer", False),
            "risk-id": ("risk_id", "risk", "risk_events", "id", "integer", False),
        }
        attributes = [{
            "id": attribute_id,
            "ontology_term_id": terms[term_code]["id"],
            "entity_id": entity_id,
            "fragment_id": f"{entity_id}-main",
            "column_id": f"{oid(table_name)}.{column_name}",
            "label": terms[term_code]["label"],
            "semantic_type": semantic_type,
            "is_measure": is_measure,
        } for attribute_id, (term_code, entity_id, table_name, column_name, semantic_type, is_measure) in attribute_specs.items()]

        relation_specs = [
            ("order-customer", "order_customer", "order", "customer", "many_to_one", "orders", "customer_id", "customers", "id"),
            ("item-order", "item_order", "item", "order", "many_to_one", "order_items", "order_id", "orders", "id"),
            ("item-product", "item_product", "item", "product", "many_to_one", "order_items", "product_id", "products", "id"),
            ("risk-supplier", "risk_supplier", "risk", "supplier", "many_to_one", "risk_events", "supplier_id", "suppliers", "id"),
        ]
        relationships = [{
            "id": relation_id,
            "ontology_term_id": terms[term_code]["id"],
            "label": terms[term_code]["label"],
            "from_entity_id": from_entity,
            "to_entity_id": to_entity,
            "cardinality": cardinality,
            "predicates": [{
                "left": {"object_id": oid(left_table), "column_id": f"{oid(left_table)}.{left_column}"},
                "operator": "=",
                "right": {"object_id": oid(right_table), "column_id": f"{oid(right_table)}.{right_column}"},
            }],
        } for relation_id, term_code, from_entity, to_entity, cardinality, left_table, left_column, right_table, right_column in relation_specs]

        mappings = call("GET", f"/semantic-mappings?source_id={source['id']}")["items"]
        mapping = next((item for item in mappings if item["name"] == "结构化经营全量映射"), None)
        if mapping is None:
            mapping = call("POST", "/semantic-mappings", json={
                "source_id": source["id"], "ontology_id": ontology["id"],
                "name": "结构化经营全量映射", "description": "确定性统计、跨表关系和多轮问答验收",
            })
        manifest = {
            "manifest_version": "chuanshen.semantic-mapping/v1",
            "source_id": source["id"],
            "ontology_id": ontology["id"],
            "schema_version_id": schema["id"],
            "entities": entities,
            "attributes": attributes,
            "relationships": relationships,
            "notes": ["隔离 Fixture 的完整经营语义映射；不包含数据库凭据"],
        }
        mapping = call("PUT", f"/semantic-mappings/{mapping['id']}", json={"manifest": manifest})
        version = mapping["latest_version"]
        validation = call("POST", f"/semantic-mappings/{mapping['id']}/validate?version_id={version['id']}")
        if not validation["validation"]["ok"]:
            raise RuntimeError(json.dumps(validation["validation"], ensure_ascii=False))
        mapping = call("POST", f"/semantic-mappings/{mapping['id']}/activate?version_id={version['id']}")

        print(json.dumps({
            "space_id": space["id"],
            "source_id": source["id"],
            "schema_version_id": schema["id"],
            "ontology_id": ontology["id"],
            "mapping_id": mapping["id"],
            "mapping_version_id": mapping["active_version_id"],
            "entities": len(entities),
            "attributes": len(attributes),
            "relationships": len(relationships),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
