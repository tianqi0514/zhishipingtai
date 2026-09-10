"""Chapter evidence orchestration; inference remains in the Semantica adapter.

Runs reuse WritingReasoningRun's immutable result/proof contract. They never
publish graph facts or rewrite documents as a side effect of preview.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from sqlalchemy import select

from packages.semantica_adapter.analyze import run_graph_inference
from .models import (AnalysisRule, AnalysisRuleSet, AnalysisRuleVersion, Chunk,
                     Document, GraphRelease, KnowledgeProductReleaseItem,
                     KnowledgeRelease, Ontology, OntologyVersion, ScenarioPackageVersion,
                     WritingProjectMaterial, WritingReasoningRun)
from .writing import content_hash
from .curation import effective_chunk_payloads

ENGINE = "semantica-writing-knowledge"


def project_fingerprint(db, project):
    materials = list(db.scalars(select(WritingProjectMaterial).where(
        WritingProjectMaterial.project_id == project.id,
        WritingProjectMaterial.status == "active", WritingProjectMaterial.deleted_at.is_(None))))
    return content_hash({"release": project.knowledge_product_release_id,
                         "scenario": project.scenario_package_version_id,
                         "materials": sorted((r.document_id, r.version_id, r.material_role) for r in materials)})


def applied_packet(db, project):
    run_id = (project.config or {}).get("writing_knowledge_run_id")
    run = db.get(WritingReasoningRun, run_id) if run_id else None
    if not run or run.tenant_id != project.tenant_id or run.project_id != project.id or run.engine != ENGINE:
        return None
    if run.mode != "applied" or run.result.get("fingerprint") != project_fingerprint(db, project):
        return None
    scenario = db.get(ScenarioPackageVersion, project.scenario_package_version_id)
    if not scenario or scenario.deleted_at is not None:
        return None
    try:
        for chapter in scenario.chapter_template.get("chapters", []):
            resolve_requirement(db, project.tenant_id, chapter.get("knowledge") or {})
    except ValueError:
        return None
    for reference in (run.result.get("references") or {}).values():
        for source in reference.get("sources") or []:
            chunk = db.get(Chunk, source.get("chunk_id"))
            document = db.get(Document, source.get("document_id"))
            if not chunk or chunk.deleted_at is not None or not document or document.deleted_at is not None or chunk.content_hash != source.get("content_hash"):
                return None
            effective = effective_chunk_payloads(db, [chunk], include_superseded=True)
            if not effective or effective[0]["effective_hash"] != source.get("effective_hash"):
                return None
    return {**run.result, "run_id": run.id}


def resolve_requirement(db, tenant_id, requirement, allowed_spaces=None):
    """Resolve only immutable published ontology and rule versions, not labels
    invented by the model. Disabled rules and removed access fail closed."""
    ontology_id = requirement.get("ontology_version_id")
    labels = set(requirement.get("entity_types") or []) | set(requirement.get("predicates") or [])
    if labels and not ontology_id:
        raise ValueError("选择对象和关系前，请先选择已发布的语义模型版本")
    aliases = {}
    if ontology_id:
        version = db.get(OntologyVersion, ontology_id)
        ontology = db.get(Ontology, version.ontology_id) if version else None
        if (not version or version.tenant_id != tenant_id or version.status != "published" or version.deleted_at is not None
                or not ontology or ontology.deleted_at is not None
                or (allowed_spaces is not None and ontology.space_id and ontology.space_id not in allowed_spaces)):
            raise ValueError("语义模型版本不可用或不在当前知识范围内")
        terms = [t for t in version.manifest.get("terms", []) if t.get("enabled", True)]
        for key, kind in (("entity_types", "class"), ("predicates", "relation")):
            candidates = {t["label"] for t in terms if t["term_type"] == kind}
            if set(requirement.get(key) or []) - candidates:
                raise ValueError("章节选择了语义模型版本中不存在的对象或关系")
        for term in terms:
            for alias in [term["label"], term["code"], *(term.get("aliases") or [])]:
                if alias in aliases and aliases[alias] != term["label"]:
                    raise ValueError(f"语义模型中“{alias}”含义不唯一，请先在智库修正")
                aliases[alias] = term["label"]
    rules = []
    for version_id in requirement.get("rule_version_ids") or []:
        version = db.get(AnalysisRuleVersion, version_id)
        rule = db.get(AnalysisRule, version.rule_id) if version else None
        rule_set = db.get(AnalysisRuleSet, rule.rule_set_id) if rule else None
        if (not version or version.tenant_id != tenant_id or not rule or not rule.enabled
                or rule.deleted_at is not None or not rule_set or not rule_set.enabled
                or rule_set.deleted_at is not None
                or (allowed_spaces is not None and not set(rule_set.space_ids or []).issubset(set(allowed_spaces)))):
            raise ValueError("章节引用的规则版本已停用或不在当前知识范围内")
        rules.append({"id": rule.id, "version_id": version.id, "name": rule.name,
                      "definition": version.definition, "dsl": version.dsl,
                      "confidence": rule.confidence})
    return aliases, rules


def validate_chapter_requirements(db, tenant_id, contract):
    for chapter in contract.get("chapter_template", {}).get("chapters", []):
        resolve_requirement(db, tenant_id, chapter.get("knowledge") or {})


def scoped_snapshot(db, project, allowed_spaces):
    selected = list(db.scalars(select(WritingProjectMaterial).where(
        WritingProjectMaterial.project_id == project.id,
        WritingProjectMaterial.status == "active", WritingProjectMaterial.deleted_at.is_(None))))
    selected_versions = {r.version_id for r in selected}
    if not selected_versions:
        raise ValueError("请先为报告选择业务材料，避免将整个知识库作为本次事实")
    items = list(db.scalars(select(KnowledgeProductReleaseItem).where(
        KnowledgeProductReleaseItem.product_release_id == project.knowledge_product_release_id,
        KnowledgeProductReleaseItem.deleted_at.is_(None))))
    facts, releases, warnings = [], [], []
    for item in items:
        if item.space_id not in allowed_spaces:
            raise ValueError("无权读取知识产品中的知识空间")
        release = db.get(KnowledgeRelease, item.knowledge_release_id)
        graph = db.get(GraphRelease, release.graph_release_id) if release else None
        if not graph or graph.tenant_id != project.tenant_id:
            raise ValueError("固定知识版本的图谱不可用")
        snapshot = (graph.validation_report or {}).get("evidence_snapshot")
        releases.append({"id": graph.id, "version": graph.release_number, "space_id": item.space_id})
        if not snapshot or snapshot.get("version") != 1:
            warnings.append("此知识版本没有不可变关系依据，请在智库重新发布知识产品版本；不会用当前关系替代历史版本。")
            continue
        if snapshot.get("checksum") != content_hash(snapshot.get("facts") or []):
            raise ValueError("知识版本的关系依据校验失败")
        for fact in snapshot["facts"]:
            source = fact.get("source") or {}
            if source.get("version_id") not in selected_versions:
                continue
            chunk = db.get(Chunk, source.get("chunk_id"))
            document = db.get(Document, source.get("document_id"))
            if (not chunk or chunk.tenant_id != project.tenant_id or chunk.space_id != item.space_id
                    or chunk.deleted_at is not None or chunk.version_id != source["version_id"]
                    or chunk.content_hash != source.get("content_hash") or not document or document.deleted_at is not None):
                warnings.append("部分关系来源已撤销或发生变化，已排除。")
                continue
            effective = effective_chunk_payloads(db, [chunk], include_superseded=True)
            if not effective or effective[0]["effective_hash"] != source.get("effective_hash"):
                warnings.append("部分来源已被人工修订或撤回，请重新发布知识版本后使用。")
                continue
            facts.append({**deepcopy(fact), "graph_release_id": graph.id})
    if len(facts) > 1500:
        raise ValueError("本次关系超过 1500 条，请缩小已选材料范围后再准备章节依据")
    return facts, releases, sorted(set(warnings))


def build_chapter_packet(*, chapters, facts, requirements):
    """Ontology-normalized bounded traversal + real Datalog closure per space.
    Absence is a missing-evidence warning, never a negative real-world fact.
    """
    sections, refs = [], {}
    for chapter in chapters:
        requirement = chapter.get("knowledge") or {}
        aliases, rules = requirements[chapter["key"]]
        requested_types = set(requirement.get("entity_types") or [])
        requested_predicates = set(requirement.get("predicates") or [])
        warnings = []
        normalized = []
        for original in facts:
            f = deepcopy(original)
            for field in ("predicate", "subject_type", "object_type"):
                f[field] = aliases.get(f.get(field), f.get(field))
            normalized.append(f)
        rule_predicates = {c["predicate"] for r in rules for c in (r.get("definition") or {}).get("conditions", [])}
        wanted = requested_predicates | rule_predicates
        filtered = [f for f in normalized if not wanted or f["predicate"] in wanted]
        # Expand the selected business objects along declared relations. The
        # endpoint identities, not similar text, determine connected evidence.
        seeds = {f[field] for f in filtered for field, typ in (("subject_entity_id", "subject_type"), ("object_entity_id", "object_type"))
                 if f.get(field) and f.get(typ) in requested_types}
        selected = []
        if requested_types:
            frontier = seeds
            for _ in range(4):
                matched = [f for f in filtered if f["subject_entity_id"] in frontier or f.get("object_entity_id") in frontier]
                selected = list({f["id"]: f for f in [*selected, *matched]}.values())
                next_frontier = frontier | {f[k] for f in matched for k in ("subject_entity_id", "object_entity_id") if f.get(k)}
                if next_frontier == frontier:
                    break
                frontier = next_frontier
        elif wanted or rules:
            selected = filtered
        missing = sorted(wanted - {f["predicate"] for f in selected})
        warnings += [f"缺少“{p}”关系的来源依据" for p in missing]
        if requested_types and not seeds:
            warnings.append("已选材料中没有匹配的业务对象")
        names = {f[k]: f[n] for f in normalized for k, n in (("subject_entity_id", "subject_name"), ("object_entity_id", "object_name")) if f.get(k)}
        conclusions, metrics = [], []
        by_space = defaultdict(list)
        for f in selected:
            by_space[f["space_id"]].append(f)
        for group in by_space.values():
            if rules:
                output = run_graph_inference(facts=group, rules=rules, max_results=100)
                conclusions.extend(output["items"])
                metrics.append(output["metrics"])
        index = {f["id"]: f for f in selected}
        derived = {f["result_key"]: f for f in conclusions}
        def leaves(conclusion, seen=None):
            seen = set(seen or ())
            key = conclusion["result_key"]
            if key in seen:
                return []
            found = []
            for premise in conclusion.get("evidence") or []:
                if premise.get("source_fact_id") in index:
                    found.append(index[premise["source_fact_id"]])
                elif premise.get("source_result_key") in derived:
                    found.extend(leaves(derived[premise["source_result_key"]], seen | {key}))
            return list({f["id"]: f for f in found}.values())
        section_refs = []
        for f in selected[:80] + conclusions[:40]:
            inferred = "result_key" in f
            sources = leaves(f) if inferred else [f]
            if not sources:
                continue
            text = f"{names.get(f['subject_entity_id'], f.get('subject_name') or '对象')} — {f['predicate']} → {names.get(f.get('object_entity_id'), f.get('object_value') or f.get('object_name') or '对象')}"
            stable = content_hash({"text": text, "rule": f.get("rule_version_id"), "sources": sorted(content_hash(s) for s in sources)})
            ref = "K" + stable[:12]
            refs[ref] = {"ref": ref, "text": text, "kind": "inference" if inferred else "relation",
                         "sources": [s["source"] for s in sources],
                         "premises": [{"text": f"{s['subject_name']} — {s['predicate']} → {s.get('object_name') or s.get('object_value')}", "source": s["source"]} for s in sources],
                         "rule_version_id": f.get("rule_version_id"), "proof": f.get("proof"),
                         "confidence": f.get("confidence"), "engine": ENGINE if inferred else None}
            section_refs.append(ref)
        if len(selected) > 80 or len(conclusions) > 40:
            warnings.append("依据已按数量上限截取，请缩小材料范围后核验完整性")
        sections.append({"key": chapter["key"], "title": chapter["title"], "references": section_refs,
                         "warnings": warnings, "relation_count": len(selected), "conclusion_count": len(conclusions),
                         "configured": bool(requested_types or wanted or rules), "engine_metrics": metrics})
    return {"sections": sections, "references": refs}


def prepare_packet(db, project, allowed_spaces):
    scenario = db.get(ScenarioPackageVersion, project.scenario_package_version_id)
    chapters = scenario.chapter_template.get("chapters") or []
    requirements = {c["key"]: resolve_requirement(db, project.tenant_id, c.get("knowledge") or {}, allowed_spaces) for c in chapters}
    facts, releases, warnings = scoped_snapshot(db, project, allowed_spaces)
    packet = build_chapter_packet(chapters=chapters, facts=facts, requirements=requirements)
    return {**packet, "graph_releases": releases, "warnings": warnings,
            "fingerprint": project_fingerprint(db, project), "input_fact_count": len(facts),
            "configured": any(s["configured"] for s in packet["sections"])}
