"""Isolated synthetic fixture for live Plate dependency acceptance.

This article is deliberately a metric-change test, not the sample-quality
article. No customer demo or historical report is modified by setup.
"""

from __future__ import annotations

import argparse
import json

from packages.platform.writing import content_hash
from scripts.miaobi.demo_client import DemoClient


SPACE_CODE = "miaobi-sample-profile-acceptance-20260915"
PROJECT_CODE = "miaobi-metric-binding-acceptance-20260915-v2"
TITLE = "搜救资源指标联动演练稿（模拟数据）"
INPUTS = {
    "rescue_required": (500, "搜救人员需求", "人"),
    "rescue_available": (320, "可用搜救人员", "人"),
    "trauma_beds_required": (330, "创伤床位需求", "张"),
    "county_trauma_beds": (110, "县域可用床位", "张"),
    "callable_trauma_beds": (250, "可调用床位", "张"),
    "tents_required": (7000, "帐篷需求", "顶"),
    "tents_available": (5200, "可用帐篷", "顶"),
}


def emit(stage: str, value: dict) -> None:
    print(json.dumps({"stage": stage, "result": value}, ensure_ascii=False), flush=True)


def setup(api: DemoClient) -> dict:
    space = api.one("/spaces", "code", SPACE_CODE)
    if not space:
        raise RuntimeError("请先执行样稿验收 setup，创建隔离知识空间")
    project = api.one("/writing/projects", "code", PROJECT_CODE)
    if not project:
        scenario = next((item for item in api.get("/writing/scenario-packages")
                         if item["code"] == "earthquake-response-plan" and item["status"] == "active"), None)
        if not scenario or not scenario.get("current_version_id"):
            raise RuntimeError("系统缺少已确认的地震公式场景包，不能造假测算")
        project = api.post("/writing/projects", {
            "code": PROJECT_CODE, "name": "妙笔·指标联动独立验收（模拟数据）",
            "space_id": space["id"], "scenario_package_version_id": scenario["current_version_id"],
            "config": {"disclaimer": "指标均为模拟验收值，不代表实际地震灾情"},
        })
    facts = {item["fact_key"]: item for item in api.get(f"/writing/projects/{project['id']}/facts")}
    for key, (number, label, unit) in INPUTS.items():
        if key not in facts:
            facts[key] = api.post(f"/writing/projects/{project['id']}/facts", {
                "fact_key": key, "label": label, "fact_type": "manual_input",
                "value": {"number": number}, "unit": unit,
                "source_type": "manual_input", "source_id": PROJECT_CODE,
                "verification_status": "verified",
            })
    baseline = api.post(f"/writing/projects/{project['id']}/computations/run-baseline", {})
    gap = next(item["run"] for item in baseline["items"] if item["fact"]["fact_key"] == "rescue_gap")
    metric = {
        "id": "metric-rescue-gap", "type": "computed_metric", "label": "搜救人员缺口", "value": 180,
        "unit": "人", "computation_run_id": gap["id"],
        "children": [{"text": "经核验与测算，搜救人员缺口为180人。"}],
    }
    resource = {"id": "p-resource", "type": "p", "children": [{"text": "本次模拟需求为500人，当前可用搜救人员320人，缺口180人。"}]}
    summary = {"id": "p-summary", "type": "p", "children": [{"text": "当前缺口180人，应根据后续到位情况更新保障安排。"}]}
    table = {"id": "table-rescue", "type": "table", "children": [{"type": "tr", "children": [
        {"type": "td", "children": [{"type": "p", "children": [{"text": "可用搜救人员320人"}]}]},
        {"type": "td", "children": [{"type": "p", "children": [{"text": "缺口180人"}]}]},
    ]}]}
    untouched = {"id": "p-unrelated", "type": "p", "children": [{"text": "其他业务安排保持不变。"}]}
    documents = api.get(f"/writing/projects/{project['id']}/documents")
    article = next((item for item in documents if item["title"] == TITLE), None)
    if not article:
        article = api.post("/writing/documents", {
            "project_id": project["id"], "title": TITLE, "document_type": "exercise_note",
            "purpose": "验证输入—公式—测算块、正文和表格绑定后的人工预览与选择接受",
            "content": [{"id": "h-report", "type": "h1", "children": [{"text": TITLE}]}, metric, resource, summary, table, untouched],
        })
    existing = {row["block_id"] for row in api.get(f"/writing/documents/{article['id']}/bindings")}
    for node, fact_id, metadata in (
        (metric, None, {}),
        (resource, facts["rescue_available"]["id"], {"input_keys": ["rescue_available"], "metric_keys": ["rescue_gap"],
            "input_fact_ids": [facts["rescue_available"]["id"]], "computation_run_ids": [gap["id"]]}),
        (summary, None, {"metric_keys": ["rescue_gap"], "computation_run_ids": [gap["id"]]}),
        (table, facts["rescue_available"]["id"], {"input_keys": ["rescue_available"], "metric_keys": ["rescue_gap"],
            "input_fact_ids": [facts["rescue_available"]["id"]], "computation_run_ids": [gap["id"]]}),
    ):
        if node["id"] in existing:
            continue
        api.post(f"/writing/documents/{article['id']}/bindings", {
            "block_id": node["id"], "block_type": node["type"], "source_type": "computation",
            "fact_id": fact_id, "computation_run_id": gap["id"],
            "content_hash": content_hash(node), "block_content": node,
            "verification_status": "verified", "metadata": metadata,
        })
    emit("setup", {"space_id": space["id"], "project_id": project["id"], "article_id": article["id"],
                   "input": 320, "expected_gap": 180, "bound_blocks": [metric["id"], resource["id"], summary["id"], table["id"]]})
    return {"project": project, "article": article}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["setup", "reset", "inspect"])
    stage = parser.parse_args().stage
    api = DemoClient()
    project = api.one("/writing/projects", "code", PROJECT_CODE)
    if stage == "setup":
        setup(api)
    elif stage == "reset" and project:
        article = next(item for item in api.get(f"/writing/projects/{project['id']}/documents") if item["title"] == TITLE)
        current = next(item for item in api.get(f"/writing/projects/{project['id']}/facts")
                       if item["fact_key"] == "rescue_available" and item["active"])
        if current["value"].get("number") != 320:
            preview = api.post(f"/writing/projects/{project['id']}/input-changes/preview", {
                "document_id": article["id"], "changes": [{"fact_key": "rescue_available",
                "new_value": {"number": 320}, "reason": "隔离验收复演：恢复模拟到位人数基线"}],
            })
            accepted = [item["block_id"] for item in preview["impact"]["content_proposals"] if item["selectable"]]
            applied = api.post(f"/writing/projects/{project['id']}/input-changes/apply", {
                "preview_id": preview["id"], "accepted_block_ids": accepted,
            })
            emit("reset", {"project_id": project["id"], "article_id": article["id"],
                           "restored_input": 320, "accepted_blocks": accepted, "version": applied.get("document_version_id")})
        else:
            emit("reset", {"project_id": project["id"], "article_id": article["id"], "already_at_baseline": True})
    elif project:
        article = next(item for item in api.get(f"/writing/projects/{project['id']}/documents") if item["title"] == TITLE)
        content = api.get(f"/writing/documents/{article['id']}")["current_version"]["content"]
        emit("inspect", {"article_id": article["id"], "blocks": {
            node.get("id"): "".join(str(child.get("text") or "") for child in node.get("children") or [])
            for node in content if node.get("id") in {"metric-rescue-gap", "p-resource", "p-summary", "p-unrelated"}
        }})
    else:
        raise RuntimeError("请先执行 setup")


if __name__ == "__main__":
    main()
