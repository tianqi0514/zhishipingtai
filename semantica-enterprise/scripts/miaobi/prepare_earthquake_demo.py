from __future__ import annotations

import json
import hashlib
from pathlib import Path

from demo_client import (
    APPLICATION_CODE,
    CAPABILITY_SCENARIO_CODE,
    DemoClient,
    PRODUCT_CODE,
    PROJECT_CODE,
    SCENARIO_CODE,
    SPACE_CODE,
)


ROOT = Path(__file__).resolve().parents[2]
GROUND_TRUTH = json.loads((ROOT / "demo/miaobi/earthquake_ground_truth.json").read_text(encoding="utf-8"))
MATERIALS = ROOT / "demo/miaobi/materials"

LABELS = {
    "event_name": "事件名称",
    "magnitude": "震级",
    "population_density": "人口密度",
    "rescue_required": "搜救人员需求",
    "rescue_available": "可用搜救人员",
    "trauma_beds_required": "创伤床位需求",
    "county_trauma_beds": "县域可用床位",
    "callable_trauma_beds": "可调用床位",
    "tents_required": "帐篷需求",
    "tents_available": "可用帐篷",
}

PLAN_INPUTS = {
    "route_start": "州级应急物资库",
    "route_target": "积石山震中安置点",
    "route_graph": {
        "州级应急物资库": [
            {"to": "快速通道", "minutes": 10, "risk": 7},
            {"to": "安全通道", "minutes": 24, "risk": 1},
            {"to": "综合通道", "minutes": 17.5, "risk": 3},
        ],
        "快速通道": [{"to": "积石山震中安置点", "minutes": 10, "risk": 7}],
        "安全通道": [{"to": "积石山震中安置点", "minutes": 24, "risk": 1}],
        "综合通道": [{"to": "积石山震中安置点", "minutes": 17.5, "risk": 3}],
    },
    "coordinates": {
        "州级应急物资库": [103.21, 35.60],
        "快速通道": [103.31, 35.62],
        "安全通道": [103.22, 35.68],
        "综合通道": [103.27, 35.65],
        "积石山震中安置点": [103.40, 35.66],
    },
}


def ensure_product_release(api: DemoClient, space: dict, product: dict) -> dict:
    product = api.put(
        f"/knowledge-products/{product['id']}",
        {"status": "active", "enabled": True, "space_ids": [space["id"]]},
    )
    knowledge = api.get("/knowledge/releases", space_id=space["id"])["knowledge"]
    if not knowledge:
        raise RuntimeError("演示知识空间尚无已发布知识版本，请先完成至少一份材料的知识加工")
    current_knowledge_id = knowledge[0]["id"]
    releases = api.get(f"/knowledge-products/{product['id']}/releases")
    for release in releases:
        if release["status"] == "published" and any(
            item["knowledge_release_id"] == current_knowledge_id for item in release.get("items", [])
        ):
            return release
    return api.post(f"/knowledge-products/{product['id']}/releases", {"note": "妙笔地震场景确定性验收版本"})


