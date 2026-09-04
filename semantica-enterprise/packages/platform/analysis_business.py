from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.semantica_adapter.analyze import run_graph_inference

from .curation import effective_entity, effective_fact
from .models import (
    AnalysisRule,
    AnalysisRuleSet,
    CanonicalEntity,
    Document,
    DocumentVersion,
    Fact,
    GraphRelease,
    InferenceRun,
    InferredFact,
)


ANALYSIS_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "policy_scope",
        "name": "制度适用范围",
        "category": "制度治理",
        "question": "某项制度是否适用于下属单位？",
        "description": "根据制度适用对象和组织管理关系，推导制度覆盖的下属单位。",
        "roles": {"P": "制度", "G": "上级组织", "U": "下属单位"},
        "definition": {
            "conditions": [
                {"predicate": "适用于", "subject": "P", "object": "G"},
                {"predicate": "管理", "subject": "G", "object": "U"},
            ],
            "conclusion": {"predicate": "适用于", "subject": "P", "object": "U"},
        },
    },
    {
        "id": "supplier_risk",
        "name": "供应商风险传导",
        "category": "风险管理",
        "question": "哪些项目可能受到供应商风险影响？",
        "description": "沿供应商、产品和项目关系，把已知供应风险传导到受影响项目。",
        "roles": {"S": "供应商", "P": "产品", "J": "项目", "R": "风险"},
        "definition": {
            "conditions": [
                {"predicate": "供应", "subject": "S", "object": "P"},
                {"predicate": "用于", "subject": "P", "object": "J"},
                {"predicate": "存在风险", "subject": "S", "object": "R"},
            ],
            "conclusion": {"predicate": "受到影响", "subject": "J", "object": "R"},
        },
    },
    {
        "id": "system_dependency",
        "name": "项目和系统依赖影响",
        "category": "技术治理",
        "question": "某个基础系统异常会影响哪些业务应用？",
        "description": "沿系统依赖关系推导基础能力变化的潜在影响范围。",
        "roles": {"A": "业务应用", "S": "依赖系统", "B": "基础平台"},
        "definition": {
            "conditions": [
                {"predicate": "依赖", "subject": "A", "object": "S"},
                {"predicate": "依赖", "subject": "S", "object": "B"},
            ],
            "conclusion": {"predicate": "间接依赖", "subject": "A", "object": "B"},
        },
    },
    {
        "id": "process_compliance",
        "name": "流程合规判断",
        "category": "合规管理",
        "question": "哪些流程实例需要应用某项制度要求？",
        "description": "根据流程所属业务与制度约束范围，推导流程应遵循的制度。",
        "roles": {"F": "流程", "B": "业务类型", "P": "制度"},
        "definition": {
            "conditions": [
                {"predicate": "属于业务", "subject": "F", "object": "B"},
                {"predicate": "约束", "subject": "P", "object": "B"},
            ],
            "conclusion": {"predicate": "应遵循", "subject": "F", "object": "P"},
        },
    },
    {
        "id": "organization_responsibility",
        "name": "组织责任关系",
        "category": "组织管理",
        "question": "某项职责最终由哪个组织承担？",
        "description": "沿组织管理和直接责任关系推导上级组织的责任范围。",
        "roles": {"G": "上级组织", "U": "下属单位", "D": "职责"},
        "definition": {
            "conditions": [
                {"predicate": "管理", "subject": "G", "object": "U"},
                {"predicate": "负责", "subject": "U", "object": "D"},
            ],
            "conclusion": {"predicate": "管理职责", "subject": "G", "object": "D"},
        },
    },
    {
        "id": "product_relation",
        "name": "产品关联发现",
        "category": "产品管理",
        "question": "哪些产品服务于同一个业务场景？",
        "description": "根据产品与业务场景的关联推导产品之间的协同关系。",
        "roles": {"P": "产品", "S": "业务场景", "Q": "关联产品"},
        "definition": {
            "conditions": [
                {"predicate": "服务于", "subject": "P", "object": "S"},
                {"predicate": "服务于", "subject": "Q", "object": "S"},
            ],
            "conclusion": {"predicate": "场景协同", "subject": "P", "object": "Q"},
        },
    },
)


