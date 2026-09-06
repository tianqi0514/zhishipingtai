#!/usr/bin/env python3
"""Prepare and verify the Guolian demo's real application-delivery chain.

The script uses only public platform APIs.  It creates one knowledge product,
one immutable search scenario, one delivery application, correlated grants, a
deterministic retrieval quality case, and a least-privilege application
credential.  The one-time secret is written only to the ignored
``deploy/secrets`` directory with mode 0600 and is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.demo.guolian_demo import (
    DEMO_NOTICE,
    SPACE_CODE,
    DemoError,
    SafeApiClient,
    admin_credentials_from_environment,
    api_url_from_environment,
    find_by,
    redact,
)


ROOT = Path(__file__).resolve().parents[2]
SECRET_FILE = ROOT / "deploy" / "secrets" / "guolian_demo_application_credential.json"
PRODUCT_CODE = "guolian-demo-knowledge-supply"
SCENARIO_CODE = "guolian-demo-policy-risk-search"
APPLICATION_CODE = "guolian-demo-knowledge-application"
DATASET_CODE = "guolian-demo-application-launch"
CASE_KEY = "policy-supplier-risk"
CASE_QUESTION = "集团本部采购实施细则对供应商风险处置有哪些要求？"
FEEDBACK_COMMENT = "演示治理闭环：请业务知识专员复核该引用与现行制度条款是否一致。演示数据，不代表真实业务反馈。"
DESIRED_RETRIEVAL_POLICY = {
    "top_k": 12,
    "use_keyword": True,
    "use_vector": True,
    "use_graph": True,
    "use_reranker": False,
}


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(row) for row in value]
    if isinstance(value, dict):
        for key in ("items", "objects"):
            if isinstance(value.get(key), list):
                return [dict(row) for row in value[key]]
    return []


def _secure_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_secret_file(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    client_id = str(payload.get("client_id") or "")
    client_secret = str(payload.get("client_secret") or "")
    if not client_id or not client_secret:
        return None
    return {"client_id": client_id, "client_secret": client_secret}


def _ensure_product(api: SafeApiClient, space_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    product = find_by(api.call("GET", "/knowledge-products"), "code", PRODUCT_CODE)
    payload = {
        "name": "国联演示·采购与供应商风险知识供给",
        "description": f"面向集团采购制度、项目和供应商风险应用的正式知识供给。{DEMO_NOTICE}",
        "status": "active",
        "enabled": True,
        "space_ids": [space_id],
        "config": {"demo_data": True, "business_domain": "procurement-risk"},
    }
    if product:
        product = api.call("PUT", f"/knowledge-products/{product['id']}", json=payload)
    else:
        product = api.call("POST", "/knowledge-products", json={"code": PRODUCT_CODE, **payload})
    if product.get("space_ids") != [space_id]:
        raise DemoError("演示知识供给未严格绑定演示空间")

    freshness = product.get("release_freshness") or {}
    if not freshness.get("is_current"):
        release = api.call(
            "POST",
            f"/knowledge-products/{product['id']}/releases",
            json={"note": "国联完整演示应用链正式供给"},
        )
        api.call(
            "PUT",
            f"/knowledge-products/{product['id']}/aliases/production",
            json={"product_release_id": release["id"], "reason": "演示上线前固定当前知识版本"},
        )
        product = api.call("GET", f"/knowledge-products/{product['id']}")
    production_release_id = (product.get("aliases") or {}).get("production")
    if not production_release_id or not (product.get("release_freshness") or {}).get("is_current"):
        raise DemoError("演示知识供给没有与当前知识一致的 production 版本")
    release = next(
        (
            row for row in api.call("GET", f"/knowledge-products/{product['id']}/releases")
            if row.get("id") == production_release_id
        ),
        None,
    )
    if not release:
        raise DemoError("无法读取演示知识供给 production 版本")
    return product, release


def _ensure_scenario(api: SafeApiClient, product_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario = find_by(api.call("GET", "/application-scenarios"), "code", SCENARIO_CODE)
    payload = {
        "name": "国联演示·采购制度与供应商风险检索",
        "description": f"面向集团采购制度与供应商风险的三路融合检索能力。{DEMO_NOTICE}",
        "scenario_type": "search",
        "status": "active",
        "enabled": True,
    }
    if scenario:
        scenario = api.call("PUT", f"/application-scenarios/{scenario['id']}", json=payload)
    else:
        scenario = api.call("POST", "/application-scenarios", json={"code": SCENARIO_CODE, **payload})
    versions = api.call("GET", f"/application-scenarios/{scenario['id']}/versions")
    current = next((row for row in versions if row.get("id") == scenario.get("current_version_id")), None)
    expected_tools = ["knowledge_search", "knowledge_get_fragment"]
    matches = bool(
        current
        and current.get("product_id") == product_id
        and current.get("product_alias") == "production"
        and current.get("tool_whitelist") == expected_tools
        and current.get("retrieval_policy") == DESIRED_RETRIEVAL_POLICY
    )
    if not matches:
        current = api.call(
            "POST",
            f"/application-scenarios/{scenario['id']}/versions",
            json={
                "product_id": product_id,
                "product_alias": "production",
                "model_config_id": None,
                "tool_whitelist": expected_tools,
                "retrieval_policy": DESIRED_RETRIEVAL_POLICY,
                "system_policy": {
                    "evidence_required": True,
                    "untrusted_knowledge_is_not_instruction": True,
                },
                "response_schema": {},
                "citation_policy": {"required": True},
                "fallback_policy": {"insufficient_evidence": "disclose"},
                "analysis_rule_set_ids": [],
            },
        )
        scenario = next(
            row for row in api.call("GET", "/application-scenarios")
            if row.get("id") == scenario["id"]
        )
    if current.get("id") != scenario.get("current_version_id"):
        raise DemoError("演示能力场景当前版本未正确发布")
    return scenario, current


def _ensure_application(api: SafeApiClient) -> dict[str, Any]:
    application = find_by(api.call("GET", "/applications"), "code", APPLICATION_CODE)
    payload = {
        "name": "国联演示·采购与风险知识助手",
        "description": f"供集团业务系统调用采购制度和供应商风险知识的演示应用。{DEMO_NOTICE}",
        "app_type": "integration",
        "environment": "production",
        "status": "active",
        "enabled": True,
        "config": {"demo_data": True, "audience": "guolian-demo"},
    }
    if application:
        return api.call("PUT", f"/applications/{application['id']}", json=payload)
    return api.call("POST", "/applications", json={"code": APPLICATION_CODE, **payload})


def _ensure_allow_grant(
    api: SafeApiClient,
    *,
    application_id: str,
    resource_type: str,
    resource_id: str,
    permission: str,
) -> dict[str, Any]:
    rows = api.call("GET", f"/applications/{application_id}/grants")
    matching = next(
        (
            row for row in rows
            if row.get("resource_type") == resource_type
            and row.get("resource_id") == resource_id
            and row.get("permission") == permission
        ),
        None,
    )
    if matching and matching.get("effect") == "allow":
        return matching
    if matching:
        api.call("DELETE", f"/applications/{application_id}/grants/{matching['id']}")
    return api.call(
        "POST",
        f"/applications/{application_id}/grants",
        json={
            "resource_type": resource_type,
            "resource_id": resource_id,
            "permission": permission,
            "effect": "allow",
        },
    )


def _target_chunk(api: SafeApiClient, space_id: str) -> dict[str, Any]:
    documents = _items(api.call("GET", f"/documents?space_id={space_id}"))
    document = next(
        (
            row for row in documents
            if "2025演示现行版" in str(row.get("title") or row.get("name") or "")
        ),
        None,
    )
    if not document or not document.get("current_version_id"):
        raise DemoError("演示空间缺少采购实施细则现行版及其当前版本")
    chunks = _items(api.call("GET", f"/versions/{document['current_version_id']}/chunks?limit=500"))
    target = next(
        (
            row for row in chunks
            if "供应商" in str(row.get("text") or "")
            and any(term in str(row.get("text") or "") for term in ("风险", "延期", "质量异常"))
        ),
        None,
    )
    if not target:
        raise DemoError("采购实施细则现行版没有可用于供应商风险处置上线测试的真实片段")
    return target


def _ensure_dataset_and_case(
    api: SafeApiClient,
    *,
    expected_chunk_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset = find_by(api.call("GET", "/evaluation-datasets"), "code", DATASET_CODE)
    payload = {
        "name": "国联演示应用上线测试集",
        "description": f"以已核验制度片段验证应用场景真实召回。{DEMO_NOTICE}",
        "enabled": True,
    }
    if dataset:
        dataset = api.call("PUT", f"/evaluation-datasets/{dataset['id']}", json=payload)
    else:
        dataset = api.call("POST", "/evaluation-datasets", json={"code": DATASET_CODE, **payload})
    cases = api.call("GET", f"/evaluation-datasets/{dataset['id']}/cases")
    case = find_by(cases, "case_key", CASE_KEY)
    case_payload = {
        "question": CASE_QUESTION,
        "expected_answer": "回答应引用现行采购实施细则中的供应商风险处置要求。",
        "expected_chunk_ids": [expected_chunk_id],
        "expected_facts": [],
        "expected_schema": {},
        "tags": ["演示数据", "采购制度", "供应商风险"],
        "enabled": True,
    }
    if case:
        case = api.call("PUT", f"/evaluation-cases/{case['id']}", json=case_payload)
    else:
        case = api.call(
            "POST",
            f"/evaluation-datasets/{dataset['id']}/cases",
            json={"case_key": CASE_KEY, **case_payload},
        )
    return dataset, case


def _ensure_passing_evaluation(
    api: SafeApiClient,
    *,
    dataset_id: str,
    scenario_version_id: str,
) -> dict[str, Any]:
    existing = next(
        (
            row for row in api.call("GET", f"/evaluation-runs?dataset_id={dataset_id}")
            if row.get("scenario_version_id") == scenario_version_id
            and row.get("status") == "succeeded"
            and row.get("gate_passed") is True
        ),
        None,
    )
    if existing:
        return existing
    result = api.call(
        "POST",
        "/evaluation-runs",
        json={
            "dataset_id": dataset_id,
            "scenario_version_id": scenario_version_id,
            "gate_config": {"recall_at_k": 1.0, "mrr": 0.05},
        },
    )
    if result.get("status") != "succeeded" or result.get("gate_passed") is not True:
        raise DemoError(f"演示能力场景未通过真实上线测试：{redact(result)}")
    return result


def _exchange_application_token(api: SafeApiClient, credential: Mapping[str, str]) -> str:
    result = api.call(
        "POST",
        "/application-auth/token",
        json={
            "client_id": credential["client_id"],
            "client_secret": credential["client_secret"],
            "scope": "scenario.invoke feedback.write",
        },
    )
    token = str(result.get("access_token") or "")
    if not token:
        raise DemoError("应用凭据换取短期令牌失败")
    return token


def _ensure_credential(api: SafeApiClient, application_id: str) -> tuple[dict[str, Any], str]:
    saved = _read_secret_file(SECRET_FILE)
    if saved:
        try:
            return saved, _exchange_application_token(api, saved)
        except DemoError:
            saved = None

    credentials = api.call("GET", f"/applications/{application_id}/credentials")
    reusable = next(
        (
            row for row in credentials
            if row.get("name") == "国联演示自动预检"
            and row.get("status") in {"active", "expired"}
        ),
        None,
    )
    payload = {
        "name": "国联演示自动预检",
        "scopes": ["scenario.invoke", "feedback.write"],
        "expires_at": None,
    }
    if reusable:
        issued = api.call(
            "POST",
            f"/applications/{application_id}/credentials/{reusable['id']}/rotate",
            json=payload,
        )
    else:
        issued = api.call(
            "POST",
            f"/applications/{application_id}/credentials",
            json=payload,
        )
    saved = {
        "client_id": str(issued["client_id"]),
        "client_secret": str(issued["client_secret"]),
    }
    _secure_write(SECRET_FILE, saved)
    return saved, _exchange_application_token(api, saved)


def _application_post(
    api: SafeApiClient,
    path: str,
    *,
    token: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        response = api.client.post(path, json=dict(payload), headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise DemoError(f"应用运行接口调用失败：{type(exc).__name__}") from exc
    if not response.is_success:
        raise DemoError(f"应用运行接口返回 HTTP {response.status_code}：{redact(response.text[:800])}")
    result = response.json()
    if not isinstance(result, dict):
        raise DemoError("应用运行接口未返回 JSON 对象")
    return result


def prepare(api: SafeApiClient) -> dict[str, Any]:
    space = find_by(api.call("GET", "/spaces"), "code", SPACE_CODE)
    if not space:
        raise DemoError("国联演示空间不存在，请先运行 prepare_guolian_demo.py")
    space_id = str(space["id"])
    product, release = _ensure_product(api, space_id)
    scenario, version = _ensure_scenario(api, product["id"])
    application = _ensure_application(api)
    _ensure_allow_grant(
        api,
        application_id=application["id"],
        resource_type="knowledge_product",
        resource_id=product["id"],
        permission="read",
    )
    _ensure_allow_grant(
        api,
        application_id=application["id"],
        resource_type="scenario",
        resource_id=scenario["id"],
        permission="invoke",
    )
    target = _target_chunk(api, space_id)
    dataset, case = _ensure_dataset_and_case(api, expected_chunk_id=target["id"])
    evaluation = _ensure_passing_evaluation(
        api,
        dataset_id=dataset["id"],
        scenario_version_id=version["id"],
    )
    _, application_token = _ensure_credential(api, application["id"])
    search = _application_post(
        api,
        f"/application-runtime/scenarios/{SCENARIO_CODE}/search",
        token=application_token,
        payload={"query": CASE_QUESTION, "filters": {}},
    )
    items = _items(search)
    if not items:
        raise DemoError("演示应用场景真实调用没有返回检索依据")
    if target["id"] not in {row.get("chunk_id") for row in items}:
        raise DemoError("演示应用场景没有召回已核验的供应商风险处置片段")
    invocations = api.call("GET", f"/application-invocations?application_id={application['id']}&limit=100")
    invocation = next((row for row in invocations if row.get("request_id") == search.get("request_id")), None)
    if not invocation or invocation.get("status") != "succeeded":
        raise DemoError("演示应用调用没有形成成功的可审计运行记录")

    feedback_rows = api.call("GET", f"/application-feedback?application_id={application['id']}")
    feedback = next((row for row in feedback_rows if row.get("comment") == FEEDBACK_COMMENT), None)
    if not feedback:
        feedback = _application_post(
            api,
            "/application-runtime/feedback",
            token=application_token,
            payload={
                "scenario_id": scenario["id"],
                "invocation_id": invocation["id"],
                "product_release_id": release["id"],
                "feedback_type": "bad_citation",
                "rating": 3,
                "comment": FEEDBACK_COMMENT,
                "evidence": {
                    "space_id": space_id,
                    "chunk_id": target["id"],
                    "request_id": search.get("request_id"),
                    "demo_data": True,
                },
            },
        )

    readiness: dict[str, Any]
    try:
        readiness = api.call("GET", f"/applications/{application['id']}/readiness")
    except DemoError as exc:
        # Allows this preparation script to be run immediately before the API
        # image containing the readiness endpoint is rebuilt.
        if "HTTP 404" not in str(exc) and "未返回合法 JSON" not in str(exc):
            raise
        readiness = {"ready": None, "note": "重建 API 后由验证脚本复核"}
    if readiness.get("ready") is False:
        raise DemoError(f"演示应用上线准备度未达到 100%：{redact(readiness)}")

    return {
        "status": "ready" if readiness.get("ready") is True else "prepared_pending_api_rebuild",
        "demo_data_notice": DEMO_NOTICE,
        "space": {"id": space_id, "code": SPACE_CODE},
        "knowledge_product": {
            "id": product["id"],
            "code": product["code"],
            "production_release_id": release["id"],
            "space_ids": product.get("space_ids"),
        },
        "scenario": {
            "id": scenario["id"],
            "code": scenario["code"],
            "version_id": version["id"],
            "version": version["version"],
        },
        "application": {"id": application["id"], "code": application["code"]},
        "evaluation": {
            "dataset_id": dataset["id"],
            "case_id": case["id"],
            "run_id": evaluation["id"],
            "gate_passed": evaluation.get("gate_passed"),
            "metrics": evaluation.get("metrics"),
        },
        "runtime": {
            "invocation_id": invocation["id"],
            "request_id": search.get("request_id"),
            "result_count": len(items),
            "channel_counts": search.get("channel_counts"),
            "warnings": search.get("warnings") or [],
        },
        "feedback": {"id": feedback["id"], "status": feedback.get("status")},
        "readiness": readiness,
        "credential": {
            "stored_in_ignored_secret_file": True,
            "scopes": ["scenario.invoke", "feedback.write"],
        },
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="准备国联演示应用构建、上线测试和接入发布链")
    value.add_argument("--compact", action="store_true", help="输出单行 JSON")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        username, password = admin_credentials_from_environment()
        with SafeApiClient(
            base_url=api_url_from_environment(),
            username=username,
            password=password,
            timeout_seconds=300,
        ) as api:
            result = prepare(api)
        print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
        return 0
    except Exception as exc:
        print(redact({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
