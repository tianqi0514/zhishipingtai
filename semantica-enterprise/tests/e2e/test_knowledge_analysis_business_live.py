from __future__ import annotations

import os
import time
import uuid

import httpx


BASE_URL = os.getenv("E2E_BASE_URL", "http://api:8080/api/v1").rstrip("/")
ADMIN_USERNAME = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]


POLICY_DEFINITION = {
    "conditions": [
        {"predicate": "适用于", "subject": "R1", "object": "R2"},
        {"predicate": "管理", "subject": "R2", "object": "R3"},
    ],
    "conclusion": {"predicate": "适用于", "subject": "R1", "object": "R3"},
}


RISK_DEFINITION = {
    "conditions": [
        {"predicate": "供应", "subject": "R1", "object": "R2"},
        {"predicate": "用于", "subject": "R2", "object": "R3"},
        {"predicate": "存在风险", "subject": "R1", "object": "R4"},
    ],
    "conclusion": {"predicate": "受到影响", "subject": "R3", "object": "R4"},
}


def test_knowledge_analysis_business_flow_against_live_docker_stack() -> None:
    suffix = uuid.uuid4().hex[:8]
    cleanup_tasks: list[str] = []
    cleanup_rule_sets: list[str] = []
    with httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(30, read=180)) as client:
        def call(method: str, path: str, expected: int | tuple[int, ...] = 200, **kwargs):
            response = client.request(method, path, **kwargs)
            allowed = (expected,) if isinstance(expected, int) else expected
            assert response.status_code in allowed, (
                f"{method} {path}: {response.status_code} {response.text[:1200]}"
            )
            return response.json() if response.content else None

        login = call(
            "POST",
            "/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        client.headers["Authorization"] = f"Bearer {login['access_token']}"

        spaces = call("GET", "/spaces")
        space = next(item for item in spaces if item["code"] == "knowledge-analysis-acceptance")
        space_id = space["id"]

        readiness = call("GET", "/analysis/readiness", params={"space_id": space_id})
        assert readiness["ready"] is True
        assert readiness["published_document_count"] == 2
        assert readiness["entity_count"] == 7
        assert readiness["asserted_fact_count"] == 5
        assert readiness["evidence_coverage"] == 1
        assert readiness["blocking_issues"] == []

        vocabulary = call("GET", "/analysis/vocabulary", params={"space_id": space_id})
        predicate_counts = {item["name"]: item["count"] for item in vocabulary["predicates"]}
        assert predicate_counts == {"供应": 1, "存在风险": 1, "用于": 1, "管理": 1, "适用于": 1}
        templates = call("GET", "/analysis/templates", params={"space_id": space_id})["items"]
        assert next(item for item in templates if item["id"] == "policy_scope")["ready"] is True
        assert next(item for item in templates if item["id"] == "supplier_risk")["ready"] is True

        match = call(
            "POST",
            "/analysis/rules/match-preview",
            json={"space_id": space_id, "definition": POLICY_DEFINITION, "max_results": 100},
        )
        assert match["engine"] == "semantica.reasoning.DatalogReasoner"
        assert match["predicted_count"] == 1
        assert match["samples"][0]["subject"] == "采购管理制度"
        assert match["samples"][0]["object"] == "数字科技公司"
        assert match["samples"][0]["premise_count"] == 2
        assert match["samples"][0]["evidence_linked_count"] == 2

        def wait_run(run_id: str) -> dict:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                detail = call("GET", f"/analysis/inference-runs/{run_id}")
                if detail["status"] in {"succeeded", "failed", "cancelled"}:
                    assert detail["status"] == "succeeded", detail
                    return detail
                time.sleep(0.5)
            raise AssertionError(f"analysis run {run_id} did not finish")

        policy_setup = call(
            "POST",
            "/analysis/guided-setups",
            json={
                "name": f"制度适用范围 API 验收 {suffix}",
                "question": "采购管理制度是否适用于数字科技公司？",
                "category": "制度治理",
                "space_id": space_id,
                "template_id": "policy_scope",
                "rule_name": "制度适用范围判断",
                "definition": POLICY_DEFINITION,
                "role_labels": {"R1": "制度", "R2": "上级组织", "R3": "下属单位"},
                "mode": "preview",
                "max_results": 100,
            },
        )
        cleanup_tasks.append(policy_setup["task"]["id"])
        cleanup_rule_sets.append(policy_setup["rule_set"]["id"])
        policy_preview = wait_run(policy_setup["run"]["id"])
        assert len(policy_preview["items"]) == 1
        conclusion = policy_preview["items"][0]
        assert (conclusion["subject_name"], conclusion["predicate"], conclusion["object_name"]) == (
            "采购管理制度",
            "适用于",
            "数字科技公司",
        )
        assert len(conclusion["evidence"]) == 2
        assert all(item["source_chunk_id"] for item in conclusion["evidence"])
        assert {item["source_title"] for item in conclusion["evidence"]} == {
            "国联集团采购制度适用说明.md"
        }
        for evidence in conclusion["evidence"]:
            fragment = call("GET", f"/fragments/{evidence['source_chunk_id']}")
            assert fragment["document_title"] == "国联集团采购制度适用说明.md"
            assert fragment["page_number"] == 1
            assert "采购管理制度适用于国联集团" in fragment["text"]

        diagnostics = call("GET", f"/analysis/runs/{policy_preview['id']}/diagnostics")
        assert diagnostics["issues"] == []
        impact = call("GET", f"/analysis/runs/{policy_preview['id']}/impact")
        assert impact["can_publish"] is True
        assert impact["conclusion_count"] == 1
        assert impact["missing_evidence_count"] == 0

        publish = call("POST", f"/analysis/runs/{policy_preview['id']}/publish")
        published = wait_run(publish["id"])
        assert published["mode"] == "publish"
        assert published["items"][0]["status"] == "published"
        facts = call("GET", "/knowledge/facts", params={"space_id": space_id, "include_inferred": True})
        assert any(
            item["origin_type"] == "inferred"
            and item["subject_name"] == "采购管理制度"
            and item["predicate"] == "适用于"
            and item["object_name"] == "数字科技公司"
            for item in facts["items"]
        )
        visual = call(
            "POST",
            "/analysis/visual-query",
            json={
                "space_ids": [space_id],
                "subject_query": "采购管理制度",
                "predicate": "适用于",
                "include_inferred": True,
                "limit": 20,
            },
        )
        assert visual["total"] >= 2
        sparql = call(
            "POST",
            "/analysis/sparql",
            json={
                "space_ids": [space_id],
                "query": (
                    'SELECT ?s ?p ?o WHERE { ?s ?p ?o . '
                    '?p <http://www.w3.org/2000/01/rdf-schema#label> "适用于" } LIMIT 20'
                ),
            },
        )
        assert sparql["total"] >= 2

        rollback_preview = call("GET", f"/analysis/inference-runs/{published['id']}/rollback-preview")
        assert rollback_preview["can_rollback"] is True
        rollback = call("POST", f"/analysis/runs/{published['id']}/rollback")
        assert rollback["invalidated"] == 1
        after_rollback = call(
            "GET", "/knowledge/facts", params={"space_id": space_id, "include_inferred": True}
        )
        assert not any(item.get("run_id") == published["id"] for item in after_rollback["items"])

        risk_setup = call(
            "POST",
            "/analysis/guided-setups",
            json={
                "name": f"供应商风险传导 API 验收 {suffix}",
                "question": "智慧流程中枢项目可能受到哪个供应商风险影响？",
                "category": "风险管理",
                "space_id": space_id,
                "template_id": "supplier_risk",
                "rule_name": "供应商风险传导判断",
                "definition": RISK_DEFINITION,
                "role_labels": {"R1": "供应商", "R2": "产品", "R3": "项目", "R4": "风险"},
                "mode": "preview",
                "max_results": 100,
            },
        )
        cleanup_tasks.append(risk_setup["task"]["id"])
        cleanup_rule_sets.append(risk_setup["rule_set"]["id"])
        risk = wait_run(risk_setup["run"]["id"])
        assert len(risk["items"]) == 1
        risk_item = risk["items"][0]
        assert (risk_item["subject_name"], risk_item["predicate"], risk_item["object_name"]) == (
            "智慧流程中枢项目",
            "受到影响",
            "交付延期",
        )
        assert len(risk_item["evidence"]) == 3
        assert all(item["source_chunk_id"] for item in risk_item["evidence"])

        missing_set = call(
            "POST",
            "/analysis/rule-sets",
            json={
                "name": f"缺少关系诊断 {suffix}",
                "description": "验证真实零结果诊断",
                "category": "验收",
                "space_ids": [space_id],
            },
        )
        cleanup_rule_sets.append(missing_set["id"])
        call(
            "POST",
            f"/analysis/rule-sets/{missing_set['id']}/rules",
            json={
                "name": "缺少关系规则",
                "definition": {
                    "conditions": [{"predicate": "尚不存在的依赖关系", "subject": "R1", "object": "R2"}],
                    "conclusion": {"predicate": "受到影响", "subject": "R1", "object": "R2"},
                },
            },
        )
        missing_run = call(
            "POST",
            "/analysis/inference-runs",
            json={"rule_set_id": missing_set["id"], "space_ids": [space_id], "mode": "preview"},
        )
        missing = wait_run(missing_run["id"])
        assert missing["items"] == []
        missing_diagnostics = call("GET", f"/analysis/runs/{missing['id']}/diagnostics")
        assert missing_diagnostics["issues"][0]["code"] == "missing_predicate"
        assert "尚不存在的依赖关系" in missing_diagnostics["issues"][0]["message"]

        for task_id in cleanup_tasks:
            call("DELETE", f"/analysis/scenarios/{task_id}")
        for rule_set_id in cleanup_rule_sets:
            call("DELETE", f"/analysis/rule-sets/{rule_set_id}")
