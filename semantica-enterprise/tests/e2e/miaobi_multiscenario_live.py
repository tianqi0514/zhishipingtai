"""Live API acceptance for the six scenario templates awaiting business confirmation.

This does not claim domain acceptance.  It proves that every independent package can
carry validated inputs through verified facts, a deterministic computation, trusted
Plate bindings, document validation, and an auditable JSON export.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from packages.platform.writing import validate_scenario_input
from scripts.miaobi.demo_client import DemoClient, PROJECT_CODE


_configured_root = os.getenv("MIAOBI_REPO_ROOT")
ROOT = Path(_configured_root) if _configured_root else Path(__file__).resolve().parents[2]
SCENARIO_ROOT = ROOT / "demo" / "miaobi" / "scenarios"


def block_hash(block: dict) -> str:
    canonical = json.dumps(block, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def value_payload(value: object) -> dict:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"number": value}
    if isinstance(value, bool):
        return {"boolean": value}
    return {"text": str(value)}


def main() -> None:
    api = DemoClient()
    earthquake_project = api.one("/writing/projects", "code", PROJECT_CODE)
    if earthquake_project is None:
        raise RuntimeError("请先运行 prepare_earthquake_demo.py 建立知识产品版本")
    packages = {item["code"]: item for item in api.get("/writing/scenario-packages")}
    suffix = uuid.uuid4().hex[:8]
    results: list[dict] = []

    for path in sorted(SCENARIO_ROOT.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["disaster_type"] == "earthquake":
            continue
        fixture = payload.get("fixture") or {}
        inputs = fixture.get("inputs") or {}
        resource = fixture.get("resource") or {}
        findings = validate_scenario_input(payload["input_schema"], inputs)
        if findings:
            raise AssertionError(f"{payload['code']} Fixture 不符合输入契约：{findings}")
        package = packages.get(payload["code"])
        if not package or not package.get("current_version_id"):
            raise AssertionError(f"场景包未激活：{payload['code']}")
        detail = api.get(f"/writing/scenario-packages/{package['id']}")
        active = next(
            item for item in detail["versions"] if item["id"] == package["current_version_id"]
        )
        for field in (resource["required_key"], resource["available_key"]):
            if field not in active["input_schema"]["properties"]:
                raise AssertionError(f"当前场景包版本缺少 Fixture 字段：{payload['code']} / {field}")

        project = api.post(
            "/writing/projects",
            {
                "code": f"accept-{payload['disaster_type']}-{suffix}",
                "name": f"{payload['name']}（确定性技术验收）",
                "application_id": earthquake_project.get("application_id"),
                "scenario_package_version_id": package["current_version_id"],
                "knowledge_product_release_id": earthquake_project["knowledge_product_release_id"],
                "config": {
                    "business_validation": "pending_customer_confirmation",
                    "disclaimer": fixture["disclaimer"],
                },
            },
        )
        try:
            event_fact = api.post(
                f"/writing/projects/{project['id']}/facts",
                {
                    "fact_key": "event_name",
                    "label": "事件名称",
                    "fact_type": "official_brief",
                    "value": value_payload(inputs["event_name"]),
                    "source_type": "official_brief",
                    "source_id": f"fixture:{payload['code']}",
                    "source_version": "v1",
                    "source_locator": {"fixture": path.name, "disclaimer": fixture["disclaimer"]},
                    "confidence": 1,
                    "verification_status": "verified",
                },
            )
            input_facts: dict[str, dict] = {}
            for role, key in (
                ("required", resource["required_key"]),
                ("available", resource["available_key"]),
            ):
                input_facts[role] = api.post(
                    f"/writing/projects/{project['id']}/facts",
                    {
                        "fact_key": key,
                        "label": f"{resource['label']}{'需求' if role == 'required' else '可用'}",
                        "fact_type": "official_brief",
                        "value": value_payload(inputs[key]),
                        "unit": resource["unit"],
                        "source_type": "official_brief",
                        "source_id": f"fixture:{payload['code']}",
                        "source_version": "v1",
                        "source_locator": {"fixture": path.name, "field": key, "disclaimer": fixture["disclaimer"]},
                        "confidence": 1,
                        "verification_status": "verified",
                    },
                )
            computation = api.post(
                f"/writing/projects/{project['id']}/compute",
                {
                    "operation": "resource_gap",
                    "inputs": {
                        "required": inputs[resource["required_key"]],
                        "available": inputs[resource["available_key"]],
                    },
                    "input_fact_ids": [input_facts["required"]["id"], input_facts["available"]["id"]],
                    "input_fact_map": {
                        "required": input_facts["required"]["id"],
                        "available": input_facts["available"]["id"],
                    },
                    "output_fact_key": f"{payload['disaster_type']}_resource_gap",
                    "output_label": resource["label"],
                    "output_unit": resource["unit"],
                },
            )
            if computation["result"]["value"] != resource["expected_gap"]:
                raise AssertionError(f"{payload['code']} 资源缺口结果错误：{computation['result']}")

            fact_block = {
                "id": f"{payload['disaster_type']}-event",
                "type": "verified_fact",
                "freshness_status": "current",
                "children": [{"text": f"事件：{inputs['event_name']}"}],
            }
            metric_block = {
                "id": f"{payload['disaster_type']}-gap",
                "type": "computed_metric",
                "formula": "resource_gap",
                "freshness_status": "current",
                "children": [{"text": f"{resource['label']}：{resource['expected_gap']}{resource['unit']}"}],
            }
            content = [
                {"id": f"{payload['disaster_type']}-title", "type": "h1", "children": [{"text": payload["name"]}]},
                {"id": f"{payload['disaster_type']}-notice", "type": "callout", "children": [{"text": fixture["disclaimer"]}]},
                fact_block,
                metric_block,
            ]
            document = api.post(
                "/writing/documents",
                {"project_id": project["id"], "title": f"{payload['name']}（技术验收稿）", "content": content[:2]},
            )
            api.post(
                f"/writing/documents/{document['id']}/bindings",
                {
                    "block_id": fact_block["id"],
                    "block_type": "verified_fact",
                    "source_type": "official_brief",
                    "source_id": event_fact["source_id"],
                    "source_version": event_fact["source_version"],
                    "fact_id": event_fact["id"],
                    "evidence_ids": [event_fact["id"]],
                    "content_hash": block_hash(fact_block),
                    "block_content": fact_block,
                    "verification_status": "verified",
                    "freshness_status": "current",
                    "metadata": {"fixture": path.name, "business_validation": "pending_customer_confirmation"},
                },
            )
            api.post(
                f"/writing/documents/{document['id']}/bindings",
                {
                    "block_id": metric_block["id"],
                    "block_type": "computed_metric",
                    "source_type": "computation",
                    "source_id": computation["id"],
                    "computation_run_id": computation["id"],
                    "evidence_ids": [input_facts["required"]["id"], input_facts["available"]["id"]],
                    "content_hash": block_hash(metric_block),
                    "block_content": metric_block,
                    "verification_status": "verified",
                    "freshness_status": "current",
                    "metadata": {"operation": "resource_gap", "fixture": path.name},
                },
            )
            api.post(
                f"/writing/documents/{document['id']}/versions",
                {"content": content, "change_summary": "六灾种独立确定性验收链路", "publish": False},
            )
            validation = api.post(f"/writing/documents/{document['id']}/validate", {"for_publish": False})
            if not validation["ok"] or validation["issues"]:
                raise AssertionError(f"{payload['code']} 可信文稿审校失败：{validation}")
            export = api.post(f"/writing/documents/{document['id']}/exports", {"output_format": "json"})
            if export["status"] != "succeeded" or not export.get("checksum"):
                raise AssertionError(f"{payload['code']} JSON 导出失败：{export}")
            results.append(
                {
                    "scenario": payload["code"],
                    "business_validation": "pending_customer_confirmation",
                    "input_contract": "passed",
                    "verified_facts": 3,
                    "resource_gap": computation["result"]["value"],
                    "trusted_bindings": 2,
                    "document_validation": "passed",
                    "json_export": "passed",
                }
            )
        finally:
            api.delete(f"/writing/projects/{project['id']}")

    if len(results) != 6:
        raise AssertionError(f"应完成六类非地震场景，实际 {len(results)}")
    print(json.dumps({"passed": len(results), "items": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
