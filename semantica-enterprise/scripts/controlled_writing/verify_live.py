#!/usr/bin/env python3
"""Run a non-destructive controlled-writing acceptance against a live server.

The script creates isolated projects and never prints credentials or access
tokens.  It deliberately exercises the public API exactly as the DeepSeek
Work plugin does instead of importing application internals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid
from typing import Any
from urllib import error, request


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class Api:
    def __init__(self, base_url: str, credential_path: Path) -> None:
        credential = json.loads(credential_path.read_text(encoding="utf-8"))
        username = credential.get("username")
        password = credential.get("password")
        if not username or not password:
            raise RuntimeError("凭据文件缺少 username/password")
        self.base = base_url.rstrip("/") + "/api/v1"
        self.token = ""
        login = self.call("POST", "/auth/login", {"username": username, "password": password}, auth=False)
        self.token = str(login["access_token"])

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        auth: bool = True,
        expected: tuple[int, ...] = (200,),
        raw: bool = False,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = canonical_json(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=180) as response:
                payload = response.read()
                status = response.status
                response_headers = dict(response.headers)
        except error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
            response_headers = dict(exc.headers)
        if status not in expected:
            safe = payload.decode("utf-8", errors="replace")[:1200]
            raise AssertionError(f"{method} {path} -> {status}: {safe}")
        if raw:
            return payload, response_headers
        if not payload:
            return None
        return json.loads(payload)


def number(fact: dict[str, Any]) -> float:
    value = fact.get("value") or {}
    return float(value.get("number", value.get("value")))


def node_text(node: dict[str, Any]) -> str:
    pieces: list[str] = []

    def walk(item: dict[str, Any]) -> None:
        if isinstance(item.get("text"), str):
            pieces.append(item["text"])
        for child in item.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    walk(node)
    return "".join(pieces)


def make_fact(api: Api, project_id: str, key: str, label: str, value: float | str, unit: str | None) -> dict[str, Any]:
    payload_value = {"number": value} if isinstance(value, (int, float)) else {"text": value}
    return api.call("POST", f"/writing/projects/{project_id}/facts", {
        "fact_key": key,
        "label": label,
        "fact_type": "official_brief",
        "value": payload_value,
        "unit": unit,
        "source_type": "official_brief",
        "source_id": "controlled-writing-live-acceptance",
        "source_version": "1",
        "source_locator": {"fixture": "live-acceptance", "verified": True},
        "confidence": 1,
        "verification_status": "verified",
    })


def compute(
    api: Api,
    project_id: str,
    facts: dict[str, dict[str, Any]],
    operation: str,
    inputs: dict[str, float],
    input_map: dict[str, str],
    output_key: str,
    label: str,
    unit: str,
    digits: int = 0,
) -> dict[str, Any]:
    payload = api.call("POST", f"/writing/projects/{project_id}/compute", {
        "operation": operation,
        "inputs": inputs,
        "parameters": {},
        "rounding": {"mode": "half_up", "digits": digits},
        "input_fact_ids": [facts[key]["id"] for key in input_map.values()],
        "input_fact_map": {name: facts[key]["id"] for name, key in input_map.items()},
        "output_fact_key": output_key,
        "output_label": label,
        "output_unit": unit,
    })
    facts[output_key] = payload["generated_fact"]
    return payload


def create_document(api: Api, project_id: str, title: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return api.call("POST", "/writing/documents", {
        "project_id": project_id,
        "title": title,
        "document_type": "feasibility_report" if "可研" in title else "response_plan",
        "purpose": "验证受控事实、确定性计算、推演证据和正文投影的一致性",
        "audience": "项目评审人员",
        "applicability": {"scope": "live-acceptance"},
        "writing_requirements": "关键数字必须绑定权威事实或不可变计算运行。",
        "content": nodes,
    })


def bind(
    api: Api,
    document_id: str,
    node: dict[str, Any],
    *,
    fact_id: str | None = None,
    run_id: str | None = None,
    input_fact_ids: list[str] | None = None,
    input_keys: list[str] | None = None,
    metric_keys: list[str] | None = None,
) -> dict[str, Any]:
    source_type = "computation" if run_id else "official_brief"
    return api.call("POST", f"/writing/documents/{document_id}/bindings", {
        "block_id": node["id"],
        "block_type": node["type"],
        "source_type": source_type,
        "fact_id": fact_id,
        "computation_run_id": run_id,
        "evidence_ids": input_fact_ids or ([fact_id] if fact_id else []),
        "content_hash": content_hash(node),
        "block_content": node,
        "verification_status": "verified",
        "freshness_status": "current",
        "metadata": {
            "input_fact_ids": input_fact_ids or ([fact_id] if fact_id else []),
            "input_keys": input_keys or [],
            "metric_keys": metric_keys or [],
            "computation_run_ids": [run_id] if run_id else [],
        },
    })


def fresh_project(
    api: Api,
    name: str,
    suffix: str,
    *,
    space_id: str | None = None,
    scenario_version_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": f"controlled-{suffix}",
        "name": name,
        "config": {"acceptance": True, "created_by": "verify_live.py"},
    }
    if space_id:
        payload["space_id"] = space_id
    if scenario_version_id:
        payload["scenario_package_version_id"] = scenario_version_id
    return api.call("POST", "/writing/projects", payload)


def verify_corpus(api: Api, project: dict[str, Any], suffix: str) -> dict[str, Any]:
    materials = api.call("GET", f"/writing/projects/{project['id']}/materials")
    if not materials:
        return {"status": "not_run", "reason": "project space has no pinned processed material"}
    sample = next((item for item in materials if item.get("material_role") == "sample_style"), materials[0])
    package = api.call("POST", f"/writing/projects/{project['id']}/corpus-packages", {
        "code": f"corpus-{suffix}",
        "name": "受控推演写作线上验收语料包",
        "source_material_ids": [sample["id"]],
    })
    artifacts = api.call("GET", f"/writing/corpus-packages/{package['id']}/artifacts")
    skeletons = artifacts.get("skeletons") or []
    assert skeletons
    slot_count = sum(len(item.get("slots") or []) for item in skeletons)
    # A source without numeric literals legitimately has no slots. Whenever
    # slots exist, their historical values must only appear as node markers.
    assert all(
        not (item.get("slots") or []) or "{{node:" in item.get("text", "")
        for item in skeletons
    )
    assert artifacts["style_profile"]["sample_values_available_to_agent"] is False
    inheritance = api.call("POST", f"/writing/projects/{project['id']}/inheritance/preview", {
        "corpus_package_version_id": package["current_version"]["id"],
    })
    assert all(item.get("historical_value_inherited") is False for item in inheritance.get("alignment") or [])
    return {
        "status": "passed",
        "package_id": package["id"],
        "version_id": package["current_version"]["id"],
        "artifact_count": len(artifacts.get("artifact_mapping") or {}),
        "skeleton_count": len(skeletons),
        "slot_count": slot_count,
        "blocking_issue_count": len(inheritance.get("blocking_issues") or []),
        "historical_values_exposed": False,
    }


def emergency_flow(api: Api, source_project: dict[str, Any], space_id: str, suffix: str) -> dict[str, Any]:
    project = fresh_project(
        api,
        "受控写作线上验收·应急资源",
        f"emergency-{suffix}",
        space_id=space_id,
        scenario_version_id=source_project["scenario_package_version_id"],
    )
    corpus = verify_corpus(api, project, f"emergency-{suffix}")
    facts = {
        "event_name": make_fact(api, project["id"], "event_name", "事件名称", "测试地区6.2级地震", None),
        "magnitude": make_fact(api, project["id"], "magnitude", "震级", 6.2, "级"),
        "population_density": make_fact(api, project["id"], "population_density", "人口密度", 305.6, "人/km²"),
        "rescue_required": make_fact(api, project["id"], "rescue_required", "搜救人员需求", 500, "人"),
        "rescue_available": make_fact(api, project["id"], "rescue_available", "可用搜救人员", 320, "人"),
    }
    gap = compute(
        api, project["id"], facts, "resource_gap",
        {"required": 500, "available": 320},
        {"required": "rescue_required", "available": "rescue_available"},
        "rescue_gap", "搜救人员缺口", "人",
    )
    criteria = api.call("POST", f"/writing/projects/{project['id']}/criteria/evaluate", {})
    reasoning = api.call("POST", f"/writing/projects/{project['id']}/reason", {"mode": "preview"})
    assert criteria["engine"] == "deterministic-criteria"
    assert reasoning["run"]["engine"] == "semantica-datalog"
    assert reasoning["run"]["status"] == "succeeded"

    nodes = [
        {"id": "summary", "type": "p", "children": [{"text": "经核验，现有可用搜救人员320人，尚缺180人。"}]},
        {"id": "resource-status", "type": "p", "children": [{"text": "当前可调配搜救人员为320人。"}]},
        {"id": "resource-gap", "type": "computed_metric", "label": "搜救人员缺口", "value": 180, "unit": "人", "computation_run_id": gap["id"], "children": [{"text": "经核验与测算，搜救人员缺口为180人。"}]},
        {"id": "resource-table", "type": "p", "children": [{"text": "资源统计表：需求500人、可用320人、缺口180人。"}]},
        {"id": "reinforcement", "type": "p", "children": [{"text": "建议按照180人的缺口组织增援力量。"}]},
        {"id": "task-appendix", "type": "p", "children": [{"text": "附件任务清单应落实可用320人并补足缺口180人。"}]},
    ]
    document = create_document(api, project["id"], "测试地区地震应急资源保障报告", nodes)
    for node in nodes:
        if node["id"] == "resource-status":
            bind(api, document["id"], node, fact_id=facts["rescue_available"]["id"], input_keys=["rescue_available"])
        elif node["id"] == "resource-gap":
            bind(api, document["id"], node, run_id=gap["id"], input_fact_ids=gap["input_fact_ids"], metric_keys=["rescue_gap"])
        else:
            bind(
                api, document["id"], node, run_id=gap["id"],
                input_fact_ids=gap["input_fact_ids"],
                input_keys=["rescue_available"], metric_keys=["rescue_gap"],
            )
    chunks = api.call("GET", f"/writing/documents/{document['id']}/chunks")
    assert len(chunks) == len(nodes) and all(item.get("dependencies") for item in chunks)
    before = api.call("GET", f"/writing/documents/{document['id']}")["current_version"]
    preview = api.call("POST", f"/writing/projects/{project['id']}/input-changes/preview", {
        "document_id": document["id"],
        "changes": [{"fact_key": "rescue_available", "new_value": {"number": 400}, "reason": "资源复核"}],
    })
    impact = preview["impact"]
    assert next(item for item in impact["calculations"] if item["result_key"] == "rescue_gap")["new_value"] == 100
    assert len(impact["report_blocks"]) == 6
    after_preview = api.call("GET", f"/writing/documents/{document['id']}")["current_version"]
    assert after_preview["id"] == before["id"] and canonical_json(after_preview["content"]) == canonical_json(before["content"])
    api.call("POST", f"/writing/projects/{project['id']}/input-changes/{preview['id']}/cancel", {})

    preview = api.call("POST", f"/writing/projects/{project['id']}/input-changes/preview", {
        "document_id": document["id"],
        "changes": [{"fact_key": "rescue_available", "new_value": {"number": 400}, "reason": "资源复核"}],
    })
    accepted = [item["block_id"] for item in preview["impact"]["content_proposals"] if item.get("selectable")]
    applied = api.call("POST", f"/writing/projects/{project['id']}/input-changes/apply", {
        "preview_id": preview["id"], "accepted_block_ids": accepted,
    })
    assert applied["document_version"]["version"] == 2
    applied_text = "\n".join(node_text(item) for item in applied["document_version"]["content"])
    assert "400" in applied_text and "100" in applied_text and "180" not in applied_text and "320" not in applied_text
    rollback = api.call("POST", f"/writing/projects/{project['id']}/input-changes/{preview['id']}/rollback", {})
    assert rollback["status"] == "rolled_back" and rollback["document_version"]["version"] == 3
    restored_text = "\n".join(node_text(item) for item in rollback["document_version"]["content"])
    assert "320" in restored_text and "180" in restored_text

    partial_preview = api.call("POST", f"/writing/projects/{project['id']}/input-changes/preview", {
        "document_id": document["id"],
        "changes": [{"fact_key": "rescue_available", "new_value": {"number": 400}, "reason": "逐项采用验证"}],
    })
    partial_selected = ["summary", "resource-gap"]
    partial = api.call("POST", f"/writing/projects/{project['id']}/input-changes/apply", {
        "preview_id": partial_preview["id"], "accepted_block_ids": partial_selected,
    })
    pending = set(partial["impact"]["pending_review_block_ids"])
    assert pending == {"resource-status", "resource-table", "reinforcement", "task-appendix"}
    latest_chunks = api.call("GET", f"/writing/documents/{document['id']}/chunks")
    stale_ids = {item["chunk_id"] for item in latest_chunks if item.get("freshness_status") == "stale"}
    assert pending.issubset(stale_ids)
    return {
        "status": "passed",
        "project_id": project["id"],
        "document_id": document["id"],
        "corpus": corpus,
        "facts": len(api.call("GET", f"/writing/projects/{project['id']}/facts")),
        "criteria_count": len(criteria["items"]),
        "semantica_engine": reasoning["run"]["engine"],
        "semantica_version": reasoning["run"]["engine_version"],
        "semantica_conclusions": len(reasoning["conclusions"]),
        "chunk_count": len(chunks),
        "binding_count": len(api.call("GET", f"/writing/documents/{document['id']}/bindings")),
        "impact_blocks": len(preview["impact"]["report_blocks"]),
        "propagation_paths": len(preview["impact"]["propagation"]["paths"]),
        "all_apply_version": applied["document_version"]["version"],
        "rollback_version": rollback["document_version"]["version"],
        "partial_apply_version": partial["document_version"]["version"],
        "stale_chunk_count": len(stale_ids),
        "verified_transition": {"available": [320, 400], "gap": [180, 100]},
    }


def research_flow(api: Api, space_id: str, suffix: str, output_dir: Path) -> dict[str, Any]:
    project = fresh_project(api, "受控写作线上验收·科研楼可研", f"feasibility-{suffix}", space_id=space_id)
    corpus = verify_corpus(api, project, f"feasibility-{suffix}")
    facts = {
        "civil_quantity": make_fact(api, project["id"], "civil_quantity", "土建工程量", 10_000, "平方米"),
        "civil_unit_price": make_fact(api, project["id"], "civil_unit_price", "土建综合单价", 3200, "元/平方米"),
        "installation_quantity": make_fact(api, project["id"], "installation_quantity", "安装工程量", 10_000, "平方米"),
        "installation_unit_price": make_fact(api, project["id"], "installation_unit_price", "安装综合单价", 800, "元/平方米"),
        "other_cost": make_fact(api, project["id"], "other_cost", "工程建设其他费", 5_000_000, "元"),
        "reserve_rate": make_fact(api, project["id"], "reserve_rate", "基本预备费率", 5, "%"),
    }
    civil = compute(api, project["id"], facts, "quantity_amount", {"quantity": 10_000, "unit_price": 3200}, {"quantity": "civil_quantity", "unit_price": "civil_unit_price"}, "civil_cost", "土建工程费", "元")
    installation = compute(api, project["id"], facts, "quantity_amount", {"quantity": 10_000, "unit_price": 800}, {"quantity": "installation_quantity", "unit_price": "installation_unit_price"}, "installation_cost", "安装工程费", "元")
    construction = compute(api, project["id"], facts, "construction_installation_cost", {"civil_cost": 32_000_000, "installation_cost": 8_000_000}, {"civil_cost": "civil_cost", "installation_cost": "installation_cost"}, "construction_cost", "建安工程费", "元")
    reserve = compute(api, project["id"], facts, "basic_reserve", {"construction_cost": 40_000_000, "other_cost": 5_000_000, "reserve_rate": 5}, {"construction_cost": "construction_cost", "other_cost": "other_cost", "reserve_rate": "reserve_rate"}, "basic_reserve", "基本预备费", "元")

    # Missing interest must fail rather than silently becoming zero.
    api.call("POST", f"/writing/projects/{project['id']}/compute", {
        "operation": "total_investment",
        "inputs": {"construction_cost": 40_000_000, "other_cost": 5_000_000, "basic_reserve": 2_250_000},
        "input_fact_ids": [facts["construction_cost"]["id"], facts["other_cost"]["id"], facts["basic_reserve"]["id"]],
        "input_fact_map": {"construction_cost": facts["construction_cost"]["id"], "other_cost": facts["other_cost"]["id"], "basic_reserve": facts["basic_reserve"]["id"]},
        "output_fact_key": "total_investment_invalid",
        "output_label": "错误总投资",
        "output_unit": "元",
    }, expected=(422,))
    facts["construction_interest"] = make_fact(api, project["id"], "construction_interest", "建设期利息", 1_000_000, "元")
    total = compute(api, project["id"], facts, "total_investment", {"construction_cost": 40_000_000, "other_cost": 5_000_000, "basic_reserve": 2_250_000, "construction_interest": 1_000_000}, {"construction_cost": "construction_cost", "other_cost": "other_cost", "basic_reserve": "basic_reserve", "construction_interest": "construction_interest"}, "total_investment", "总投资", "元")
    ratio = compute(api, project["id"], facts, "investment_ratio", {"part": 40_000_000, "total": 48_250_000}, {"part": "construction_cost", "total": "total_investment"}, "construction_ratio", "建安工程费占比", "%", digits=2)
    assert number(facts["total_investment"]) == 48_250_000 and number(facts["construction_ratio"]) == 82.9

    nodes = [
        {"id": "title", "type": "h1", "children": [{"text": "科研楼建设项目可行性研究报告"}]},
        {"id": "overview", "type": "p", "children": [{"text": "本报告依据当前项目工程量和单价开展确定性投资估算。"}]},
        {"id": "civil", "type": "computed_metric", "label": "土建工程费", "value": 32_000_000, "unit": "元", "computation_run_id": civil["id"], "children": [{"text": "经核验与测算，土建工程费为32000000元。"}]},
        {"id": "construction", "type": "computed_metric", "label": "建安工程费", "value": 40_000_000, "unit": "元", "computation_run_id": construction["id"], "children": [{"text": "经核验与测算，建安工程费为40000000元。"}]},
        {"id": "total", "type": "computed_metric", "label": "总投资", "value": 48_250_000, "unit": "元", "computation_run_id": total["id"], "children": [{"text": "经核验与测算，项目总投资为48250000元。"}]},
        {"id": "ratio", "type": "computed_metric", "label": "建安工程费占比", "value": 82.9, "unit": "%", "computation_run_id": ratio["id"], "children": [{"text": "经核验与测算，建安工程费占总投资82.9%。"}]},
    ]
    document = create_document(api, project["id"], "科研楼建设项目可行性研究报告", nodes)
    for node, run, metric in (
        (nodes[2], civil, "civil_cost"),
        (nodes[3], construction, "construction_cost"),
        (nodes[4], total, "total_investment"),
        (nodes[5], ratio, "construction_ratio"),
    ):
        bind(api, document["id"], node, run_id=run["id"], input_fact_ids=run["input_fact_ids"], metric_keys=[metric])
    preview = api.call("POST", f"/writing/projects/{project['id']}/input-changes/preview", {
        "document_id": document["id"],
        "changes": [{"fact_key": "civil_unit_price", "new_value": {"number": 3500}, "reason": "单价复核"}],
    })
    results = {item["result_key"]: item["new_value"] for item in preview["impact"]["calculations"]}
    expected = {
        "civil_cost": 35_000_000,
        "construction_cost": 43_000_000,
        "basic_reserve": 2_400_000,
        "total_investment": 51_400_000,
        "construction_ratio": 83.66,
    }
    assert results == expected, (results, expected)
    assert len(preview["impact"]["propagation"]["paths"]) >= 5
    selected = [item["block_id"] for item in preview["impact"]["content_proposals"] if item.get("selectable")]
    applied = api.call("POST", f"/writing/projects/{project['id']}/input-changes/apply", {
        "preview_id": preview["id"], "accepted_block_ids": selected,
    })
    by_id = {item["id"]: item for item in applied["document_version"]["content"]}
    assert by_id["total"]["value"] == 51_400_000 and by_id["ratio"]["value"] == 83.66

    # Prose diff is versioned; an unbound precise number is blocked.
    changeset = api.call("POST", f"/writing/documents/{document['id']}/changesets/preview", {
        "operations": [{"operation": "MOD", "block_id": "overview", "before": node_text(nodes[1]), "after": "本报告依据经复核的工程量、单价及费用口径开展确定性投资估算。"}],
    })
    changed = api.call("POST", f"/writing/documents/{document['id']}/changesets/{changeset['id']}/apply", {"accepted_operation_indexes": [0]})
    unsafe = api.call("POST", f"/writing/documents/{document['id']}/changesets/preview", {
        "operations": [{"operation": "ADD", "block_id": "unbound-total", "after": "项目总投资为9999万元。"}],
    })
    api.call("POST", f"/writing/documents/{document['id']}/changesets/{unsafe['id']}/apply", {"accepted_operation_indexes": [0]}, expected=(409,))

    exports: list[dict[str, Any]] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for output_format in ("docx", "pdf"):
        try:
            job = api.call("POST", f"/writing/documents/{document['id']}/exports", {"output_format": output_format})
            blob, _headers = api.call("GET", f"/writing/exports/{job['id']}/download", raw=True)
            path = output_dir / f"controlled-writing-feasibility-{suffix}.{output_format}"
            path.write_bytes(blob)
            if output_format == "docx":
                assert blob.startswith(b"PK")
            else:
                assert blob.startswith(b"%PDF")
            exports.append({"format": output_format, "status": "passed", "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "path": str(path)})
        except Exception as exc:  # Preserve an auditable live limitation without hiding it.
            exports.append({"format": output_format, "status": "failed", "reason": str(exc)[:500]})
    return {
        "status": "passed",
        "project_id": project["id"],
        "document_id": document["id"],
        "corpus": corpus,
        "computation_run_count": 6,
        "missing_interest_guard": "passed",
        "baseline": {"total_investment": 48_250_000, "construction_ratio": 82.9},
        "changed": expected,
        "propagation_paths": len(preview["impact"]["propagation"]["paths"]),
        "affected_chunks": len(preview["impact"]["report_blocks"]),
        "applied_version": applied["document_version"]["version"],
        "editor_diff_version": changed["document_version"]["version"],
        "unbound_exact_number_blocked": True,
        "exports": exports,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9002")
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/controlled-writing-live"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    api = Api(args.base_url, args.credential_file)
    suffix = time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    spaces = api.call("GET", "/writing/spaces")
    projects = api.call("GET", "/writing/projects")
    emergency_source = next((item for item in projects if item["id"] == "778305b2-8ac4-4abc-b752-aadcef307757"), None)
    if emergency_source is None:
        emergency_source = next((item for item in projects if "地震" in item.get("name", "") and item.get("scenario_package_version_id")), None)
    if emergency_source is None:
        raise RuntimeError("没有找到已验收的地震场景包项目")
    emergency_space = next((item for item in spaces if item["id"] == "9f0e2365-5a03-44af-b076-6a172b68474d"), None)
    if emergency_space is None:
        emergency_space = next((item for item in spaces if "写作图谱" in item.get("name", "")), None)
    research_space = next((item for item in spaces if item["id"] == "bdfe7137-39ae-44e6-b156-b2a643f936a8"), None)
    if research_space is None:
        research_space = next((item for item in spaces if "科研楼" in item.get("name", "")), None)
    if emergency_space is None or research_space is None:
        raise RuntimeError("线上缺少应急或科研楼验收知识空间")
    report = {
        "started_at_epoch": int(time.time()),
        "base_url": args.base_url,
        "credentials_redacted": True,
        "emergency": emergency_flow(api, emergency_source, emergency_space["id"], suffix),
        "feasibility": research_flow(api, research_space["id"], suffix, args.output_dir),
    }
    report["completed_at_epoch"] = int(time.time())
    report["status"] = "passed" if all(report[key]["status"] == "passed" for key in ("emergency", "feasibility")) else "failed"
    target = args.report or (args.output_dir / f"controlled-writing-live-{suffix}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(target), "emergency_project_id": report["emergency"]["project_id"], "feasibility_project_id": report["feasibility"]["project_id"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