def active_analysis_context(
    db: Session,
    *,
    tenant_id: str,
    space_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    entity_rows = list(
        db.scalars(
            select(CanonicalEntity).where(
                CanonicalEntity.tenant_id == tenant_id,
                CanonicalEntity.space_id.in_(space_ids),
                CanonicalEntity.deleted_at.is_(None),
            )
        )
    )
    entities: dict[str, dict[str, Any]] = {}
    for row in entity_rows:
        value = effective_entity(db, row)
        if value.get("status") not in {"published", "active"}:
            continue
        entities[row.id] = {
            "id": row.id,
            "space_id": row.space_id,
            "name": value["canonical_name"],
            "type": value["entity_type"],
        }

    fact_rows = list(
        db.scalars(
            select(Fact).where(
                Fact.tenant_id == tenant_id,
                Fact.space_id.in_(space_ids),
                Fact.deleted_at.is_(None),
            )
        )
    )
    facts: list[dict[str, Any]] = []
    for row in fact_rows:
        value = effective_fact(db, row)
        if value.get("status") != "published":
            continue
        subject = entities.get(str(value.get("subject_entity_id") or ""))
        object_id = str(value.get("object_entity_id") or "")
        obj = entities.get(object_id) if object_id else None
        if subject is None or (object_id and obj is None):
            continue
        facts.append(
            {
                "id": row.id,
                "space_id": row.space_id,
                "subject_entity_id": subject["id"],
                "subject_name": subject["name"],
                "subject_type": subject["type"],
                "predicate": value["predicate"],
                "object_entity_id": obj["id"] if obj else None,
                "object_name": obj["name"] if obj else None,
                "object_type": obj["type"] if obj else "属性值",
                "object_value": value.get("object_value"),
                "source_chunk_id": row.source_chunk_id,
                "confidence": value["confidence"],
            }
        )
    return entities, facts


def analysis_vocabulary(db: Session, *, tenant_id: str, space_id: str) -> dict[str, Any]:
    entities, facts = active_analysis_context(db, tenant_id=tenant_id, space_ids=[space_id])
    type_rows: dict[str, dict[str, Any]] = {}
    for entity in entities.values():
        row = type_rows.setdefault(entity["type"], {"name": entity["type"], "count": 0, "samples": []})
        row["count"] += 1
        if len(row["samples"]) < 3:
            row["samples"].append({"id": entity["id"], "name": entity["name"]})

    predicate_rows: dict[str, dict[str, Any]] = {}
    for fact in facts:
        row = predicate_rows.setdefault(
            fact["predicate"],
            {"name": fact["predicate"], "count": 0, "evidence_count": 0, "samples": []},
        )
        row["count"] += 1
        row["evidence_count"] += int(bool(fact.get("source_chunk_id")))
        if len(row["samples"]) < 3:
            row["samples"].append(
                {
                    "subject": fact["subject_name"],
                    "subject_type": fact["subject_type"],
                    "predicate": fact["predicate"],
                    "object": fact["object_name"] or fact["object_value"],
                    "object_type": fact["object_type"],
                    "source_chunk_id": fact.get("source_chunk_id"),
                }
            )
    return {
        "space_id": space_id,
        "entity_count": len(entities),
        "asserted_fact_count": len(facts),
        "entity_types": sorted(type_rows.values(), key=lambda item: (-item["count"], item["name"])),
        "predicates": sorted(predicate_rows.values(), key=lambda item: (-item["count"], item["name"])),
    }


def analysis_readiness(db: Session, *, tenant_id: str, space_id: str) -> dict[str, Any]:
    vocabulary = analysis_vocabulary(db, tenant_id=tenant_id, space_id=space_id)
    published_documents = db.scalar(
        select(func.count())
        .select_from(Document)
        .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
        .where(
            Document.tenant_id == tenant_id,
            Document.space_id == space_id,
            Document.status == "ready",
            Document.deleted_at.is_(None),
            DocumentVersion.status == "ready",
            DocumentVersion.deleted_at.is_(None),
        )
    ) or 0
    graph_release = db.scalar(
        select(GraphRelease)
        .where(
            GraphRelease.tenant_id == tenant_id,
            GraphRelease.space_id == space_id,
            GraphRelease.status == "published",
            GraphRelease.deleted_at.is_(None),
        )
        .order_by(GraphRelease.release_number.desc())
        .limit(1)
    )
    inferred_count = db.scalar(
        select(func.count()).select_from(InferredFact).where(
            InferredFact.tenant_id == tenant_id,
            InferredFact.space_id == space_id,
            InferredFact.status == "published",
            InferredFact.deleted_at.is_(None),
        )
    ) or 0
    evidence_count = sum(row["evidence_count"] for row in vocabulary["predicates"])
    fact_count = int(vocabulary["asserted_fact_count"])
    active_sets = [
        row
        for row in db.scalars(
            select(AnalysisRuleSet).where(
                AnalysisRuleSet.tenant_id == tenant_id,
                AnalysisRuleSet.enabled.is_(True),
                AnalysisRuleSet.deleted_at.is_(None),
            )
        )
        if space_id in (row.space_ids or [])
    ]
    active_rule_count = 0
    if active_sets:
        active_rule_count = db.scalar(
            select(func.count()).select_from(AnalysisRule).where(
                AnalysisRule.rule_set_id.in_([row.id for row in active_sets]),
                AnalysisRule.enabled.is_(True),
                AnalysisRule.deleted_at.is_(None),
            )
        ) or 0
    runs = [
        row
        for row in db.scalars(
            select(InferenceRun).where(
                InferenceRun.tenant_id == tenant_id,
                InferenceRun.deleted_at.is_(None),
            ).order_by(InferenceRun.created_at.desc()).limit(500)
        )
        if space_id in (row.space_ids or [])
    ]
    last_run = runs[0] if runs else None
    latest_success = next((row for row in runs if row.status == "succeeded"), None)
    recent_new = 0
    if latest_success:
        recent_new = db.scalar(
            select(func.count()).select_from(InferredFact).where(
                InferredFact.run_id == latest_success.id,
                InferredFact.deleted_at.is_(None),
            )
        ) or 0

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    recommendations: list[dict[str, str]] = []
    if not vocabulary["entity_count"]:
        blockers.append({"code": "no_entities", "message": "当前空间还没有可用于分析的知识对象。"})
    if not fact_count:
        blockers.append({"code": "no_asserted_facts", "message": "当前空间还没有可用于规则判断的知识关系。"})
    if not published_documents:
        warnings.append({"code": "no_documents", "message": "当前空间没有已发布文档，人工关系仍可分析，但来源追溯可能不足。"})
    if fact_count and evidence_count / fact_count < 0.5:
        warnings.append({"code": "low_evidence_coverage", "message": "少于一半的已有关系关联了来源片段。"})
    if graph_release is None:
        warnings.append({"code": "no_graph_release", "message": "当前空间尚未形成正式图谱发布。"})
    if not active_rule_count:
        recommendations.append({"code": "create_task", "label": "创建分析任务", "view": "analysis"})
    if blockers:
        recommendations.extend(
            [
                {"code": "view_graph", "label": "查看知识图谱", "view": "knowledge"},
                {"code": "view_documents", "label": "查看文档加工", "view": "documents"},
            ]
        )
    return {
        "space_id": space_id,
        "ready": not blockers,
        "published_document_count": int(published_documents),
        "current_graph_release": graph_release.release_number if graph_release else None,
        "entity_count": vocabulary["entity_count"],
        "asserted_fact_count": fact_count,
        "inferred_fact_count": int(inferred_count),
        "evidence_linked_fact_count": evidence_count,
        "evidence_coverage": round(evidence_count / fact_count, 4) if fact_count else 0,
        "entity_types": vocabulary["entity_types"],
        "predicates": vocabulary["predicates"],
        "active_rule_count": int(active_rule_count),
        "last_inference_at": (last_run.finished_at or last_run.created_at).isoformat() if last_run else None,
        "recent_new_count": int(recent_new),
        "blocking_issues": blockers,
        "warnings": warnings,
        "recommended_actions": recommendations,
    }


def templates_for_vocabulary(vocabulary: dict[str, Any]) -> list[dict[str, Any]]:
    available = {row["name"] for row in vocabulary.get("predicates") or []}
    rows: list[dict[str, Any]] = []
    for template in ANALYSIS_TEMPLATES:
        item = {**template, "definition": {**template["definition"]}}
        required = list(dict.fromkeys(row["predicate"] for row in template["definition"]["conditions"]))
        missing = [predicate for predicate in required if predicate not in available]
        item["required_predicates"] = required
        item["missing_predicates"] = missing
        item["ready"] = not missing
        rows.append(item)
    return rows


def preview_rule_matches(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    definition: dict[str, Any],
    confidence: float,
    max_results: int,
) -> dict[str, Any]:
    entities, facts = active_analysis_context(db, tenant_id=tenant_id, space_ids=[space_id])
    conditions = definition.get("conditions") or []
    counts = Counter(item["predicate"] for item in facts)
    missing = [item["predicate"] for item in conditions if not counts.get(item["predicate"])]
    if not facts:
        diagnostics = [{"code": "no_asserted_facts", "message": "当前空间没有可用于规则判断的已有知识关系。"}]
        result = {"items": [], "metrics": {"engine": "semantica.reasoning.DatalogReasoner", "input_facts": 0, "rules": 1, "returned_results": 0}}
    else:
        result = run_graph_inference(
            facts=facts,
            rules=[{"id": "match-preview", "version_id": "match-preview-v1", "definition": definition, "confidence": confidence}],
            max_results=max_results,
        )
        diagnostics = []
        if missing:
            diagnostics.append({"code": "missing_predicate", "message": f"当前知识中没有关系：{'、'.join(dict.fromkeys(missing))}。"})
        elif not result["items"]:
            diagnostics.append({"code": "empty_relation_intersection", "message": "每个条件都有数据，但这些关系暂时无法连接到同一组业务对象。"})
    samples = []
    for item in result["items"][:5]:
        subject = entities.get(item["subject_entity_id"], {})
        obj = entities.get(item.get("object_entity_id") or "", {})
        samples.append(
            {
                "subject": subject.get("name", item["subject_entity_id"]),
                "predicate": item["predicate"],
                "object": obj.get("name", item.get("object_value")),
                "confidence": item["confidence"],
                "premise_count": len(item.get("evidence") or []),
                "evidence_linked_count": sum(bool(row.get("source_chunk_id")) for row in item.get("evidence") or []),
            }
        )
    return {
        "valid": True,
        "space_id": space_id,
        "engine": result["metrics"].get("engine"),
        "input_fact_count": len(facts),
        "condition_stats": [
            {"predicate": item["predicate"], "fact_count": counts.get(item["predicate"], 0)}
            for item in conditions
        ],
        "predicted_count": len(result["items"]),
        "truncated": bool(result["metrics"].get("truncated")),
        "samples": samples,
        "diagnostics": diagnostics,
        "metrics": result["metrics"],
    }


def run_comparison(db: Session, run: InferenceRun) -> dict[str, Any]:
    current = list(
        db.scalars(
            select(InferredFact).where(
                InferredFact.run_id == run.id,
                InferredFact.deleted_at.is_(None),
            )
        )
    )
    previous_runs = [
        candidate
        for candidate in db.scalars(
            select(InferenceRun)
            .where(
                InferenceRun.tenant_id == run.tenant_id,
                InferenceRun.status == "succeeded",
                InferenceRun.id != run.id,
                InferenceRun.created_at < run.created_at,
                InferenceRun.deleted_at.is_(None),
            )
            .order_by(InferenceRun.created_at.desc())
            .limit(200)
        )
        if (run.scenario_id and candidate.scenario_id == run.scenario_id)
        or (not run.scenario_id and candidate.rule_set_id == run.rule_set_id)
    ]
    previous_run = previous_runs[0] if previous_runs else None
    previous = list(
        db.scalars(
            select(InferredFact).where(
                InferredFact.run_id == previous_run.id,
                InferredFact.deleted_at.is_(None),
            )
        )
    ) if previous_run else []

    def logical_key(item: InferredFact) -> tuple[str, str, str]:
        return (item.subject_entity_id, item.predicate, item.object_entity_id or str(item.object_value or ""))

    current_by_key = {logical_key(item): item for item in current}
    previous_by_key = {logical_key(item): item for item in previous}
    new_keys = current_by_key.keys() - previous_by_key.keys()
    stable_keys = current_by_key.keys() & previous_by_key.keys()
    invalidated_keys = previous_by_key.keys() - current_by_key.keys()
    evidence_changed = [
        key for key in stable_keys
        if current_by_key[key].proof != previous_by_key[key].proof
        or current_by_key[key].rule_version_id != previous_by_key[key].rule_version_id
    ]
    invalidated_entity_ids = {
        entity_id
        for key in invalidated_keys
        for entity_id in (previous_by_key[key].subject_entity_id, previous_by_key[key].object_entity_id)
        if entity_id
    }
    invalidated_entity_names = {
        entity.id: entity.canonical_name
        for entity in db.scalars(
            select(CanonicalEntity).where(CanonicalEntity.id.in_(invalidated_entity_ids))
        )
    } if invalidated_entity_ids else {}
    return {
        "run_id": run.id,
        "previous_run_id": previous_run.id if previous_run else None,
        "new_count": len(new_keys),
        "unchanged_count": len(stable_keys),
        "invalidated_count": len(invalidated_keys),
        "evidence_changed_count": len(evidence_changed),
        "new_fact_ids": [current_by_key[key].id for key in new_keys],
        "unchanged_fact_ids": [current_by_key[key].id for key in stable_keys],
        "evidence_changed_fact_ids": [current_by_key[key].id for key in evidence_changed],
        "invalidated_items": [
            {
                "id": previous_by_key[key].id,
                "subject_entity_id": previous_by_key[key].subject_entity_id,
                "subject_name": invalidated_entity_names.get(previous_by_key[key].subject_entity_id, "已删除对象"),
                "predicate": previous_by_key[key].predicate,
                "object_entity_id": previous_by_key[key].object_entity_id,
                "object_name": invalidated_entity_names.get(previous_by_key[key].object_entity_id or ""),
                "object_value": previous_by_key[key].object_value,
                "previous_run_id": previous_run.id if previous_run else None,
            }
            for key in invalidated_keys
        ],
    }
