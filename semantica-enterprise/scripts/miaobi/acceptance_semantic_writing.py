"""Real upload -> pinned graph -> Semantica -> DSH report -> DOCX acceptance.
Only creates its own labelled fixture space; does not touch customer material.
Run inside the API container with existing safe bootstrap authentication.
"""
from pathlib import Path
import json
import uuid
from scripts.miaobi.demo_client import DemoClient
from scripts.miaobi.acceptance_upload_to_report import parse_sse


def run():
    api = DemoClient()
    suffix = uuid.uuid4().hex[:6]
    name = "妙笔·章节依据验收"
    space = api.post("/spaces", {"code": f"miaobi-semantic-{suffix}", "name": f"{name} {suffix}", "enabled": True})
    print("space_created", space["id"], flush=True)
    documents = []
    for path in sorted((Path(__file__).resolve().parents[2] / "demo/miaobi/semantic-writing").glob("*.md")):
        result = api.upload_file(space["id"], path, mode="both")
        documents.append(result)
        print("parsed", path.name, flush=True)
    chunks = [api.get(f"/versions/{d['version']['id']}/chunks", limit=20)["items"] for d in documents]
    assert all(chunks)
    # These are explicitly human-confirmed fixture relations, not a claim of
    # perfect LLM extraction. Every relation points to an uploaded real chunk.
    entities = {}
    for label, typ in [("安置点A", "安置点"), ("供水站C", "供水设施"), ("供水中断", "风险事件"), ("保障组D", "责任单位")]:
        entities[label] = api.post("/knowledge/entities", {"space_id": space["id"], "canonical_name": label, "entity_type": typ, "status": "published", "confidence": 1})
    specifications = [("安置点A", "依赖", "供水站C", 0), ("供水站C", "发生", "供水中断", 1), ("保障组D", "负责", "安置点A", 2)]
    for s, p, o, i in specifications:
        api.post("/knowledge/facts", {"space_id": space["id"], "subject_entity_id": entities[s]["id"], "predicate": p, "object_entity_id": entities[o]["id"], "source_chunk_id": chunks[i][0]["id"], "status": "published", "confidence": 1})
    api.post(f"/knowledge/releases/publish?space_id={space['id']}")
    product = api.post("/knowledge-products", {"code": f"semantic-writing-{suffix}", "name": name, "status": "active", "space_ids": [space["id"]]})
    release = api.post(f"/knowledge-products/{product['id']}/releases", {"note": "真实上传与人工核验关系的固定版本"})
    ontology = api.post("/ontologies", {"space_id": space["id"], "code": f"writing-{suffix}", "name": "安置保障语义模型", "namespace": f"urn:writing:{suffix}:"})
    for i, (label, kind) in enumerate([("安置点", "class"), ("供水设施", "class"), ("风险事件", "class"), ("责任单位", "class"), ("依赖", "relation"), ("发生", "relation"), ("负责", "relation"), ("受到影响", "relation")]):
        api.post(f"/ontologies/{ontology['id']}/terms", {"code": f"term_{i}", "label": label, "term_type": kind})
    published = api.post(f"/ontologies/{ontology['id']}/publish", {})
    versions = api.get(f"/ontologies/{ontology['id']}/versions")
    ontology_version = versions[0]["id"]
    rule_set = api.post("/analysis/rule-sets", {"name": f"安置点风险传导 {suffix}", "space_ids": [space["id"]]})
    rule = api.post(f"/analysis/rule-sets/{rule_set['id']}/rules", {"name": "依赖设施故障影响安置点", "definition": {"conditions": [{"predicate": "依赖", "subject": "X", "object": "Y"}, {"predicate": "发生", "subject": "Y", "object": "Z"}], "conclusion": {"predicate": "受到影响", "subject": "X", "object": "Z"}}})
    rule_versions = api.get(f"/analysis/rules/{rule['id']}/versions")
    package = api.post("/writing/scenario-packages", {"code": f"shelter-writing-{suffix}", "name": "安置保障报告", "disaster_type": "earthquake"})
    requirement = {"ontology_version_id": ontology_version, "entity_types": ["安置点"], "predicates": ["依赖", "发生", "负责"], "rule_version_ids": [rule_versions[0]["id"]]}
    business = {"inputs": [{"key": "rescue_required", "label": "搜救人员需求", "data_type": "number", "required": True}, {"key": "rescue_available", "label": "可用搜救人员", "data_type": "number", "required": True}],
                "sections": [{"key": "situation", "title": "一、现状与依据", "purpose": "根据上传巡查简报与清单，说明已知设施状态、时间、适用范围，不夸大影响。每章写足可操作的内容。", "required_inputs": []},
                             {"key": "shelter", "title": "二、安置点饮水保障", "purpose": "结合关系依据写明安置点A依赖供水站C、供水中断的影响及保障组D的职责，明确替代供水和待确认条件。不要漏掉责任单位，也不要虚构时限。", "knowledge": requirement},
                             {"key": "resources", "title": "三、人员与后续核验", "purpose": "说明需求500人与可用320人的搜救人员准备差异、需要进一步核实的库存与交通条件。引用职责指引。", "required_inputs": ["rescue_required", "rescue_available"], "toolbox_outputs": ["rescue_gap"]}],
                "toolbox": {"reasoning_enabled": False, "calculation_enabled": True, "target_sections": {"rescue_gap": ["resources"]}}, "decision_gates": [{"key": "publish", "name": "发布前确认", "required": True}], "activate": True}
    scenario = api.put(f"/writing/scenario-packages/{package['id']}/business-config", business)["version"]
    project = api.post("/writing/projects", {"code": f"writing-evidence-{suffix}", "name": f"安置点供水保障报告 {suffix}", "scenario_package_version_id": scenario["id"], "knowledge_product_release_id": release["id"]})
    for d in documents:
        api.post(f"/writing/projects/{project['id']}/materials", {"document_id": d["document"]["id"], "version_id": d["version"]["id"], "material_role": "task_data"})
    for key, label, number in [("rescue_required", "搜救人员需求", 500), ("rescue_available", "可用搜救人员", 320)]:
        fact = api.post(f"/writing/projects/{project['id']}/facts", {"fact_key": key, "label": label, "fact_type": "manual_input", "value": {"number": number}, "unit": "人", "source_type": "manual_input", "source_locator": {"document_id": documents[0]["document"]["id"]}})
        api.post(f"/writing/projects/{project['id']}/facts/{fact['id']}/confirm", {"decision": "confirm", "reason": "与上传设施清单逐项核对"})
    prefix = f"/writing/projects/{project['id']}/chapter-evidence"
    prepared = api.post(prefix + "/preview")
    derived = [r for r in prepared["references"].values() if r["kind"] == "inference"]
    assert len(derived) == 1 and "安置点A — 受到影响 → 供水中断" == derived[0]["text"]
    api.post(prefix + f"/{prepared['run_id']}/apply")
    print("semantica_verified", len(derived), "project", project["id"], flush=True)
    started = api.post(f"/writing/projects/{project['id']}/generate-report", {})
    with api.client.stream("POST", f"/writing/generation-runs/{started['id']}/agent", headers={"Accept": "text/event-stream"}) as response:
        api._raise(response)
        events = parse_sse(response)
    failures = [v.get("message", "Agent 执行失败") for e, v in events if e == "turn_failed"]
    if failures:
        raise RuntimeError("; ".join(failures))
    assert any(e == "tool_finished" for e, _ in events)
    assert any(e == "turn_completed" for e, _ in events)
    finished = api.post(f"/writing/generation-runs/{started['id']}/finalize", {})
    document_id = finished["document"]["id"]
    evidence = api.get(f"/writing/documents/{document_id}/paragraph-evidence")
    assert any(e["metadata"].get("knowledge_refs") for p in evidence["paragraphs"] for e in p["evidence"])
    for reference in prepared["references"].values():
        for source in reference["sources"]:
            opened = api.get(prefix + "/source/" + source["chunk_id"])
            assert opened["text"] and opened["document_id"] == source["document_id"]
    for gate in api.get(f"/writing/projects/{project['id']}/decision-gates"):
        api.post(f"/writing/projects/{project['id']}/decision-gates/{gate['id']}/records", {"decision": "confirm", "reason": "自动验收：材料、规则前提、计算值已逐项核对，非真实业务发布"})
    exports = []
    for format in ("docx", "pdf"):
        exported = api.post(f"/writing/documents/{document_id}/exports", {"output_format": format})
        assert exported["status"] == "succeeded"
        download = api.client.get(f"/writing/exports/{exported['id']}/download")
        api._raise(download)
        assert download.content.startswith(b"PK" if format == "docx" else b"%PDF")
        exports.append({"format": format, "id": exported["id"], "bytes": len(download.content)})
    print("report_verified", document_id, flush=True)
    return {"space_id": space["id"], "project_id": project["id"], "document_id": document_id,
            "material_count": len(documents), "relation_count": len(specifications), "inferences": len(derived),
            "events": [name for name, _ in events], "quality": finished.get("quality_report"), "exports": exports}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False), flush=True)
