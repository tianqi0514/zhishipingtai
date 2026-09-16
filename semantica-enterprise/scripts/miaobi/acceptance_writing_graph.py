"""Live acceptance for governed writing graph -> Miaobi fact dependencies.

All fixture files are synthetic and explicitly marked as test data.  The
script talks only to the public FastAPI surface; it never imports customer
attachments or reaches middleware/database services directly.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.miaobi.demo_client import DemoClient


FIXTURE_DIR = ROOT / "demo/miaobi/writing-graph-validation"
GROUND_TRUTH = json.loads((FIXTURE_DIR / "ground_truth.json").read_text(encoding="utf-8"))
SPACE_CODE = "writing-graph-validation"
PROJECT_CODE = "writing-graph-miaobi-validation"
ARTICLE_PROJECT_CODE = "writing-graph-miaobi-article-validation"
SCENARIO_CODE = "writing-graph-response-plan-validation"
ARTICLE_TITLE = GROUND_TRUTH["article"]["title"]
FILES = (
    ("01-测试地区地震灾情简报.md", "task_data"),
    ("02-测试地区资源核验台账.md", "task_data"),
    ("03-测试地区处置职责与行动依据.md", "policy_basis"),
)
INPUT_SPECS = {
    "rescue_required": ("搜救人员需求", 500, "人"),
    "rescue_available": ("可用搜救人员", 320, "人"),
    "trauma_beds_required": ("创伤床位需求", 330, "张"),
    "county_trauma_beds": ("县域可用床位", 110, "张"),
    "callable_trauma_beds": ("可调用床位", 250, "张"),
    "tents_required": ("帐篷需求", 7000, "顶"),
    "tents_available": ("可用帐篷", 5200, "顶"),
}

RELEASE_INPUT_SPECS = {
    "event_name": {"label": "事件名称", "value": "测试地区 6.2 级地震应急处置演练", "unit": None},
    "magnitude": {"label": "地震震级", "value": 6.2, "unit": "级"},
    **{
        key: {"label": label, "value": value, "unit": unit}
        for key, (label, value, unit) in INPUT_SPECS.items()
    },
}

ARTICLE_CHAPTERS = (
    ("compilation", "一、编制说明", "说明本文用途、资料边界、适用范围和测试稿属性，不虚构文号或签发信息。", ["event_name"], []),
    ("situation", "二、事件基本情况", "写明事件名称、时间范围、地区和震级；未知灾情必须标记待核实。", ["event_name", "magnitude"], []),
    ("objectives", "三、处置目标", "形成以人员安全、医疗救治、群众安置和基础保障为重点的可核验目标。", ["event_name"], []),
    ("command", "四、组织指挥", "仅依据当前材料说明指挥部和三个业务部门的已确认职责，不套用其他地区机构。", ["event_name"], []),
    ("response", "五、响应行动", "按照先期核验、任务协调、动态续报的顺序组织行动；响应等级保留为待有权人员确认。", ["event_name", "magnitude"], []),
    ("rescue", "六、抢险救援", "说明搜救人员需求、可用力量、缺口和增援建议；精确数字必须绑定事实或计算。", ["rescue_required", "rescue_available"], ["rescue_gap"]),
    ("medical", "七、医疗救治", "说明创伤床位需求、县域容量、可调用床位和两种统计口径，不得静默消解来源冲突。", ["trauma_beds_required", "county_trauma_beds", "callable_trauma_beds"], ["county_bed_gap", "all_area_bed_gap"]),
    ("shelter", "八、人员安置", "说明帐篷需求、可用数量、缺口及安置保障待办。", ["tents_required", "tents_available"], ["tents_gap"]),
    ("materials", "九、物资保障", "围绕已确认帐篷数据形成调拨、核验和登记要求，不补造其他库存。", ["tents_required", "tents_available"], ["tents_gap"]),
    ("transport", "十、交通和通信保障", "依据交通保障组职责形成通道核查和信息联络安排；无依据的路线和时长不得写入。", ["event_name"], []),
    ("secondary", "十一、次生灾害防范", "提出持续监测和风险报告要求；未提供的风险类型仅列为待核实事项。", ["event_name"], []),
    ("reporting", "十二、信息报告", "说明信息汇总、续报、核验和授权发布边界。", ["event_name"], []),
    ("gaps", "十三、资源需求和缺口", "集中呈现搜救、医疗和帐篷的输入、公式、结果与口径，并形成可执行补充建议。", ["rescue_required", "rescue_available", "trauma_beds_required", "county_trauma_beds", "callable_trauma_beds", "tents_required", "tents_available"], ["rescue_gap", "county_bed_gap", "all_area_bed_gap", "tents_gap"]),
    ("requirements", "十四、工作要求", "说明人工确认、动态核验、执行反馈和变更留痕要求。", ["event_name"], []),
    ("appendix", "十五、附件或任务清单", "以表格或清单列出责任单位、任务、已知资源缺口、确认状态和后续动作。", ["rescue_required", "rescue_available", "tents_required", "tents_available"], ["rescue_gap", "tents_gap"]),
)


def emit(stage: str, payload: Any) -> None:
    print(json.dumps({"stage": stage, "result": payload}, ensure_ascii=False, indent=2), flush=True)


def space(api: DemoClient) -> dict[str, Any]:
    row = api.one("/spaces", "code", SPACE_CODE)
    if not row:
        row = api.post("/spaces", {
            "code": SPACE_CODE,
            "name": "写作图谱与妙笔联动验收空间",
            "description": "测试数据：验证五层写作知识、治理、发布、文章绑定和事实联动。",
        })
    return row


def upload(api: DemoClient) -> dict[str, Any]:
    current_space = space(api)
    existing = {row["title"]: row for row in api.get("/documents", space_id=current_space["id"])}
    results: list[dict[str, Any]] = []
    for filename, role in FILES:
        if filename in existing:
            document = existing[filename]
            version_id = document.get("current_version_id")
            latest = next((
                row for row in api.get("/jobs")
                if row.get("job_type") == "process_knowledge"
                and (row.get("input") or {}).get("version_id") == version_id
            ), None)
            retried = False
            if latest and latest.get("status") in {"failed", "partial_failed"}:
                latest = api.post(f"/jobs/{latest['id']}/retry")
                latest = api.wait_job(latest["id"])
                retried = True
            results.append({
                "filename": filename,
                "document_id": document["id"],
                "version_id": version_id,
                "reused": True,
                "retried": retried,
                "job_status": latest.get("status") if latest else None,
            })
            continue
        result = api.upload_file(
            current_space["id"],
            FIXTURE_DIR / filename,
            mode="both",
            targets=["fulltext", "vector", "graph", "writing_graph"],
            material_role=role,
        )
        results.append({
            "filename": filename,
            "document_id": result["document"]["id"],
            "version_id": result["version"]["id"],
            "reused": False,
        })
    payload = {
        "space_id": current_space["id"],
        "files": results,
        "governance": api.get("/writing-graph/governance/summary", space_id=current_space["id"]),
    }
    emit("upload", payload)
    return payload


def inspect(api: DemoClient) -> dict[str, Any]:
    current_space = space(api)
    types: dict[str, Any] = {}
    for target_type in ("evidence", "entity", "claim", "fact", "relation"):
        data = api.get(
            "/writing-graph/governance/items",
            space_id=current_space["id"], target_type=target_type, limit=500,
        )
        types[target_type] = {"total": data["total"], "items": data["items"]}
    payload = {
        "space_id": current_space["id"],
        "summary": api.get("/writing-graph/governance/summary", space_id=current_space["id"]),
        "types": types,
    }
    emit("inspect", payload)
    return payload


def govern(api: DemoClient) -> dict[str, Any]:
    """Apply an auditable test-review policy, never blind bulk acceptance."""

    current_space = space(api)
    accepted: dict[str, list[str]] = {key: [] for key in ("entity", "claim", "fact", "relation")}
    deferred: dict[str, list[dict[str, Any]]] = {key: [] for key in accepted}
    for target_type in ("entity", "claim", "fact"):
        rows = api.get(
            "/writing-graph/governance/items", space_id=current_space["id"],
            target_type=target_type, limit=500,
        )["items"]
        for row in rows:
            if row.get("verification_status") != "candidate":
                if row.get("verification_status") in {"conflicted", "stale"}:
                    deferred[target_type].append({"id": row["id"], "status": row["verification_status"]})
                continue
            evidence_ids = list(row.get("evidence_ids") or [])
            confidence = float(row.get("confidence") or 1)
            if not evidence_ids or confidence < 0.8:
                deferred[target_type].append({
                    "id": row["id"], "reason": "缺少来源或置信度低于验收阈值",
                })
                continue
            try:
                api.post(
                    f"/writing-graph/governance/items/{target_type}/{row['id']}/decide",
                    {"action": "accept", "reason": "验收人员逐项核对原文、结构化值和来源定位后确认", "changes": {}},
                )
                accepted[target_type].append(row["id"])
            except RuntimeError as exc:
                deferred[target_type].append({"id": row["id"], "reason": str(exc)})

    # Relations require verified endpoint entities and their projected Fact.
    rows = api.get(
        "/writing-graph/governance/items", space_id=current_space["id"],
        target_type="relation", limit=500,
    )["items"]
    for row in rows:
        if row.get("verification_status") != "candidate":
            if row.get("verification_status") in {"conflicted", "stale"}:
                deferred["relation"].append({"id": row["id"], "status": row["verification_status"]})
            continue
        try:
            api.post(
                f"/writing-graph/governance/items/relation/{row['id']}/decide",
                {"action": "accept", "reason": "验收人员核对关系两端、关系事实和原文依据后确认", "changes": {}},
            )
            accepted["relation"].append(row["id"])
        except RuntimeError as exc:
            deferred["relation"].append({"id": row["id"], "reason": str(exc)})
    payload = {
        "accepted": {key: len(value) for key, value in accepted.items()},
        "deferred": deferred,
        "summary": api.get("/writing-graph/governance/summary", space_id=current_space["id"]),
    }
    emit("govern", payload)
    return payload


def publish(api: DemoClient) -> dict[str, Any]:
    current_space = space(api)
    release = api.post("/writing-graph/releases", {"space_id": current_space["id"]})
    business = api.get(f"/writing-graph/releases/{release['id']}/graph", view="business")
    evidence = api.get(f"/writing-graph/releases/{release['id']}/graph", view="evidence")
    payload = {
        "release_id": release["id"],
        "release_number": release["release_number"],
        "checksum": release["checksum"],
        "counts": release["counts"],
        "business_nodes": len(business["nodes"]),
        "business_edges": len(business["edges"]),
        "evidence_nodes": len(evidence["nodes"]),
        "evidence_edges": len(evidence["edges"]),
    }
    emit("publish", payload)
    return release


def numeric_value(snapshot: dict[str, Any]) -> float | None:
    raw = (snapshot.get("object_value") or {}).get("value")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def release_value(snapshot: dict[str, Any]) -> Any:
    return (snapshot.get("object_value") or {}).get("value")


def map_release_inputs(release: dict[str, Any]) -> list[dict[str, str]]:
    facts = release["items"]["fact"]
    adopted: list[dict[str, str]] = []
    used: set[str] = set()
    for fact_key, spec in RELEASE_INPUT_SPECS.items():
        label, expected, unit = spec["label"], spec["value"], spec["unit"]

        def value_matches(item: dict[str, Any]) -> bool:
            actual = release_value(item)
            if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                return (
                    isinstance(actual, (int, float))
                    and not isinstance(actual, bool)
                    and math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=1e-9)
                )
            return str(actual or "").strip() == str(expected).strip()

        candidates = [
            item for item in facts
            if item.get("verification_status") == "verified"
            and item.get("id") not in used
            and value_matches(item)
            and (not item.get("unit") or item.get("unit") == unit)
        ]
        candidates.sort(key=lambda item: (
            0 if label in str(item.get("predicate") or "") else 1,
            0 if item.get("origin_type") == "metric_mention" else 1,
            item["id"],
        ))
        if not candidates or label not in str(candidates[0].get("predicate") or ""):
            raise RuntimeError(f"写作图谱中没有找到已确认输入：{fact_key} / {label}={expected}{unit}")
        selected = candidates[0]
        used.add(selected["id"])
        adopted.append({"fact_id": selected["id"], "fact_key": fact_key, "label": label})
    return adopted


def ensure_article_scenario(api: DemoClient) -> dict[str, Any]:
    package = api.one("/writing/scenario-packages", "code", SCENARIO_CODE)
    if package:
        detail = api.get(f"/writing/scenario-packages/{package['id']}")
        current = next(
            (row for row in detail.get("versions") or [] if row["id"] == detail.get("current_version_id")),
            None,
        )
        if current and len((current.get("chapter_template") or {}).get("chapters") or []) == len(ARTICLE_CHAPTERS):
            return current
    else:
        package = api.post("/writing/scenario-packages", {
            "code": SCENARIO_CODE,
            "name": "写作图谱地震处置方案验收模板",
            "disaster_type": "earthquake",
            "description": "测试场景：用已治理写作图谱、确定性计算和 15 章文章验证端到端写作与事实联动。",
        })

    properties: dict[str, Any] = {
        "event_name": {"type": "string", "title": "事件名称", "confirmation_required": True},
        "magnitude": {"type": "number", "title": "地震震级", "minimum": 0, "maximum": 10, "unit": "级", "confirmation_required": True},
    }
    for key, (label, _value, unit) in INPUT_SPECS.items():
        properties[key] = {
            "type": "integer", "title": label, "minimum": 0, "unit": unit,
            "confirmation_required": True,
        }
    chapters = [
        {
            "key": key,
            "title": title,
            "instruction": instruction,
            "generation_mode": "agent",
            "required_inputs": required_inputs,
            "toolbox_outputs": outputs,
            "citation_required": True,
        }
        for key, title, instruction, required_inputs, outputs in ARTICLE_CHAPTERS
    ]
    return api.post(f"/writing/scenario-packages/{package['id']}/versions", {
        "input_schema": {
            "type": "object",
            "required": list(RELEASE_INPUT_SPECS),
            "properties": properties,
        },
        "ontology_mapping": {},
        "rule_set_ids": [],
        "formula_ids": ["resource-gap"],
        "tool_ids": [],
        "chapter_template": {"chapters": chapters},
        "output_schema": {
            "type": "object",
            "required": ["title", "chapters", "evidence_manifest"],
            "title_pattern": "{project_name}",
            "allowed_formats": ["docx", "pdf"],
        },
        "review_rules": {
            "missing_input_action": "block",
            "unverified_fact_action": "block",
            "require_citations": True,
            "allow_manual_override": True,
            "reject_stale_blocks": True,
        },
        "decision_gates": [{"key": "publish", "name": "正式定稿确认", "required": False}],
        "comparison_dimensions": [],
        "config": {
            "minimum_plan_count": 2,
            "default_plan_count": 3,
            "business_validation": "accepted",
            "toolbox": {
                "reasoning_enabled": False,
                "calculation_enabled": True,
                "target_sections": {
                    "rescue_gap": ["gaps"],
                    "county_bed_gap": ["medical"],
                    "all_area_bed_gap": ["medical"],
                    "tents_gap": ["gaps"],
                },
            },
            "writing_policy": {
                "missing_input_action": "block",
                "unverified_fact_action": "block",
                "require_citations": True,
                "allow_manual_override": True,
            },
            "output": {"allowed_formats": ["docx", "pdf"]},
        },
        "activate": True,
    })


def ensure_project_materials(api: DemoClient, project_id: str, space_id: str) -> None:
    linked = {row["version_id"] for row in api.get(f"/writing/projects/{project_id}/materials")}
    roles = {filename: role for filename, role in FILES}
    for document in api.get("/documents", space_id=space_id):
        if document.get("title") not in roles or document.get("current_version_id") in linked:
            continue
        api.post(f"/writing/projects/{project_id}/materials", {
            "document_id": document["id"],
            "version_id": document["current_version_id"],
            "material_role": roles[document["title"]],
            "usage_scope": "task_only",
        })


def ensure_public_references(api: DemoClient, project_id: str) -> list[dict[str, Any]]:
    rows = api.get(f"/writing/projects/{project_id}/public-references")
    by_url = {row["url"]: row for row in rows}
    references = (
        {
            "title": "甘肃省地震应急预案（2026年版）",
            "publisher": "甘肃省人民政府办公厅",
            "url": "https://zwfw.gansu.gov.cn/jingning/zczx/zcwj/art/2026/art_a7ce570bd1fb46bfa4ddff7bbcfedc70.html",
            "publication_date": "2026-02-28",
            "excerpt": "公开预案采用总则、组织体系、监测报告、应急响应、恢复重建、保障措施和附则等结构；仅用于文章结构和通用处置环节参考，不作为测试地区职责或灾情事实。",
        },
        {
            "title": "云南省地震应急预案（2025年修订版）",
            "publisher": "云南省人民政府办公厅",
            "url": "https://yjglt.yn.gov.cn/html/2026/qtwj_0107/4032338.html",
            "publication_date": "2025-12-31",
            "excerpt": "公开预案包含总则、组织指挥体系、分级应对、应急响应和保障等内容；仅用于结构和正式表达参考，不把云南机构、阈值和职责套用到测试地区。",
        },
    )
    for item in references:
        if item["url"] in by_url:
            continue
        by_url[item["url"]] = api.post(f"/writing/projects/{project_id}/public-references", {
            **item,
            "applicable_scope": {"usage": "structure_and_style_only", "exclude_as_project_fact": True},
            "validity_status": "current",
            "usage_sections": [key for key, *_ in ARTICLE_CHAPTERS],
        })
    return list(by_url.values())


def project(api: DemoClient) -> dict[str, Any]:
    current_space = space(api)
    releases = api.get("/writing-graph/releases", space_id=current_space["id"])
    if not releases:
        raise RuntimeError("请先发布写作图谱")
    release = api.get(f"/writing-graph/releases/{releases[0]['id']}")
    current_project = api.one("/writing/projects", "code", PROJECT_CODE)
    if not current_project:
        scenarios = api.get("/writing/scenario-packages")
        scenario = next(
            (item for item in scenarios if item.get("status") == "active" and item.get("disaster_type") == "earthquake"),
            None,
        )
        current_project = api.post("/writing/projects", {
            "code": PROJECT_CODE,
            "name": "写作图谱与事实联动验收项目（测试数据）",
            "space_id": current_space["id"],
            "writing_graph_release_id": release["id"],
            "scenario_package_version_id": scenario.get("current_version_id") if scenario else None,
            "config": {"disclaimer": GROUND_TRUTH["disclaimer"]},
        })
    facts = api.get(f"/writing/projects/{current_project['id']}/facts")
    if not all(any(row["fact_key"] == key and row["active"] for row in facts) for key in INPUT_SPECS):
        adoption = api.post(
            f"/writing/projects/{current_project['id']}/facts/adopt-writing-graph",
            {"items": map_release_inputs(release)},
        )
    else:
        adoption = {"reused": [row for row in facts if row["fact_key"] in INPUT_SPECS], "adopted": []}
    baseline = api.post(f"/writing/projects/{current_project['id']}/computations/run-baseline", {})
    results = {
        item["fact"]["fact_key"]: (item["fact"]["value"] or {}).get("number")
        for item in baseline["items"]
    }
    if results.get("rescue_gap") != 180:
        raise RuntimeError(f"确定性公式结果错误：rescue_gap={results.get('rescue_gap')}")
    payload = {
        "project_id": current_project["id"], "release_id": release["id"],
        "adopted": len(adoption.get("adopted") or []), "reused": len(adoption.get("reused") or []),
        "computed": results,
    }
    emit("project", payload)
    return {"project": current_project, "release": release, "baseline": baseline}


def article_setup(api: DemoClient) -> dict[str, Any]:
    current_space = space(api)
    releases = api.get("/writing-graph/releases", space_id=current_space["id"])
    if not releases:
        raise RuntimeError("请先发布写作图谱")
    release = api.get(f"/writing-graph/releases/{releases[0]['id']}")
    scenario = ensure_article_scenario(api)
    current_project = api.one("/writing/projects", "code", ARTICLE_PROJECT_CODE)
    if not current_project:
        current_project = api.post("/writing/projects", {
            "code": ARTICLE_PROJECT_CODE,
            "name": "测试地区地震应急处置方案写作验收项目",
            "space_id": current_space["id"],
            "writing_graph_release_id": release["id"],
            "scenario_package_version_id": scenario["id"],
            "config": {"disclaimer": GROUND_TRUTH["disclaimer"], "acceptance": "writing_graph_article"},
        })
    if current_project.get("scenario_package_version_id") != scenario["id"]:
        raise RuntimeError("同名验收项目已绑定旧模板，请使用新的验收项目编码，不能静默更换历史契约")
    ensure_project_materials(api, current_project["id"], current_space["id"])
    references = ensure_public_references(api, current_project["id"])
    facts = api.get(f"/writing/projects/{current_project['id']}/facts")
    active_keys = {row["fact_key"] for row in facts if row.get("active")}
    if not set(RELEASE_INPUT_SPECS).issubset(active_keys):
        adoption = api.post(
            f"/writing/projects/{current_project['id']}/facts/adopt-writing-graph",
            {"items": map_release_inputs(release)},
        )
    else:
        adoption = {"reused": [row for row in facts if row.get("active") and row["fact_key"] in RELEASE_INPUT_SPECS], "adopted": []}
    baseline = api.post(f"/writing/projects/{current_project['id']}/computations/run-baseline", {})
    computed = {
        item["fact"]["fact_key"]: (item["fact"]["value"] or {}).get("number")
        for item in baseline["items"]
    }
    expected = {"rescue_gap": 180, "county_bed_gap": 220, "all_area_bed_gap": 80, "tents_gap": 1800}
    if computed != expected:
        raise RuntimeError(f"确定性公式结果与验收口径不一致：{computed}")
    documents = api.get(f"/writing/projects/{current_project['id']}/documents")
    document = next((row for row in documents if row.get("title") == ARTICLE_TITLE), None)
    if not document:
        document = api.post("/writing/documents", {
            "project_id": current_project["id"],
            "title": ARTICLE_TITLE,
            "document_type": GROUND_TRUTH["article"]["document_type"],
            "purpose": GROUND_TRUTH["article"]["purpose"],
            "audience": GROUND_TRUTH["article"]["audience"],
            "applicability": {
                **GROUND_TRUTH["article"]["applicability"],
                "test_data": True,
                "source_policy": "pinned_writing_graph_and_project_materials",
            },
            "writing_requirements": (
                "形成正式、清楚、适合指挥部审阅的测试稿。精确数字只使用已确认事实或确定性计算；"
                "职责只使用本项目材料；公开预案仅参考结构和通用表达；未知信息标记待核实；"
                "不得虚构文号、机构、响应决定、路线、时限和库存。"
            ),
            "scenario_package_version_id": scenario["id"],
            "knowledge_product_release_id": current_project["knowledge_product_release_id"],
            "writing_graph_release_id": release["id"],
            "content": [],
        })
    payload = {
        "project_id": current_project["id"],
        "document_id": document["id"],
        "scenario_version_id": scenario["id"],
        "writing_graph_release_id": release["id"],
        "chapter_count": len(ARTICLE_CHAPTERS),
        "required_inputs": len(RELEASE_INPUT_SPECS),
        "adopted": len(adoption.get("adopted") or []),
        "reused": len(adoption.get("reused") or []),
        "computed": computed,
        "public_references": len(references),
        "materials": len(api.get(f"/writing/projects/{current_project['id']}/materials")),
    }
    emit("article_setup", payload)
    return {"project": current_project, "document": document, "scenario": scenario, "release": release, "baseline": baseline}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["upload", "inspect", "govern", "publish", "project", "article", "all"])
    args = parser.parse_args()
    api = DemoClient()
    if args.stage in {"upload", "all"}:
        upload(api)
    if args.stage in {"inspect", "all"}:
        inspect(api)
    if args.stage in {"govern", "all"}:
        govern(api)
    if args.stage in {"publish", "all"}:
        publish(api)
    if args.stage in {"project", "all"}:
        project(api)
    if args.stage in {"article", "all"}:
        article_setup(api)


if __name__ == "__main__":
    main()
