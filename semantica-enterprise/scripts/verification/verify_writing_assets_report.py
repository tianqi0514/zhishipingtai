"""Real report generation from reviewed assets in the isolated 8082 environment.

Fixture facts are explicitly constructed, not customer data. Report text is
never supplied by this verifier: only the actual Harness stream can create it.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import httpx

from packages.platform.config import get_settings


def changed_confirmed_inputs(facts, definitions):
    latest = {row["fact_key"]: row for row in facts}
    return [key for key, _, value, _ in definitions if key in latest
            and latest[key]["verification_status"] == "verified" and latest[key]["value"] != value]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--article-title", default="科研用房建设项目可行性研究讨论稿")
    parser.add_argument("--reset-inputs", action="store_true", help="显式允许将隔离验收输入重置为初始 Fixture；不修改旧文稿版本")
    args = parser.parse_args()
    settings = get_settings()
    with httpx.Client(base_url="http://127.0.0.1:8082/api/v1", timeout=600) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if not response.is_success:
                detail = response.json().get("detail") if "json" in response.headers.get("content-type", "") else "service_error"
                raise ValueError(json.dumps({"path": path, "status": response.status_code, "detail": detail}, ensure_ascii=False))
            return response.json()

        request("POST", "/auth/login", json={"username": settings.bootstrap_admin_username,
                "password": settings.bootstrap_admin_password})
        space = next(row for row in request("GET", "/spaces") if row["code"] == "writing-assets-validation")
        project_name = "科研用房报告生成验收"
        project = next((row for row in request("GET", "/writing/projects") if row["name"] == project_name), None)
        if project is None:
            project = request("POST", "/writing/projects", json={"name": project_name, "space_id": space["id"]})
        project_path = f"/writing/projects/{project['id']}"
        materials = request("GET", project_path + "/materials")
        current = next((row for row in materials if row["version"]["filename"] == "writing_assets_current.md"), None)
        if current is None:
            path = Path("tests/fixtures/writing_assets_current.md")
            with path.open("rb") as file:
                uploaded = request("POST", "/documents/upload", data={"space_id": space["id"],
                    "knowledge_processing_targets": '["fulltext"]', "knowledge_processing_mode": "vector",
                    "material_role": "task_data"}, files={"file": (path.name, file, "text/markdown")})
            current = request("POST", project_path + "/materials", json={"document_id": uploaded["document"]["id"],
                              "version_id": uploaded["version"]["id"], "material_role": "task_data"})
            job = request("POST", f"/documents/{uploaded['document']['id']}/process",
                          json={"targets": ["fulltext"], "mode": "vector"})
            status = request("GET", f"/jobs/{job['id']}")
            if status["status"] != "succeeded":
                raise ValueError("本次业务资料未完成真实全文发布")
        request("POST", project_path + "/knowledge-release/refresh")
        title = args.article_title
        document = next((row for row in request("GET", project_path + "/documents") if row["title"] == title), None)
        if document is None:
            document = request("POST", "/writing/documents", json={"project_id": project["id"], "title": title,
                "document_type": "custom", "audience": "科研管理部门", "purpose": "讨论建设必要性、费用边界与待核实事项，非审批文件",
                "applicability": {"time_range": "2026年10月", "matter": "科研用房建设", "organization": "未指定真实单位"},
                "writing_requirements": "根据本次任务资料和已确认计算写正式讨论稿；历史材料只复用结构与方法，不编造单位、法规、地名和批准结论。"})
        document = request("GET", f"/writing/documents/{document['id']}")
        assets = request("GET", "/writing-assets", params={"project_id": project["id"]})
        keys = ["feasibility_report_structure", "construction_cost_calc", "other_cost_calc", "reserve_cost_calc", "total_investment_calc"]
        selected = [next(row for row in assets if row["definition"]["key"] == key and row["status"] == "reviewed") for key in keys]
        if not (document.get("applicability") or {}).get("asset_bundle"):
            release = request("POST", "/writing-assets/releases", json={"space_id": space["id"], "asset_ids": [row["id"] for row in selected]})
            request("POST", f"/writing-assets/documents/{document['id']}/adopt", json={"release_id": release["id"],
                "asset_ids": [row["id"] for row in selected], "base_version_id": document["current_version_id"],
                "scope_confirmed": True, "adopt_outline": True})
        facts_path = project_path + "/facts"
        facts = request("GET", facts_path)
        text_inputs = {
            "使用单位": "科研团队与科研管理部门，具体单位名称和法定职责待确认",
            "使用需求": "共享科研空间、公共设备集中管理与团队交流，尚无核验的团队人数和使用率",
            "项目名称": "科研用房建设项目",
            "建筑规模": "暂定建筑面积20000平方米；配置比例与选址尚待专项论证",
            "用房台账": "台账尚未提供，只能进行定性需求讨论，不作定量供需结论",
            "敏感性分析参数": "讨论单价上浮对费用链的影响，其他输入保持不变；不编造市场价格区间",
            "风险因素": "价格来源不足、专项设备费用缺失、运营管理责任未明确",
            "现有科研空间情况": "空间分散与共享预约冲突为定性说明，没有已确认面积和使用率",
            "投资测算": "采用本次确认输入与四条确定性费用方法；土地、融资和专项设备费用尚缺，不能视为零",
        }
        numbers = {"building_area": (20000, "建筑面积", "平方米"), "unit_cost": (5000, "建筑安装单价", "元/平方米"),
                   "other_cost_ratio": (0.1, "工程其他费用比例", ""), "reserve_ratio": (0.05, "基本预备费比例", "")}
        definitions = [(row["fact_key"], row["label"], {"text": text_inputs[row["label"]]}, row.get("unit") or "")
                       for row in facts if row["label"] in text_inputs]
        definitions += [(key, label, {"number": number}, unit) for key, (number, label, unit) in numbers.items()]
        changed = changed_confirmed_inputs(facts, definitions)
        if changed and not args.reset_inputs:
            raise ValueError("本次输入已被人工修改，保留当前值；如确需重置隔离验收输入，请显式传 --reset-inputs。受影响字段：" + ", ".join(changed))
        latest = {row["fact_key"]: row for row in facts}
        for key, label, value, unit in definitions:
            if key in latest and latest[key]["value"] == value and latest[key]["verification_status"] == "verified":
                continue
            fact = request("POST", facts_path, json={"fact_key": key, "label": label, "value": value, "unit": unit,
                "source_type": "manual_input", "fact_type": "manual_input", "verification_status": "unverified",
                "source_locator": {"verification_fixture": "writing_assets_current.md", "constructed_input": True,
                                   "document_version_id": current["version_id"]}})
            request("POST", facts_path + f"/{fact['id']}/confirm", json={"decision": "confirm",
                    "reason": "核对已上传的软件验收任务资料，全部为构造输入；不代表真实项目或审批意见"})
        document = request("GET", f"/writing/documents/{document['id']}")
        bundle = document["applicability"]["asset_bundle"]
        calculation = request("POST", f"/writing-assets/documents/{document['id']}/compute", json={
            "base_version_id": document["current_version_id"], "release_id": bundle["release_id"], "checksum": bundle["checksum"]})
        assert [row["fact"]["value"]["number"] for row in calculation["items"]] == [10000, 1000, 550, 11550]
        summary = {"environment": "isolated_local", "project_id": project["id"], "document_id": document["id"],
                   "current_material": current["document_id"], "calculation_count": len(calculation["items"]),
                   "current_input_count": len(definitions), "prepared": True}
        if args.generate:
            existing = request("GET", project_path + "/generation-runs")
            completed = next((row for row in existing if row["status"] == "completed" and row["document_id"] == document["id"]), None)
            if completed:
                summary["generation"] = {"run_id": completed["id"], "status": "already_completed"}
            else:
                run = request("POST", project_path + "/generate-report", json={"document_id": document["id"], "allow_partial": False})
                events = Counter()
                for _ in range(4):
                    with client.stream("POST", f"/writing/generation-runs/{run['id']}/agent") as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if line.startswith("event: "):
                                events[line[7:]] += 1
                    run = request("POST", f"/writing/generation-runs/{run['id']}/finalize")
                    if run["status"] == "completed":
                        break
                if run["status"] != "completed":
                    raise ValueError("真实写作未完成，不把候选文字计为正式生成结果")
                summary["generation"] = {"run_id": run["id"], "status": run["status"], "events": dict(events)}
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "message": str(exc)[:1200]}, ensure_ascii=False))
        raise SystemExit(1)