def ensure_materials(api: DemoClient, space: dict) -> list[dict]:
    existing: dict[str, list[dict]] = {}
    for row in api.get("/documents", space_id=space["id"]):
        existing.setdefault(row["title"], []).append(row)
    uploaded: list[dict] = []
    for path in sorted(MATERIALS.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        candidates = existing.get(path.name, [])
        expected_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        details = [(row, api.get(f"/documents/{row['id']}")) for row in candidates]
        matching = [
            (row, detail)
            for row, detail in details
            if any(version.get("sha256") == expected_sha for version in detail.get("versions", []))
        ]
        row, cached_detail = matching[0] if matching else (None, None)
        removed_duplicates = False
        for duplicate, _ in details:
            if row and duplicate["id"] == row["id"]:
                continue
            # The isolated demo space is owned by this preparation script.
            # Exact-title leftovers from interrupted preparations are retired
            # through the audited public API instead of being hard-deleted.
            api.delete(f"/documents/{duplicate['id']}")
            removed_duplicates = True
        if row:
            detail = cached_detail or api.get(f"/documents/{row['id']}")
            current = next((item for item in detail.get("versions", []) if item["id"] == row.get("current_version_id")), None)
            summary = (current or {}).get("parse_summary") or {}
            if current and current.get("status") == "ready" and summary.get("knowledge_status") == "published":
                completed = set(summary.get("knowledge_targets_completed") or [])
                if {"vector", "graph"}.issubset(completed) and not removed_duplicates:
                    uploaded.append({"document_id": row["id"], "status": "existing"})
                    continue
                # Product releases lock the index and graph projections at one
                # consistency boundary. A previous vector-only preparation is
                # therefore upgraded through the normal public processing API;
                # the worker reuses its existing chunks and vectors.
                force = "?force=true" if removed_duplicates else ""
                knowledge_job = api.post(f"/documents/{row['id']}/process{force}", {"mode": "both"})
                api.wait_job(knowledge_job["id"])
                uploaded.append({
                    "document_id": row["id"],
                    "status": "deduplicated_and_republished" if removed_duplicates else "completed_graph_projection",
                })
                continue
            if current:
                parse_job = next(
                    (
                        job
                        for job in api.get("/jobs")
                        if job.get("job_type") == "parse_document"
                        and (job.get("input") or {}).get("version_id") == current["id"]
                    ),
                    None,
                )
                if parse_job and parse_job.get("status") == "failed":
                    parse_job = api.post(f"/jobs/{parse_job['id']}/retry")
                if parse_job and parse_job.get("status") != "succeeded":
                    api.wait_job(parse_job["id"])
                if parse_job:
                    api.wait_knowledge_job(current["id"])
                    uploaded.append({"document_id": row["id"], "status": "recovered"})
                    continue
        result = api.upload_file(space["id"], path, mode="both")
        uploaded.append({"document_id": result["document"]["id"], "status": "uploaded"})
    if len(uploaded) < 3:
        raise RuntimeError("妙笔地震演示资料不足，至少需要预案、资源清单和调度约束三份材料")
    return uploaded


def ensure_demo_graph(api: DemoClient, space: dict) -> dict:
    """Publish a small evidence-backed graph derived from the checked-in fixtures."""
    documents = {row["title"]: row for row in api.get("/documents", space_id=space["id"])}

    def source_chunk(title: str) -> str:
        document = documents.get(title)
        if not document:
            raise RuntimeError(f"图谱证据文档不存在：{title}")
        detail = api.get(f"/documents/{document['id']}")
        chunks = api.get(f"/versions/{detail['current_version_id']}/chunks", limit=10)["items"]
        if not chunks:
            raise RuntimeError(f"图谱证据文档没有已发布片段：{title}")
        return chunks[0]["id"]

    entity_rows = api.get("/knowledge/entities", space_id=space["id"], limit=500)["items"]

    def ensure_entity(name: str, entity_type: str) -> dict:
        existing = next(
            (row for row in entity_rows if row["canonical_name"] == name and row["entity_type"] == entity_type),
            None,
        )
        if existing:
            return existing
        created = api.post(
            "/knowledge/entities",
            {
                "space_id": space["id"],
                "canonical_name": name,
                "entity_type": entity_type,
                "properties": {"fixture": True, "disclaimer": GROUND_TRUTH["disclaimer"]},
                "confidence": 1,
                "status": "published",
            },
        )
        entity_rows.append(created)
        return created

    entities = {
        "event": ensure_entity("积石山县6.2级地震", "事件"),
        "place": ensure_entity("积石山县", "地点"),
        "command": ensure_entity("应急指挥机构", "组织"),
        "level": ensure_entity("重大地震灾害（Ⅱ级）", "灾害等级"),
        "rescue": ensure_entity("人员搜救", "应急任务"),
        "dispatch": ensure_entity("物资调度", "应急任务"),
        "rescue_resource": ensure_entity("500名搜救人员需求", "资源需求"),
        "tent_resource": ensure_entity("7000顶帐篷需求", "资源需求"),
    }
    resource_title = "积石山县地震灾情与资源清单（验收演示）.yaml"
    policy_title = "地震应急预案摘编（验收演示）.md"
    resource_chunk = source_chunk(resource_title)
    policy_chunk = source_chunk(policy_title)
    specs = [
        ("event", "发生于", "place", resource_chunk),
        ("event", "需要", "rescue_resource", resource_chunk),
        ("event", "需要", "tent_resource", resource_chunk),
        ("event", "满足灾害等级判据", "level", policy_chunk),
        ("command", "负责", "rescue", policy_chunk),
        ("command", "负责", "dispatch", policy_chunk),
    ]
    current = api.get("/knowledge/facts", space_id=space["id"], limit=500)["items"]
    created = 0
    for subject_key, predicate, object_key, chunk_id in specs:
        subject = entities[subject_key]
        obj = entities[object_key]
        if any(
            row.get("subject_entity_id") == subject["id"]
            and row.get("predicate") == predicate
            and row.get("object_entity_id") == obj["id"]
            and row.get("status") == "published"
            for row in current
        ):
            continue
        current.append(
            api.post(
                "/knowledge/facts",
                {
                    "space_id": space["id"],
                    "subject_entity_id": subject["id"],
                    "predicate": predicate,
                    "object_entity_id": obj["id"],
                    "source_chunk_id": chunk_id,
                    "confidence": 1,
                    "status": "published",
                },
            )
        )
        created += 1

    # Manual relation edits generate a graph snapshot. Publish an explicit
    # immutable knowledge boundary without rerunning extraction, which would
    # incorrectly treat manually curated relations as extraction leftovers.
    api.post(f"/knowledge/releases/publish?space_id={space['id']}")
    return {"entities": len(entities), "facts": len(specs), "created": created}


def ensure_application_binding(api: DemoClient, product: dict, release: dict) -> tuple[dict, dict]:
    api.put(
        f"/knowledge-products/{product['id']}/aliases/production",
        {"product_release_id": release["id"], "reason": "妙笔地震应急方案生产知识基线"},
    )
    application = api.one("/applications", "code", APPLICATION_CODE)
    if not application:
        application = api.post(
            "/applications",
            {
                "code": APPLICATION_CODE,
                "name": "妙笔·应急方案生成",
                "description": "基于组织知识、确定性计算和规则推演生成可核验的应急处置方案。",
                "app_type": "web",
                "environment": "production",
                "status": "active",
                "config": {"entry_path": "/miaobi/", "scenario": SCENARIO_CODE},
            },
        )
    elif application["status"] != "active" or not application["enabled"]:
        application = api.put(
            f"/applications/{application['id']}",
            {"status": "active", "enabled": True, "environment": "production"},
        )
    capability = api.one("/application-scenarios", "code", CAPABILITY_SCENARIO_CODE)
    if not capability:
        capability = api.post(
            "/application-scenarios",
            {
                "code": CAPABILITY_SCENARIO_CODE,
                "name": "地震应急处置方案生成",
                "description": "绑定地震场景包、生产知识版本和受控写作工具的应用能力。",
                "scenario_type": "analysis",
                "status": "active",
            },
        )
    versions = api.get(f"/application-scenarios/{capability['id']}/versions")
    if not versions:
        models = [row for row in api.get("/model-configs") if row.get("model_kind") == "llm" and row.get("enabled")]
        default_model = next((row for row in models if row.get("is_default")), models[0] if models else None)
        api.post(
            f"/application-scenarios/{capability['id']}/versions",
            {
                "product_id": product["id"],
                "product_alias": "production",
                "model_config_id": default_model["id"] if default_model else None,
                "tool_whitelist": [
                    "knowledge_search",
                    "knowledge_get_fragment",
                    "knowledge_graph_query",
                    "writing_get_project_context",
                    "writing_generate_section_draft",
                    "writing_bind_evidence",
                    "writing_validate_document",
                    "writing_compare_alternative_plans",
                    "writing_prepare_export",
                ],
                "retrieval_policy": {
                    "top_k": 8,
                    "use_keyword": True,
                    "use_vector": True,
                    "use_graph": True,
                    "use_reranker": False,
                },
                "system_policy": {
                    "scenario_package": SCENARIO_CODE,
                    "authoritative_numbers": "deterministic-only",
                    "require_human_confirmation": True,
                },
                "response_schema": {"type": "writing_document"},
                "citation_policy": {"required": True, "release_locked": True},
                "fallback_policy": {"insufficient_evidence": "disclose"},
                "analysis_rule_set_ids": [],
            },
        )
    grants = api.get(f"/applications/{application['id']}/grants")
    required = {
        ("knowledge_product", product["id"], "read"),
        ("scenario", capability["id"], "invoke"),
    }
    existing = {(row["resource_type"], row["resource_id"], row["permission"]) for row in grants if row["effect"] == "allow"}
    for resource_type, resource_id, permission in sorted(required - existing):
        api.post(
            f"/applications/{application['id']}/grants",
            {"resource_type": resource_type, "resource_id": resource_id, "permission": permission, "effect": "allow"},
        )
    return application, capability


def ensure_facts(api: DemoClient, project: dict) -> list[dict]:
    current = {row["fact_key"]: row for row in api.get(f"/writing/projects/{project['id']}/facts")}
    expected = {
        "event_name": {"value": {"text": "积石山县6.2级地震"}, "unit": None},
        **{
            key: {"value": {"number": item["value"]}, "unit": item.get("unit")}
            for key, item in GROUND_TRUTH["facts"].items()
            if key in LABELS and key != "event_name"
        },
    }
    for key, item in expected.items():
        existing = current.get(key)
        if existing:
            if existing["value"] != item["value"] or existing.get("unit") != item.get("unit"):
                raise RuntimeError(f"演示事实 {key} 已被修改；为保护人工结果，准备脚本不会静默覆盖")
            continue
        current[key] = api.post(
            f"/writing/projects/{project['id']}/facts",
            {
                "fact_key": key,
                "label": LABELS[key],
                "fact_type": "official_brief",
                "value": item["value"],
                "unit": item.get("unit"),
                "source_type": "official_brief",
                "source_id": "miaobi-earthquake-ground-truth",
                "source_version": "v1",
                "source_locator": {"catalog": "客户验收材料", "disclaimer": GROUND_TRUTH["disclaimer"]},
                "confidence": 1,
                "verification_status": "verified",
            },
        )
    return list(current.values())


def main() -> None:
    api = DemoClient()
    space = api.one("/spaces", "code", SPACE_CODE)
    if not space:
        space = api.post(
            "/spaces",
            {
                "code": SPACE_CODE,
                "name": "妙笔·地震应急知识空间",
                "description": "验收演示数据，不代表实时灾情或正式指挥决定。用于验证知识约束写作。",
                "enabled": True,
            },
        )
    materials = ensure_materials(api, space)
    graph = ensure_demo_graph(api, space)
    product = api.one("/knowledge-products", "code", PRODUCT_CODE)
    if not product:
        product = api.post(
            "/knowledge-products",
            {"code": PRODUCT_CODE, "name": "妙笔·地震应急知识产品", "status": "active", "space_ids": [space["id"]]},
        )
    release = ensure_product_release(api, space, product)
    application, capability = ensure_application_binding(api, product, release)
    scenario = api.one("/writing/scenario-packages", "code", SCENARIO_CODE)
    if not scenario or not scenario.get("current_version_id"):
        raise RuntimeError("地震场景包尚未激活")
    project = api.one("/writing/projects", "code", PROJECT_CODE)
    if not project:
        project = api.post(
            "/writing/projects",
            {
                "code": PROJECT_CODE,
                "name": "积石山县6.2级地震应急处置方案（验收演示）",
                "application_id": application["id"],
                "scenario_package_version_id": scenario["current_version_id"],
                "knowledge_product_release_id": release["id"],
                "config": {
                    "disclaimer": "验收演示数据，不代表实时灾情或正式指挥决定。",
                    "plan_inputs": PLAN_INPUTS,
                },
            },
        )
    elif project["knowledge_product_release_id"] != release["id"]:
        project = api.post(
            f"/writing/projects/{project['id']}/knowledge-release",
            {
                "knowledge_product_release_id": release["id"],
                "reason": "切换到地震应急专属、不可变知识产品版本",
            },
        )
    ensure_facts(api, project)
    current = {row["fact_key"]: row for row in api.get(f"/writing/projects/{project['id']}/facts")}
    if not {"criterion_major_magnitude", "criterion_high_population_density"}.issubset(current):
        api.post(f"/writing/projects/{project['id']}/criteria/evaluate")
    if not api.get(f"/writing/projects/{project['id']}/reasoning-runs"):
        api.post(f"/writing/projects/{project['id']}/reason", {"mode": "preview"})
    current = {row["fact_key"]: row for row in api.get(f"/writing/projects/{project['id']}/facts")}
    if not {"rescue_gap", "county_bed_gap", "all_area_bed_gap", "tents_gap"}.issubset(current):
        api.post(f"/writing/projects/{project['id']}/computations/run-baseline")
    plans = api.get(f"/writing/projects/{project['id']}/plans")
    if len(plans) < 3:
        plans = api.post(f"/writing/projects/{project['id']}/plans/generate", {"count": 3})
    documents = api.get(f"/writing/projects/{project['id']}/documents")
    if not documents:
        documents = [api.post(
            "/writing/documents",
            {
                "project_id": project["id"],
                "title": "积石山县6.2级地震应急处置方案",
                "content": [
                    {"id": "demo-title", "type": "h1", "children": [{"text": "积石山县6.2级地震应急处置方案"}]},
                    {"id": "demo-notice", "type": "callout", "children": [{"text": "验收演示数据，不代表实时灾情或正式指挥决定。"}]},
                    {"id": "demo-assessment", "type": "h2", "children": [{"text": "一、灾情研判"}]},
                    {"id": "demo-assessment-body", "type": "p", "children": [{"text": "本章节由已核验事实、确定性判据和规则推演共同支撑。"}]},
                    {"id": "demo-response", "type": "h2", "children": [{"text": "二、应急保障"}]},
                    {"id": "demo-response-body", "type": "p", "children": [{"text": "资源缺口和调度方案需要经过人工确认后写入正式版本。"}]},
                ],
            },
        )]
    else:
        current = api.get(f"/writing/documents/{documents[0]['id']}")["current_version"]
        if current["knowledge_product_release_id"] != release["id"]:
            api.post(
                f"/writing/documents/{documents[0]['id']}/versions",
                {
                    "content": current["content"],
                    "change_summary": "绑定地震应急专属知识产品版本",
                    "publish": False,
                },
            )
    summary = {
        "project_id": project["id"],
        "project": project["name"],
        "application": application["name"],
        "application_scenario": capability["name"],
        "knowledge_release": release["version"],
        "verified_facts": len([row for row in api.get(f"/writing/projects/{project['id']}/facts") if row["verification_status"] == "verified"]),
        "alternative_plans": len(plans),
        "documents": len(documents),
        "knowledge_materials": len(materials),
        "graph_entities": graph["entities"],
        "graph_facts": graph["facts"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
