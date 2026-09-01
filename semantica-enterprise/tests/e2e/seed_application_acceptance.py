#!/usr/bin/env python3
"""Build and invoke two real application scenarios over the seeded knowledge."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx


API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("APPLICATION_BUILDER_USERNAME", "gl_app_builder")
PASSWORD = os.environ["GUOLIAN_ACCEPTANCE_USER_PASSWORD"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


class Platform:
    def __init__(self, username: str = USERNAME, password: str = PASSWORD) -> None:
        self.client = httpx.Client(base_url=API, timeout=httpx.Timeout(30, read=600))
        login = self.call("POST", "/auth/login", json={"username": username, "password": password})
        if username == USERNAME and "application.manage" not in login["user"].get("permissions", []):
            raise RuntimeError("应用开发者角色没有生效")
        self.client.headers["Authorization"] = f"Bearer {login['access_token']}"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:1600]}")
        return response.json() if response.content else {}

    def wait_job(self, job_id: str, timeout: int = 600) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.call("GET", f"/jobs/{job_id}")
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                if job["status"] != "succeeded":
                    raise RuntimeError(f"任务失败：{job['status']} {job.get('error_message')}")
                return job
            time.sleep(1)
        raise TimeoutError(f"任务等待超时：{job_id}")


def ensure_product(platform: Platform, spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    product = next((row for row in platform.call("GET", "/knowledge-products") if row["code"] == spec["code"]), None)
    if product is None:
        product = platform.call("POST", "/knowledge-products", json=spec)
    releases = platform.call("GET", f"/knowledge-products/{product['id']}/releases")
    release = releases[0] if releases else platform.call(
        "POST", f"/knowledge-products/{product['id']}/releases", json={"note": "国联集团全业务验收基线"}
    )
    current = platform.call("GET", f"/knowledge-products/{product['id']}")
    if current.get("aliases", {}).get("production") != release["id"]:
        platform.call(
            "PUT",
            f"/knowledge-products/{product['id']}/aliases/production",
            json={"product_release_id": release["id"], "reason": "验收通过后进入正式供给"},
        )
    return product, release


def ensure_scenario(platform: Platform, spec: dict[str, Any], product: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario = next((row for row in platform.call("GET", "/application-scenarios") if row["code"] == spec["code"]), None)
    if scenario is None:
        scenario = platform.call("POST", "/application-scenarios", json=spec)
    versions = platform.call("GET", f"/application-scenarios/{scenario['id']}/versions")
    if versions:
        return scenario, versions[0]
    version = platform.call("POST", f"/application-scenarios/{scenario['id']}/versions", json={
        "product_id": product["id"],
        "product_alias": "production",
        "tool_whitelist": ["knowledge_search", "knowledge_get_fragment"],
        "retrieval_policy": {
            "top_k": 8,
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": False,
        },
        "citation_policy": {"required": True},
        "fallback_policy": {"insufficient_evidence": "disclose"},
    })
    return scenario, version


def ensure_application(platform: Platform, spec: dict[str, Any], product: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    application = next((row for row in platform.call("GET", "/applications") if row["code"] == spec["code"]), None)
    if application is None:
        application = platform.call("POST", "/applications", json=spec)
    grants = platform.call("GET", f"/applications/{application['id']}/grants")
    required = [
        ("knowledge_product", product["id"], "read"),
        ("scenario", scenario["id"], "invoke"),
    ]
    for resource_type, resource_id, permission in required:
        if not any(
            row["resource_type"] == resource_type and row["resource_id"] == resource_id
            and row["permission"] == permission and row["effect"] == "allow"
            for row in grants
        ):
            platform.call("POST", f"/applications/{application['id']}/grants", json={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "permission": permission,
                "effect": "allow",
            })
    return application


def invoke(
    platform: Platform,
    application: dict[str, Any],
    scenario: dict[str, Any],
    query: str,
    *,
    feedback_type: str = "positive",
    feedback_comment: str = "全业务验收：答案依据可核验。",
) -> dict[str, Any]:
    credentials = platform.call("GET", f"/applications/{application['id']}/credentials")
    previous = next((row for row in credentials if row["name"] == "全业务验收接入凭据" and not row.get("revoked_at")), None)
    credential_path = (
        f"/applications/{application['id']}/credentials/{previous['id']}/rotate"
        if previous else f"/applications/{application['id']}/credentials"
    )
    credential = platform.call("POST", credential_path, json={
        "name": "全业务验收接入凭据",
        "scopes": ["scenario.invoke", "feedback.write"],
    })
    token = platform.call("POST", "/application-auth/token", json={
        "client_id": credential["client_id"],
        "client_secret": credential["client_secret"],
        "scope": "scenario.invoke feedback.write",
    })["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    result = platform.call(
        "POST",
        f"/application-runtime/scenarios/{scenario['code']}/search",
        headers=headers,
        json={"query": query, "filters": {}},
    )
    if not result.get("items"):
        raise RuntimeError(f"应用场景没有召回真实知识：{scenario['code']}")
    top = result["items"][0]
    feedback = platform.call("POST", "/application-runtime/feedback", headers=headers, json={
        "scenario_id": scenario["id"],
        "query_run_id": result["query_id"],
        "product_release_id": result["knowledge_product_release"]["id"],
        "feedback_type": feedback_type,
        "rating": 3 if feedback_type != "positive" else 5,
        "comment": feedback_comment,
        "evidence": {"chunk_id": top["chunk_id"], "space_id": top["space_id"]},
    })
    return {
        "application_id": application["id"],
        "scenario_id": scenario["id"],
        "query_id": result["query_id"],
        "request_id": result["request_id"],
        "result_count": len(result["items"]),
        "top_chunk_id": top["chunk_id"],
        "top_space_id": top["space_id"],
        "feedback_id": feedback["id"],
    }


def ensure_evaluation(platform: Platform, scenario_version: dict[str, Any], invocation: dict[str, Any], question: str) -> dict[str, Any]:
    datasets = platform.call("GET", "/evaluation-datasets")
    dataset = next((row for row in datasets if row["code"] == "guolian_business_acceptance"), None)
    if dataset is None:
        dataset = platform.call("POST", "/evaluation-datasets", json={
            "code": "guolian_business_acceptance",
            "name": "国联集团应用上线验收集",
            "description": "验证应用知识范围、召回依据和上线门禁",
        })
    cases = platform.call("GET", f"/evaluation-datasets/{dataset['id']}/cases")
    if not any(row["case_key"] == "policy-positioning" for row in cases):
        platform.call("POST", f"/evaluation-datasets/{dataset['id']}/cases", json={
            "case_key": "policy-positioning",
            "question": question,
            "expected_answer": "NexusOne 面向集团型企业提供知识管理与智能问答一体化能力。",
            "expected_chunk_ids": [invocation["top_chunk_id"]],
            "tags": ["国联集团", "产品定位", "上线门禁"],
        })
    return platform.call("POST", "/evaluation-runs", json={
        "dataset_id": dataset["id"],
        "scenario_version_id": scenario_version["id"],
        "gate_config": {"recall_at_k": 1.0, "mrr": 1.0},
    })


def main() -> int:
    platform = Platform()
    spaces = {row["code"]: row for row in platform.call("GET", "/spaces")}
    specs = [
        {
            "product": {
                "code": "guolian_employee_knowledge",
                "name": "集团员工知识供给",
                "description": "集团制度与 NexusOne 产品权威知识",
                "status": "active",
                "space_ids": [spaces["gl-policy-acceptance"]["id"], spaces["gl-product-acceptance"]["id"]],
            },
            "scenario": {
                "code": "guolian_employee_qa",
                "name": "集团员工知识问答",
                "description": "查询集团制度、知识管理要求和 NexusOne 产品资料",
                "scenario_type": "chat",
                "status": "active",
            },
            "application": {
                "code": "guolian_employee_assistant",
                "name": "集团员工知识助手",
                "description": "面向集团员工的制度与产品问答应用",
                "app_type": "agent",
                "environment": "production",
                "status": "active",
            },
            "question": "NexusOne 的产品定位和核心能力是什么？",
            "feedback_type": "incomplete",
            "feedback_comment": "全业务验收：回答缺少产品适用边界，需要进入知识治理补充。",
        },
        {
            "product": {
                "code": "guolian_supplier_risk",
                "name": "供应商风险知识供给",
                "description": "采购合同、供应商资质、风险报告与处置通知",
                "status": "active",
                "space_ids": [spaces["gl-procurement-acceptance"]["id"]],
            },
            "scenario": {
                "code": "guolian_supplier_risk_qa",
                "name": "供应商风险问答",
                "description": "依据合同和风险材料回答供应商风险问题",
                "scenario_type": "chat",
                "status": "active",
            },
            "application": {
                "code": "guolian_supplier_risk_assistant",
                "name": "供应商风险助手",
                "description": "面向采购和风险岗位的供应商知识应用",
                "app_type": "agent",
                "environment": "production",
                "status": "active",
            },
            "question": "华星核心器件有哪些高风险事件，应采取什么处置？",
        },
    ]
    results = []
    versions = []
    for spec in specs:
        product, _ = ensure_product(platform, spec["product"])
        scenario, version = ensure_scenario(platform, spec["scenario"], product)
        application = ensure_application(platform, spec["application"], product, scenario)
        results.append(invoke(
            platform,
            application,
            scenario,
            spec["question"],
            feedback_type=spec.get("feedback_type", "positive"),
            feedback_comment=spec.get("feedback_comment", "全业务验收：答案依据可核验。"),
        ))
        versions.append(version)
    curation_case = platform.call(
        "POST",
        f"/application-feedback/{results[0]['feedback_id']}/convert-to-curation",
    )
    admin = Platform("admin", ADMIN_PASSWORD)
    fragment = admin.call("GET", f"/fragments/{results[0]['top_chunk_id']}")
    correction = admin.call("POST", "/curation/decisions", json={
        "space_id": results[0]["top_space_id"],
        "target_type": "chunk",
        "target_id": results[0]["top_chunk_id"],
        "version_id": fragment["version_id"],
        "field_path": "text",
        "operation": "override",
        "value": fragment["text"] + "\n适用边界：面向集团总部、所属企业及获得知识空间授权的业务部门。",
        "scope": "version_only",
        "reason_code": "application_feedback",
        "reason_note": "根据应用反馈补充产品适用边界，并保留 Semantica 自动结果。",
        "auto_publish": True,
    })
    if correction.get("job"):
        admin.wait_job(correction["job"]["id"])
    admin.call("PUT", f"/curation/cases/{curation_case['id']}", json={
        "status": "handled",
        "resolution": "corrected",
        "reason_note": "已通过人工治理覆盖补充适用边界并重新发布知识索引。",
    })
    evaluation = ensure_evaluation(platform, versions[0], results[0], specs[0]["question"])
    resolved_feedback = platform.call(
        "POST",
        f"/application-feedback/{results[0]['feedback_id']}/verify-resolution",
        json={"evaluation_run_id": evaluation["id"]},
    )
    print(json.dumps({
        "applications": results,
        "evaluation": {
            "id": evaluation["id"],
            "status": evaluation["status"],
            "gate_passed": evaluation["gate_passed"],
            "metrics": evaluation["metrics"],
        },
        "feedback_curation_case": {
            "feedback_id": results[0]["feedback_id"],
            "curation_case_id": curation_case["id"],
            "status": curation_case["status"],
            "curation_decision_id": correction["decision"]["id"],
            "publish_job_id": correction.get("job", {}).get("id") if correction.get("job") else None,
            "feedback_final_status": resolved_feedback["status"],
            "verified_by_evaluation_run_id": evaluation["id"],
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
