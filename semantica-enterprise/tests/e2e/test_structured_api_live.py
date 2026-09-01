from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest
from sqlalchemy import create_engine, text


BASE_URL = os.getenv("E2E_BASE_URL", "").rstrip("/")
ADMIN_USERNAME = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
FIXTURE_PASSWORD = os.getenv("STRUCTURED_FIXTURE_PASSWORD", "")
FIXTURE_PG_ADMIN_PASSWORD = os.getenv("STRUCTURED_FIXTURE_PG_ADMIN_PASSWORD", "")
KEEP_DATA = os.getenv("KEEP_STRUCTURED_E2E_DATA") == "1"


pytestmark = pytest.mark.skipif(
    not BASE_URL or not ADMIN_PASSWORD or not FIXTURE_PASSWORD,
    reason="live structured API E2E requires the API and isolated fixture credentials",
)


def test_structured_schema_mapping_preview_and_query_end_to_end() -> None:
    suffix = uuid.uuid4().hex[:8]
    created: dict[str, object] = {"documents": []}
    with httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(30, read=180)) as client:
        def call(method: str, path: str, **kwargs):
            response = client.request(method, path, **kwargs)
            assert response.is_success, f"{method} {path}: {response.status_code} {response.text[:1200]}"
            return response.json()

        login = call("POST", "/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        client.headers["Authorization"] = f"Bearer {login['access_token']}"
        assert call("GET", "/auth/me")["username"] == ADMIN_USERNAME

        try:
            space = call("POST", "/spaces", json={
                "code": f"structured-e2e-{suffix}",
                "name": f"结构化语义查询验收 {suffix}",
                "description": "MySQL/PostgreSQL 结构发现、预览、映射和确定性查询验收数据",
            })
            created["space"] = space["id"]

            source_config = {
                "dialect": "postgresql",
                "host": "structured-postgres",
                "port": 5432,
                "database": "structured_fixture",
                "username": "structured_reader",
                "schema": "public",
                "include_tables": ["customers", "orders", "order_items", "products", "sales_targets", "risk_events", "activity_log"],
                "knowledge_index_enabled": True,
                "realtime_query_enabled": True,
                "graph_materialization_enabled": False,
            }
            source = call("POST", "/sources", json={
                "space_id": space["id"],
                "name": f"经营数据验收库 {suffix}",
                "source_type": "database",
                "config": source_config,
                "secret": FIXTURE_PASSWORD,
            })
            created["source"] = source["id"]

            connection = call("POST", "/sources/test", json={
                "source_id": source["id"], "source_type": "database", "config": source_config,
            })
            assert connection["status"] == "success"
            assert "connection_string" not in connection

            discovered = call("POST", f"/sources/{source['id']}/schema/discover")
            assert discovered["object_count"] >= 7
            assert discovered["primary_key_count"] >= 6
            schema_id = discovered["id"]
            objects_response = call("GET", f"/sources/{source['id']}/data-objects")
            objects = {item["id"]: item for item in objects_response["objects"]}
            assert {"public.customers", "public.orders", "public.products"} <= set(objects)
            assert objects["public.customers"]["primary_key"] == ["id"]
            assert objects["public.activity_log"]["primary_key"] == []
            assert any(item["referred_table"] == "customers" for item in objects["public.orders"]["foreign_keys"])

            preview = call("POST", f"/sources/{source['id']}/data-preview", json={
                "object_id": "public.customers", "mode": "live", "page": 1, "page_size": 2,
                "order_by": "public.customers.id", "order_direction": "asc",
                "filters": [{"column_id": "public.customers.customer_name", "operator": "contains", "value": "集团"}],
            })
            assert preview["mode"] == "live"
            assert preview["current_page_rows"] == 2
            assert preview["has_next"] is True
            assert "password" not in {column["name"] for column in preview["columns"]}
            assert preview["rows"][0]["email"].endswith("@example.com")
            assert preview["rows"][0]["mobile"] == "138****2222"
            assert "never-return-this" not in repr(preview)

            policy = call("GET", f"/sources/{source['id']}/preview/config")
            update_fields = {
                key: policy[key] for key in [
                    "live_preview_enabled", "allowed_objects", "denied_objects", "allowed_columns",
                    "sensitive_columns", "masking_rules", "default_order", "default_page_size",
                    "max_page_size", "max_text_length", "allow_full_cell", "allow_exact_count",
                    "query_timeout_seconds", "max_filter_conditions", "max_result_bytes",
                ]
            }
            update_fields["allow_exact_count"] = True
            call("PUT", f"/sources/{source['id']}/preview/config", json=update_fields)
            count = call("POST", f"/sources/{source['id']}/data-preview/count", json={
                "object_id": "public.orders",
                "filters": [{"column_id": "public.orders.status", "operator": "eq", "value": "completed"}],
            })
            assert count["count"] == 4

            ontology = call("POST", "/ontologies", json={
                "space_id": space["id"], "code": f"sales-{suffix}", "name": f"经营本体 {suffix}",
                "namespace": f"urn:chuanshen:e2e:{suffix}",
            })
            created["ontology"] = ontology["id"]
            term_payloads = [
                ("order", "订单", "class"),
                ("sales_amount", "销售额", "property"),
                ("order_date", "订单日期", "property"),
                ("order_status", "订单状态", "property"),
            ]
            terms = {}
            for code, label, term_type in term_payloads:
                terms[code] = call("POST", f"/ontologies/{ontology['id']}/terms", json={
                    "code": code, "label": label, "term_type": term_type,
                })

            mapping = call("POST", "/semantic-mappings", json={
                "source_id": source["id"], "ontology_id": ontology["id"],
                "name": f"订单语义映射 {suffix}", "description": "实时经营指标查询",
            })
            created["mapping"] = mapping["id"]
            manifest = {
                "manifest_version": "chuanshen.semantic-mapping/v1",
                "source_id": source["id"], "ontology_id": ontology["id"], "schema_version_id": schema_id,
                "entities": [{
                    "id": "order", "ontology_term_id": terms["order"]["id"], "label": "订单",
                    "description": "每行代表一笔订单",
                    "fragments": [{
                        "id": "order-main", "object_id": "public.orders", "role": "primary",
                        "identity_column_ids": ["public.orders.id"],
                        "display_column_id": "public.orders.order_no", "grain": "订单",
                    }],
                }],
                "attributes": [
                    {
                        "id": "order-sales", "ontology_term_id": terms["sales_amount"]["id"],
                        "entity_id": "order", "fragment_id": "order-main", "column_id": "public.orders.sales_amount",
                        "label": "销售额", "semantic_type": "number", "is_measure": True,
                    },
                    {
                        "id": "order-date", "ontology_term_id": terms["order_date"]["id"],
                        "entity_id": "order", "fragment_id": "order-main", "column_id": "public.orders.order_date",
                        "label": "订单日期", "semantic_type": "date",
                    },
                    {
                        "id": "order-status", "ontology_term_id": terms["order_status"]["id"],
                        "entity_id": "order", "fragment_id": "order-main", "column_id": "public.orders.status",
                        "label": "订单状态", "semantic_type": "string",
                    },
                ],
                "relationships": [], "notes": ["API E2E 初始映射"],
            }
            mapping = call("PUT", f"/semantic-mappings/{mapping['id']}", json={"manifest": manifest})
            version_2 = mapping["latest_version"]
            validated = call("POST", f"/semantic-mappings/{mapping['id']}/validate?version_id={version_2['id']}")
            assert validated["validation"]["ok"] is True
            activated = call("POST", f"/semantic-mappings/{mapping['id']}/activate?version_id={version_2['id']}")
            assert activated["active_version_id"] == version_2["id"]

            plan = {
                "version": "chuanshen.semantic-query-plan/v1",
                "original_question": "2026 年已完成订单销售总额是多少？",
                "intent": "计算 2026 年已完成订单销售总额",
                "entity_ids": ["order"], "relationship_ids": [],
                "outputs": [{"position": 1, "label": "销售总额", "kind": "metric", "attribute_ids": ["order-sales"], "aggregate": "sum"}],
                "filters": [
                    {"attribute_id": "order-date", "operator": "between", "value": "2026-01-01", "upper": "2026-12-31"},
                    {"attribute_id": "order-status", "operator": "eq", "value": "completed"},
                ],
                "expected_cardinality": "single_value", "result_grain": "已完成订单",
                "time_range": "2026 年", "null_policy": "忽略空值",
                "metric_contract": {"kind": "sum", "aggregation_grain": "订单", "base_entity_ids": ["order"]},
            }
            query_ir = {
                "version": "chuanshen.query-ir/v1",
                "from_entity": {"binding": "o", "entity_id": "order"},
                "select": [{"alias": "total_sales", "expression": {
                    "kind": "aggregate", "function": "sum",
                    "expression": {"kind": "attribute", "attribute_id": "order-sales", "binding": "o"},
                }}],
                "where": {"kind": "logical", "operator": "and", "operands": [
                    {
                        "kind": "between", "expression": {"kind": "attribute", "attribute_id": "order-date", "binding": "o"},
                        "lower": {"kind": "literal", "value": "2026-01-01"},
                        "upper": {"kind": "literal", "value": "2026-12-31"},
                    },
                    {
                        "kind": "binary", "operator": "=",
                        "left": {"kind": "attribute", "attribute_id": "order-status", "binding": "o"},
                        "right": {"kind": "literal", "value": "completed"},
                    },
                ]},
            }
            assert call("POST", "/structured-query/plan/validate", json={
                "mapping_version_id": version_2["id"], "plan": plan,
            })["ok"] is True
            assert call("POST", "/structured-query/ir/validate", json={
                "mapping_version_id": version_2["id"], "plan": plan, "query_ir": query_ir,
            })["ok"] is True
            compiled = call("POST", "/structured-query/compile", json={
                "mapping_version_id": version_2["id"], "plan": plan, "query_ir": query_ir, "max_rows": 20,
            })
            assert "2026-01-01" not in compiled["sql_template"]
            assert "completed" not in compiled["sql_template"]
            result = call("POST", "/structured-query/execute", json={
                "mapping_version_id": version_2["id"], "plan": plan, "query_ir": query_ir, "max_rows": 20,
            })
            assert result["rows"] == [{"total_sales": "910000.00"}]
            assert result["source_citations"][0]["summary"]["query_run_id"] == result["query_run_id"]
            run = call("GET", f"/structured-query/runs/{result['query_run_id']}")
            assert run["status"] == "succeeded"
            assert run["mapping_version_id"] == version_2["id"]

            if FIXTURE_PG_ADMIN_PASSWORD:
                admin = create_engine(
                    f"postgresql+psycopg://structured_admin:{FIXTURE_PG_ADMIN_PASSWORD}@structured-postgres:5432/structured_fixture",
                    pool_pre_ping=True,
                )
                renamed = False
                try:
                    with admin.begin() as connection:
                        connection.execute(text("ALTER TABLE orders RENAME COLUMN status TO status_drift"))
                    renamed = True
                    drifted = call("POST", f"/sources/{source['id']}/schema/discover")
                    assert "public.orders.status" in drifted["diff_from_previous"]["removed_columns"]
                    stale_mapping = call("GET", f"/semantic-mappings/{mapping['id']}")
                    assert stale_mapping["active_version"]["status"] == "stale"
                    blocked = client.post("/structured-query/execute", json={
                        "mapping_version_id": version_2["id"], "plan": plan,
                        "query_ir": query_ir, "max_rows": 20,
                    })
                    assert blocked.status_code == 409
                finally:
                    if renamed:
                        with admin.begin() as connection:
                            connection.execute(text("ALTER TABLE orders RENAME COLUMN status_drift TO status"))
                    admin.dispose()
                restored = call("POST", f"/sources/{source['id']}/schema/discover")
                manifest = {**manifest, "schema_version_id": restored["id"], "notes": ["Schema 漂移恢复版本"]}
                mapping = call("PUT", f"/semantic-mappings/{mapping['id']}", json={"manifest": manifest})
                version_2 = mapping["latest_version"]
                assert call("POST", f"/semantic-mappings/{mapping['id']}/validate?version_id={version_2['id']}")["validation"]["ok"] is True
                call("POST", f"/semantic-mappings/{mapping['id']}/activate?version_id={version_2['id']}")

            changed_manifest = {**manifest, "notes": ["API E2E 初始映射", "版本差异与回滚验证"]}
            mapping = call("PUT", f"/semantic-mappings/{mapping['id']}", json={"manifest": changed_manifest})
            version_3 = mapping["latest_version"]
            diff = call("GET", f"/semantic-mappings/{mapping['id']}/diff?left_version_id={version_2['id']}&right_version_id={version_3['id']}")
            assert diff["diff"]["notes_changed"] is True
            call("POST", f"/semantic-mappings/{mapping['id']}/activate?version_id={version_3['id']}")
            rolled_back = call("POST", f"/semantic-mappings/{mapping['id']}/rollback", json={"version_id": version_2["id"]})
            assert rolled_back["active_version_id"] == version_2["id"]
            assert rolled_back["active_version"]["status"] == "active"

            job = call("POST", f"/sources/{source['id']}/sync")
            created["job"] = job["id"]
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                jobs = call("GET", f"/sources/{source['id']}/jobs")["items"]
                current = next(item for item in jobs if item["id"] == job["id"])
                if current["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(2)
            assert current["status"] == "succeeded", current
            parse_job_id = current["result"]["parse_job_id"]
            while time.monotonic() < deadline:
                parse_job = call("GET", f"/jobs/{parse_job_id}")
                if parse_job["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(2)
            assert parse_job["status"] == "succeeded", parse_job
            documents = call("GET", f"/documents?space_id={space['id']}")
            created["documents"] = [item["id"] for item in documents]
            assert documents
            snapshot = call("POST", f"/sources/{source['id']}/data-preview", json={
                "object_id": "public.customers", "mode": "snapshot", "page": 1, "page_size": 20,
            })
            assert snapshot["mode"] == "snapshot"
            assert snapshot["current_page_rows"] == 4
            assert "password" not in {column["name"] for column in snapshot["columns"]}
            assert snapshot["rows"][0]["email"].endswith("@example.com")
            assert "never-return-this" not in repr(snapshot)
        finally:
            if not KEEP_DATA:
                for document_id in created.get("documents", []):
                    client.delete(f"/documents/{document_id}")
                if created.get("mapping"):
                    client.delete(f"/semantic-mappings/{created['mapping']}")
                if created.get("source"):
                    client.delete(f"/sources/{created['source']}")
                if created.get("ontology"):
                    client.delete(f"/ontologies/{created['ontology']}")
                if created.get("space"):
                    client.delete(f"/spaces/{created['space']}")
