from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.semantica_adapter.analyze import run_graph_inference

from .curation import effective_entity, effective_fact
from .models import (
    AnalysisRule,
    AnalysisRuleVersion,
    CanonicalEntity,
    Fact,
    InferenceEvidence,
    InferenceRun,
    InferredFact,
)


ProgressCallback = Callable[[int, str, dict[str, Any]], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active(model):
    return model.deleted_at.is_(None)


def _progress(callback: ProgressCallback | None, percent: int, stage: str, detail: dict[str, Any]) -> None:
    if callback:
        callback(percent, stage, detail)


def execute_inference_run(
    db: Session,
    run_id: str,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    run = db.get(InferenceRun, run_id)
    if run is None or run.deleted_at is not None:
        raise ValueError("推理任务不存在")
    run.status = "running"
    run.progress = 5
    run.started_at = _now()
    run.error_code = None
    run.error_message = None
    db.commit()
    _progress(progress, 5, "scope", {"space_ids": run.space_ids})

    current = _now()
    rules = list(
        db.scalars(
            select(AnalysisRule)
            .where(
                AnalysisRule.tenant_id == run.tenant_id,
                AnalysisRule.rule_set_id == run.rule_set_id,
                AnalysisRule.enabled.is_(True),
                _active(AnalysisRule),
            )
            .order_by(AnalysisRule.priority, AnalysisRule.created_at)
        )
    )
    rules = [
        row
        for row in rules
        if (row.valid_from is None or row.valid_from <= current)
        and (row.valid_to is None or row.valid_to > current)
    ]
    if not rules:
        raise ValueError("规则集中没有当前有效的启用规则")
    versions: dict[str, AnalysisRuleVersion] = {}
    for rule in rules:
        version = db.scalar(
            select(AnalysisRuleVersion).where(
                AnalysisRuleVersion.rule_id == rule.id,
                AnalysisRuleVersion.version == rule.current_version,
            )
        )
        if version is None:
            raise ValueError(f"规则“{rule.name}”缺少当前版本")
        versions[rule.id] = version
    run.progress = 20
    db.commit()
    _progress(progress, 20, "rules", {"rules": len(rules)})

    entity_rows = list(db.scalars(
        select(CanonicalEntity).where(
            CanonicalEntity.tenant_id == run.tenant_id,
            CanonicalEntity.space_id.in_(run.space_ids),
            _active(CanonicalEntity),
        )
    ))
    entity_values = {row.id: effective_entity(db, row) for row in entity_rows}
    entities = {
        row.id: row for row in entity_rows
        if entity_values[row.id].get("status") in {"published", "active"}
    }
    facts = list(
        db.scalars(
            select(Fact).where(
                Fact.tenant_id == run.tenant_id,
                Fact.space_id.in_(run.space_ids),
                Fact.status == "published",
                _active(Fact),
            )
        )
    )
    fact_values = {row.id: effective_fact(db, row) for row in facts}
    fact_payload = []
    for row in facts:
        effective = fact_values[row.id]
        if effective.get("status") != "published":
            continue
        subject = entities.get(effective["subject_entity_id"])
        obj = entities.get(effective["object_entity_id"]) if effective.get("object_entity_id") else None
        if subject is None or (effective.get("object_entity_id") and obj is None):
            continue
        fact_payload.append(
            {
                "id": row.id,
                "space_id": row.space_id,
                "subject_entity_id": effective["subject_entity_id"],
                "subject_name": entity_values[subject.id]["canonical_name"],
                "predicate": effective["predicate"],
                "object_entity_id": effective["object_entity_id"],
                "object_name": entity_values[obj.id]["canonical_name"] if obj else None,
                "object_value": effective["object_value"],
                "source_chunk_id": row.source_chunk_id,
                "confidence": effective["confidence"],
            }
        )
    run.progress = 40
    db.commit()
    _progress(progress, 40, "facts", {"facts": len(fact_payload), "entities": len(entities)})

    result = run_graph_inference(
        facts=fact_payload,
        rules=[
            {
                "id": rule.id,
                "version_id": versions[rule.id].id,
                "definition": rule.definition,
                "dsl": rule.dsl,
                "confidence": rule.confidence,
            }
            for rule in rules
        ],
        max_results=int((run.run_input or {}).get("max_results", 1000)),
    )
    run.progress = 70
    db.commit()
    _progress(progress, 70, "reasoning", result["metrics"])

    created: list[InferredFact] = []
    pending_evidence: list[tuple[dict[str, Any], InferredFact]] = []
    created_by_result_key: dict[str, InferredFact] = {}
    skipped_cross_space = 0
    for item in result["items"]:
        subject = entities.get(item["subject_entity_id"])
        obj = entities.get(item.get("object_entity_id")) if item.get("object_entity_id") else None
        if subject is None or (obj is not None and obj.space_id != subject.space_id):
            skipped_cross_space += 1
            continue
        checksum_source = "|".join(
            [
                item["rule_version_id"],
                subject.id,
                item["predicate"],
                obj.id if obj else str(item.get("object_value") or ""),
            ]
        )
        inferred = InferredFact(
            tenant_id=run.tenant_id,
            run_id=run.id,
            rule_id=item["rule_id"],
            rule_version_id=item["rule_version_id"],
            space_id=subject.space_id,
            subject_entity_id=subject.id,
            predicate=item["predicate"],
            object_entity_id=obj.id if obj else None,
            object_value=item.get("object_value") if obj is None else None,
            confidence=item["confidence"],
            status="published" if run.mode == "publish" else "preview",
            proof=item["proof"],
            checksum=hashlib.sha256(checksum_source.encode()).hexdigest(),
            published_at=_now() if run.mode == "publish" else None,
        )
        db.add(inferred)
        db.flush()
        created.append(inferred)
        pending_evidence.append((item, inferred))
        if item.get("result_key"):
            created_by_result_key[str(item["result_key"])] = inferred

    for item, inferred in pending_evidence:
        for ordinal, evidence in enumerate(item.get("evidence") or [], start=1):
            source_inferred = created_by_result_key.get(str(evidence.get("source_result_key") or ""))
            db.add(
                InferenceEvidence(
                    tenant_id=run.tenant_id,
                    inferred_fact_id=inferred.id,
                    ordinal=ordinal,
                    premise_type=str(evidence.get("premise_type") or "asserted"),
                    source_fact_id=evidence.get("source_fact_id"),
                    source_inferred_fact_id=source_inferred.id if source_inferred else None,
                    source_chunk_id=evidence.get("source_chunk_id"),
                    snapshot=evidence,
                )
            )

    invalidated = 0
    if run.mode == "publish":
        current_ids = {row.id for row in created}
        old_rows = list(
            db.scalars(
                select(InferredFact)
                .join(InferenceRun, InferenceRun.id == InferredFact.run_id)
                .where(
                    InferredFact.tenant_id == run.tenant_id,
                    InferredFact.space_id.in_(run.space_ids),
                    InferredFact.status == "published",
                    InferredFact.run_id != run.id,
                    InferenceRun.rule_set_id == run.rule_set_id,
                    _active(InferredFact),
                )
            )
        )
        new_checksums = {row.checksum for row in created}
        for row in old_rows:
            if row.id not in current_ids and row.checksum not in new_checksums:
                row.status = "invalidated"
                row.invalidated_at = _now()
                invalidated += 1

    metrics = {
        **result["metrics"],
        "persisted_results": len(created),
        "published": run.mode == "publish",
        "invalidated": invalidated,
        "skipped_cross_space": skipped_cross_space,
    }
    run.metrics = metrics
    run.status = "succeeded"
    run.progress = 100
    run.finished_at = _now()
    db.commit()
    _progress(progress, 100, "persist", metrics)
    return {"run_id": run.id, **metrics}


def inference_result_rows(db: Session, run_id: str) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(InferredFact)
            .where(InferredFact.run_id == run_id, _active(InferredFact))
            .order_by(InferredFact.confidence.desc(), InferredFact.created_at)
        )
    )
    entity_ids = {row.subject_entity_id for row in rows} | {
        row.object_entity_id for row in rows if row.object_entity_id
    }
    entities = {
        row.id: row for row in db.scalars(select(CanonicalEntity).where(CanonicalEntity.id.in_(entity_ids)))
    } if entity_ids else {}
    entity_values = {row.id: effective_entity(db, row) for row in entities.values()}
    result: list[dict[str, Any]] = []
    for row in rows:
        evidence = list(
            db.scalars(
                select(InferenceEvidence)
                .where(InferenceEvidence.inferred_fact_id == row.id)
                .order_by(InferenceEvidence.ordinal)
            )
        )
        result.append(
            {
                "id": row.id,
                "run_id": row.run_id,
                "rule_id": row.rule_id,
                "rule_version_id": row.rule_version_id,
                "space_id": row.space_id,
                "subject_entity_id": row.subject_entity_id,
                "subject_name": entity_values[row.subject_entity_id]["canonical_name"]
                if row.subject_entity_id in entities else row.subject_entity_id,
                "predicate": row.predicate,
                "object_entity_id": row.object_entity_id,
                "object_name": entity_values[row.object_entity_id]["canonical_name"]
                if row.object_entity_id in entities else row.object_value,
                "confidence": row.confidence,
                "status": row.status,
                "proof": row.proof,
                "checksum": row.checksum,
                "created_at": row.created_at.isoformat(),
                "evidence": [item.snapshot for item in evidence],
            }
        )
    return result
