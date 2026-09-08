from __future__ import annotations

import json
from pathlib import Path

from demo_client import (
    APPLICATION_CODE,
    CAPABILITY_SCENARIO_CODE,
    DemoClient,
    PRODUCT_CODE,
    PROJECT_CODE,
    SPACE_CODE,
)


ROOT = Path(__file__).resolve().parents[2]
TRUTH = json.loads((ROOT / "demo/miaobi/earthquake_ground_truth.json").read_text(encoding="utf-8"))


def main() -> None:
    api = DemoClient()
    project = api.one("/writing/projects", "code", PROJECT_CODE)
    if not project:
        raise RuntimeError("妙笔地震验收任务不存在")
    facts = {row["fact_key"]: row for row in api.get(f"/writing/projects/{project['id']}/facts")}
    failures: list[str] = []
    application = api.one("/applications", "code", APPLICATION_CODE)
    capability = api.one("/application-scenarios", "code", CAPABILITY_SCENARIO_CODE)
    space = api.one("/spaces", "code", SPACE_CODE)
    product = api.one("/knowledge-products", "code", PRODUCT_CODE)
    if not application or project.get("application_id") != application["id"]:
        failures.append("妙笔任务未绑定应用中心中的正式应用")
    if not capability or not capability.get("current_version_id"):
        failures.append("妙笔地震能力场景尚未发布")
    if not space or not product:
        failures.append("地震应急专属知识空间或知识产品不存在")
    elif project.get("knowledge_product_release_id") not in {
        row["id"] for row in api.get(f"/knowledge-products/{product['id']}/releases")
    }:
        failures.append("妙笔任务没有绑定地震应急专属知识产品版本")
    if application:
        grants = api.get(f"/applications/{application['id']}/grants")
        if not any(row["resource_type"] == "scenario" and row["effect"] == "allow" for row in grants):
            failures.append("妙笔应用未获得地震能力场景调用授权")
    for key, expected in TRUTH["facts"].items():
        if key not in facts:
            failures.append(f"缺少事实 {key}")
            continue
        value = facts[key]["value"].get("number", facts[key]["value"].get("text"))
        if value != expected["value"]:
            failures.append(f"{key}: {value!r} != {expected['value']!r}")
    plans = api.get(f"/writing/projects/{project['id']}/plans")
    routes = {tuple(row["result"]["route"]["path"]) for row in plans}
    if len(plans) != 3 or len(routes) != 3:
        failures.append("三套方案没有形成三条真实不同路线")
    selected_plans = [row for row in plans if row.get("status") == "selected"]
    if len(selected_plans) != 1 or selected_plans[0].get("plan_key") != "balanced":
        failures.append("演示任务应且仅应选择综合平衡方案")
    reasoning = api.get(f"/writing/projects/{project['id']}/reasoning-runs")
    if not reasoning or reasoning[0]["engine"] != "semantica-datalog" or reasoning[0]["status"] != "succeeded":
        failures.append("Semantica 地震等级推演未成功")
    documents = api.get(f"/writing/projects/{project['id']}/documents")
    if not documents:
        failures.append("演示文稿不存在")
    else:
        release_version = next(
            (
                row["version"]
                for row in api.get(f"/knowledge-products/{product['id']}/releases")
                if row["id"] == project.get("knowledge_product_release_id")
            ),
            None,
        ) if product else None
        ready_title = f"积石山县6.2级地震应急处置方案（知识版本 {release_version}）"
        ready_document = next((row for row in documents if row["title"] == ready_title), None)
        if not ready_document:
            failures.append("缺少与当前知识产品版本一致的可审校演示文稿")
        else:
            validation = api.post(
                f"/writing/documents/{ready_document['id']}/validate",
                {"for_publish": False},
            )
            if validation.get("issues"):
                failures.append(f"当前演示文稿存在可信块问题：{validation['issues']}")
            bindings = api.get(f"/writing/documents/{ready_document['id']}/bindings")
            if len(bindings) < 3 or any(
                (row.get("metadata_json") or {}).get("content_hash_algorithm") != "canonical-json-v1"
                for row in bindings
            ):
                failures.append("当前演示文稿没有完成引用、测算和推演块的服务端哈希绑定")
    material_documents = api.get("/documents", space_id=space["id"]) if space else []
    if len(material_documents) != 3:
        failures.append(f"地震应急专属空间应有 3 份确定性材料，实际为 {len(material_documents)} 份")
    for item in material_documents:
        detail = api.get(f"/documents/{item['id']}")
        current = next(
            (row for row in detail.get("versions", []) if row["id"] == item.get("current_version_id")),
            None,
        )
        summary = (current or {}).get("parse_summary") or {}
        completed = set(summary.get("knowledge_targets_completed") or [])
        if (current or {}).get("status") != "ready" or summary.get("knowledge_status") != "published" or completed != {"graph", "vector"}:
            failures.append(f"材料《{item['title']}》未完成向量与图谱一致性发布")
    search = api.post(
        f"/writing/projects/{project['id']}/knowledge/search",
        {
            "query": "积石山县6.2级地震达到什么灾害等级",
            "top_k": 8,
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": False,
        },
    )
    titles = [item["title"] for item in search.get("items", [])]
    if not titles or "地震应急预案摘编（验收演示）.md" not in titles:
        failures.append("知识产品锁定检索未命中地震应急预案")
    if len({item["document_id"] for item in search.get("items", [])}) != len(search.get("items", [])):
        failures.append("知识产品锁定检索返回了重复文档依据")
    if not search.get("snapshot_locked") or search.get("knowledge_product_release_id") != project.get("knowledge_product_release_id"):
        failures.append("妙笔检索没有锁定当前任务的不可变知识产品版本")
    graph_facts = api.get("/knowledge/facts", space_id=space["id"], limit=500) if space else {"total": 0, "items": []}
    if graph_facts.get("total") != 6 or sum(bool(row.get("source_chunk_id")) for row in graph_facts.get("items", [])) != 6:
        failures.append("地震验收图谱应包含 6 条具有真实片段依据的业务关系")
    if not search.get("channel_counts", {}).get("graph"):
        failures.append("锁定知识产品的混合检索没有命中图谱关系")
    if failures:
        raise RuntimeError("；".join(failures))
    print(json.dumps({
        "ready": True,
        "project_id": project["id"],
        "ground_truth": f"{len(TRUTH['facts'])}/{len(TRUTH['facts'])}",
        "plans": len(plans),
        "reasoning_engine": reasoning[0]["engine"],
        "documents": len(documents),
        "application": application["name"] if application else None,
        "knowledge_product": product["name"] if product else None,
        "knowledge_materials": len(material_documents),
        "retrieval_hits": len(titles),
        "retrieval_channels": search.get("channel_counts"),
        "graph_facts": graph_facts.get("total"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
