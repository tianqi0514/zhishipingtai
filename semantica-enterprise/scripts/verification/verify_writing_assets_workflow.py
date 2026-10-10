"""Live REST verification against the dedicated local asset-validation project.

This is not the browser test and does not manufacture prose or index releases.
The four numeric inputs below are explicitly constructed verification data.
"""
from __future__ import annotations

import argparse
import json
from urllib.parse import urlparse

import httpx

from packages.platform.config import get_settings
from packages.platform.writing_graph import content_hash


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8082")
    parser.add_argument("--publish-fulltext", action="store_true")
    parser.add_argument("--verify-cost-impact", action="store_true")
    args = parser.parse_args()
    target = urlparse(args.base_url)
    if target.hostname not in {"127.0.0.1", "localhost"} or target.port != 8082:
        raise ValueError("仅允许专用本地 8082 验证环境")
    with httpx.Client(base_url=args.base_url + "/api/v1", timeout=240) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        settings = get_settings()
        request("POST", "/auth/login", json={"username": settings.bootstrap_admin_username,
                "password": settings.bootstrap_admin_password})
        projects = request("GET", "/writing/projects")
        project = next(item for item in projects if item["name"] == "科研用房可控写作验收")
        articles = request("GET", f"/writing/projects/{project['id']}/documents")
        article = next(item for item in articles if item["title"] == "科研用房建设项目可行性研究报告（验收稿）")
        prefix = f"/writing-assets/documents/{article['id']}"
        assets = request("GET", "/writing-assets", params={"project_id": project["id"]})
        keys = ["feasibility_report_structure", "construction_cost_calc", "other_cost_calc",
                "reserve_cost_calc", "total_investment_calc"]
        selected = {key: next(item for item in assets if item["definition"]["key"] == key
                              and item["status"] == "reviewed") for key in keys}
        published = request("POST", "/writing-assets/releases", json={"space_id": project["knowledge_space_id"],
                            "asset_ids": [selected[key]["id"] for key in keys]})
        document = request("GET", f"/writing/documents/{article['id']}")
        if not (document.get("applicability") or {}).get("asset_bundle"):
            adopted = request("POST", prefix + "/adopt", json={"release_id": published["id"],
                "asset_ids": [selected[key]["id"] for key in keys], "base_version_id": document["current_version_id"],
                "scope_confirmed": True, "adopt_outline": True})
            assert len(adopted["document"]["applicability"]["native_outline"]["sections"]) == 4
        facts_path = f"/writing/projects/{project['id']}/facts"
        current = {row["fact_key"]: row for row in request("GET", facts_path)}
        data = {"building_area": (20000, "建筑面积", "平方米"), "unit_cost": (5000, "建筑安装单价", "元/平方米"),
                "other_cost_ratio": (0.1, "工程其他费用比例", ""), "reserve_ratio": (0.05, "基本预备费比例", "")}
        for key, (number, label, unit) in data.items():
            if key in current and current[key]["value"].get("number") == number and current[key]["verification_status"] == "verified":
                continue
            row = request("POST", facts_path, json={"fact_key": key, "label": label, "unit": unit,
                "fact_type": "manual_input", "source_type": "manual_input", "value": {"number": number},
                "source_locator": {"value_type": "number", "verification_fixture": "constructed_current_inputs"},
                "verification_status": "unverified"})
            request("POST", facts_path + f"/{row['id']}/confirm", json={"decision": "confirm",
                "reason": "软件验收构造的本次输入；已核对值、单位和同一估算口径，不代表实际项目数据"})
        results = []
        computed_rows = []
        for key, expected in zip(keys[1:], (10000, 1000, 550, 11550)):
            computed = request("POST", prefix + f"/compute/{selected[key]['id']}")
            assert computed["fact"]["value"]["number"] == expected
            assert computed["run"]["input_fact_ids"]
            computed_rows.append(computed)
            results.append({"method": key, "value": expected, "unit": computed["fact"]["unit"],
                            "run_id": computed["run"]["id"]})
        summary = {"environment": "isolated_local", "project_id": project["id"], "document_id": article["id"],
                   "adopted_asset_count": len(keys), "current_input_count": len(data), "calculations": results}
        # One business operation follows the declared producer dependencies.
        # Repeated computation must reuse these exact current input versions.
        state = request("GET", f"/writing/documents/{article['id']}")
        bundle = state["applicability"]["asset_bundle"]
        chain = request("POST", prefix + "/compute", json={"base_version_id": state["current_version_id"],
            "release_id": bundle["release_id"], "checksum": bundle["checksum"]})
        assert [row["fact"]["value"]["number"] for row in chain["items"]] == [10000, 1000, 550, 11550]
        assert all(row["reused"] for row in chain["items"])
        assert request("GET", f"/writing/documents/{article['id']}")["current_version_id"] == state["current_version_id"]
        summary["one_click_chain"] = {"result_count": len(chain["items"]), "all_reused": True, "document_unchanged": True}
        if args.verify_cost_impact:
            # This is a calculation-binding fixture, not a generated report.
            title = "投资估算联动核验片段"
            check_article = next((item for item in articles if item["title"] == title), None)
            if not check_article:
                check_article = request("POST", "/writing/documents", json={"project_id": project["id"],
                    "title": title, "purpose": "软件验收构造的计算绑定片段，不是 Agent 生成的正式报告"})
            state = request("GET", f"/writing/documents/{check_article['id']}")
            if not state["current_version"]["content"]:
                nodes = [{"id": f"cost-check-{item['fact']['fact_key']}", "type": "computed_metric",
                    "label": item["fact"]["label"], "value": item["fact"]["value"]["number"],
                    "unit": item["fact"]["unit"], "computation_run_id": item["run"]["id"], "children": [{"text": ""}]}
                    for item in computed_rows]
                nodes.append({"id": "unbound-number-check", "type": "p", "children": [{"text": "无关记录编号5000，历史台账编码11550，不参与本次计算。"}]})
                request("POST", f"/writing/documents/{check_article['id']}/versions", json={
                    "content": nodes, "change_summary": "确定性费用结果绑定验收，不是报告质量验收"})
                for node, computed in zip(nodes, computed_rows):
                    request("POST", f"/writing/documents/{check_article['id']}/bindings", json={
                        "block_id": node["id"], "block_type": "computed_metric", "source_type": "computation",
                        "computation_run_id": computed["run"]["id"], "fact_id": computed["fact"]["id"],
                        "content_hash": content_hash(node), "block_content": node, "verification_status": "verified"})
            before = request("GET", f"/writing/documents/{check_article['id']}")
            change_path = f"/writing/projects/{project['id']}/input-changes"
            preview = request("POST", change_path + "/preview", json={"document_id": check_article["id"],
                "changes": [{"fact_key": "unit_cost", "new_value": {"number": 5500}, "reason": "构造单价上浮10%的联动验收"}]})
            calculations = preview["impact"]["calculations"]
            actual = sorted(item["new_value"] for item in calculations)
            assert actual == [605, 1100, 11000, 12705], actual
            after = request("GET", f"/writing/documents/{check_article['id']}")
            assert before["current_version_id"] == after["current_version_id"]
            assert before["current_version"]["content"] == after["current_version"]["content"]
            request("POST", change_path + f"/{preview['id']}/cancel")
            summary["cost_impact"] = {"input": "5000→5500", "new_values": actual, "preview_only": True,
                "document_unchanged": True, "cancelled": True, "verification_document_id": check_article["id"]}
        if args.publish_fulltext:
            materials = request("GET", f"/writing/projects/{project['id']}/materials")
            material = next(item for item in materials if item["version"]["filename"] == "writing_assets_history.md")
            job = request("POST", f"/documents/{material['document_id']}/process",
                          json={"mode": "vector", "targets": ["fulltext"]})
            job = request("GET", f"/jobs/{job['id']}")
            summary["index_job"] = {key: job.get(key) for key in ("id", "status", "progress", "error_code")}
            if job["status"] != "succeeded":
                print(json.dumps(summary, ensure_ascii=False))
                raise ValueError("真实全文加工未成功，不能声明索引已就绪")
            request("POST", f"/writing/projects/{project['id']}/knowledge-space",
                    json={"space_id": project["knowledge_space_id"]})
            result = request("POST", f"/writing/projects/{project['id']}/knowledge/search", json={
                "document_id": article["id"], "query": "投资估算", "use_keyword": True,
                "use_vector": False, "use_graph": False, "use_reranker": False})
            hits = result.get("items", result.get("results", []))
            if not hits:
                raise ValueError("索引发布成功但正文素材无法召回，不能声明写作检索已就绪")
            summary["writing_fulltext_hits"] = len(hits)
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1)
