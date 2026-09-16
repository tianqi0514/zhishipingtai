from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.semantica_adapter.writing_extract import (
    JointWritingExtraction,
    WritingEvidenceInput,
    candidate_key,
    extract_writing_knowledge,
)

from .models import (
    Chunk,
    ContentElement,
    Document,
    DocumentVersion,
    WritingClaim,
    WritingEntityCandidate,
    WritingEvidence,
    WritingExtractionRun,
    WritingFact,
    WritingGovernanceAction,
    WritingGraphRelease,
    WritingGraphReleaseItem,
    WritingRelation,
)


WRITING_GRAPH_STRATEGY_VERSION = "writing-graph-v1"
WRITING_GRAPH_SCHEMA_VERSION = "joint-v1"
WRITING_GRAPH_STATUSES = {
    "candidate", "verified", "rejected", "conflicted", "superseded", "stale",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def stable_evidence_key(version_id: str, chunk_id: str, chunk_hash: str) -> str:
    return hashlib.sha256(f"writing-evidence-v1:{version_id}:{chunk_id}:{chunk_hash}".encode()).hexdigest()


def ensure_writing_evidence(
    db: Session,
    *,
    document: Document,
    version: DocumentVersion,
    actor_id: str | None = None,
) -> list[WritingEvidence]:
    """Project current parser chunks to immutable, reusable Evidence rows."""

    chunks = list(db.scalars(select(Chunk).where(
        Chunk.version_id == version.id,
        Chunk.deleted_at.is_(None),
        Chunk.status != "superseded",
    ).order_by(Chunk.ordinal)))
    result: list[WritingEvidence] = []
    for index, chunk in enumerate(chunks):
        key = stable_evidence_key(version.id, chunk.chunk_id, chunk.content_hash)
        row = db.scalar(select(WritingEvidence).where(
            WritingEvidence.tenant_id == version.tenant_id,
            WritingEvidence.evidence_key == key,
            WritingEvidence.deleted_at.is_(None),
        ))
        element = db.get(ContentElement, chunk.element_id) if chunk.element_id else None
        locator = {
            "page": chunk.page_number,
            "structural_path": chunk.structural_path,
            "source_span": chunk.source_span or {},
            "element_type": element.element_type if element else None,
            "element_id": element.element_id if element else None,
            "element_metadata": element.element_metadata if element else {},
        }
        if row is None:
            row = WritingEvidence(
                tenant_id=version.tenant_id,
                space_id=document.space_id,
                document_id=document.id,
                document_version_id=version.id,
                content_element_id=chunk.element_id,
                chunk_id=chunk.id,
                evidence_key=key,
                filename=version.filename,
                file_version=version.version_number,
                locator=locator,
                text=chunk.text,
                context_before=chunks[index - 1].text[-500:] if index else "",
                context_after=chunks[index + 1].text[:500] if index + 1 < len(chunks) else "",
                content_hash=chunk.content_hash,
                status="current",
                created_by=actor_id,
            )
            db.add(row)
            db.flush()
        result.append(row)
    return result


def evidence_batches(
    evidence: list[WritingEvidence],
    *,
    target_chars: int = 8_000,
    max_items: int = 12,
) -> list[list[WritingEvidence]]:
    target_chars = max(500, min(int(target_chars), 40_000))
    max_items = max(1, min(int(max_items), 50))
    batches: list[list[WritingEvidence]] = []
    pending: list[WritingEvidence] = []
    pending_chars = 0
    for row in evidence:
        if pending and (len(pending) >= max_items or pending_chars + len(row.text) > target_chars):
            batches.append(pending)
            pending = []
            pending_chars = 0
        pending.append(row)
        pending_chars += len(row.text)
    if pending:
        batches.append(pending)
    return batches


def _next_fact_version(db: Session, space_id: str, fact_key: str) -> int:
    return int(db.scalar(select(func.max(WritingFact.version)).where(
        WritingFact.space_id == space_id,
        WritingFact.fact_key == fact_key,
    )) or 0) + 1


def _candidate_by_name(
    candidates: Iterable[WritingEntityCandidate], value: str,
) -> WritingEntityCandidate | None:
    wanted = normalized_name(value)
    return next((item for item in candidates if item.normalized_name == wanted), None)


def _fact_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"value": value}


def detect_writing_fact_conflicts(
    db: Session,
    *,
    space_id: str,
    fact_keys: Iterable[str],
) -> int:
    """Mark contradictory candidates without invalidating an accepted fact.

    Facts with the same business key share subject, predicate, time and scope.
    A different value/unit therefore needs a human decision.  A previously
    verified value remains the current authority; only new, unverified rows are
    moved to ``conflicted`` so background extraction cannot silently revoke an
    accepted fact.
    """

    conflicted = 0
    for fact_key in sorted({str(value) for value in fact_keys if value}):
        rows = list(db.scalars(select(WritingFact).where(
            WritingFact.space_id == space_id,
            WritingFact.fact_key == fact_key,
            WritingFact.verification_status.notin_({"rejected", "superseded", "stale"}),
            WritingFact.deleted_at.is_(None),
        )))
        signatures = {
            canonical_json({
                "value": row.object_value,
                "value_type": row.value_type,
                "unit": row.unit,
            })
            for row in rows
        }
        if len(signatures) < 2:
            continue
        for row in rows:
            if row.verification_status == "verified":
                continue
            if row.verification_status != "conflicted":
                row.verification_status = "conflicted"
                conflicted += 1
            if row.claim_ids:
                claims = list(db.scalars(select(WritingClaim).where(
                    WritingClaim.id.in_(row.claim_ids),
                    WritingClaim.deleted_at.is_(None),
                )))
                for claim in claims:
                    claim.conflict_status = "conflicted"
                    if claim.verification_status == "candidate":
                        claim.verification_status = "conflicted"
    return conflicted


def persist_joint_extraction(
    db: Session,
    *,
    run: WritingExtractionRun,
    result: JointWritingExtraction,
) -> dict[str, int]:
    """Persist only candidate knowledge; this function never verifies it."""

    entities: list[WritingEntityCandidate] = []
    for item in result.entities:
        key = candidate_key(item.mention_text, item.entity_type, item.evidence_ids)
        row = WritingEntityCandidate(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            extraction_run_id=run.id,
            candidate_key=key,
            entity_type=item.entity_type,
            canonical_name=item.canonical_name,
            normalized_name=normalized_name(item.canonical_name),
            mention_text=item.mention_text,
            aliases=sorted({alias.strip() for alias in item.aliases if alias.strip()}),
            evidence_ids=item.evidence_ids,
            confidence=item.confidence,
            normalization_status="needs_confirmation" if item.needs_confirmation else "candidate",
            verification_status="candidate",
            candidate_metadata={"needs_confirmation": item.needs_confirmation},
        )
        db.add(row)
        entities.append(row)
    db.flush()

    claims: list[WritingClaim] = []
    facts: list[WritingFact] = []
    for item in result.claims:
        key = candidate_key(
            item.subject, item.predicate, item.object_value, item.time_scope,
            item.applicable_scope, item.evidence_ids,
        )
        claim = WritingClaim(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            extraction_run_id=run.id,
            document_version_id=run.document_version_id,
            claim_key=key,
            subject={"name": item.subject},
            predicate=item.predicate,
            object_value=_fact_value(item.object_value),
            claim_type=item.claim_type,
            time_scope=item.time_scope,
            applicable_scope=item.applicable_scope,
            evidence_ids=item.evidence_ids,
            confidence=item.confidence,
            verification_status="candidate",
            conflict_status="needs_confirmation" if item.needs_confirmation else "clear",
        )
        db.add(claim)
        db.flush()
        claims.append(claim)
        fact_key = candidate_key(
            "claim-fact", normalized_name(item.subject), item.predicate,
            item.time_scope, item.applicable_scope,
        )[:160]
        fact = WritingFact(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            fact_key=fact_key,
            subject={"name": item.subject},
            subject_candidate_id=(
                _candidate_by_name(entities, item.subject).id
                if _candidate_by_name(entities, item.subject) else None
            ),
            predicate=item.predicate,
            object_value=_fact_value(item.object_value),
            object_candidate_id=(
                _candidate_by_name(entities, str(item.object_value)).id
                if item.value_type == "entity" and _candidate_by_name(entities, str(item.object_value))
                else None
            ),
            value_type=item.value_type,
            unit=item.unit,
            time_scope=item.time_scope,
            applicable_scope=item.applicable_scope,
            claim_ids=[claim.id],
            evidence_ids=item.evidence_ids,
            origin_type="claim",
            verification_status="candidate",
            version=_next_fact_version(db, run.space_id, fact_key),
        )
        db.add(fact)
        facts.append(fact)
    db.flush()

    for item in result.metrics:
        fact_key = candidate_key(
            "metric", item.name, item.time_scope, item.applicable_scope,
        )[:160]
        facts.append(WritingFact(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            fact_key=fact_key,
            subject={"name": item.applicable_scope.get("organization") or item.applicable_scope.get("region") or "当前事项"},
            predicate=item.name,
            object_value={"value": item.value, "raw_value": item.value},
            value_type=item.value_type,
            unit=item.unit,
            time_scope=item.time_scope,
            applicable_scope=item.applicable_scope,
            claim_ids=[],
            evidence_ids=item.evidence_ids,
            origin_type="metric_mention",
            verification_status="candidate",
            version=_next_fact_version(db, run.space_id, fact_key),
        ))
        db.add(facts[-1])
    db.flush()

    relations: list[WritingRelation] = []
    for item in result.relations:
        subject_candidate = _candidate_by_name(entities, item.subject)
        object_candidate = _candidate_by_name(entities, item.object)
        fact_key = candidate_key(
            "relation", normalized_name(item.subject), item.predicate,
            normalized_name(item.object), item.time_scope, item.applicable_scope,
        )[:160]
        fact = WritingFact(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            fact_key=fact_key,
            subject={"name": item.subject},
            subject_candidate_id=subject_candidate.id if subject_candidate else None,
            predicate=item.predicate,
            object_value={"value": item.object},
            object_candidate_id=object_candidate.id if object_candidate else None,
            value_type="entity",
            time_scope=item.time_scope,
            applicable_scope=item.applicable_scope,
            claim_ids=[],
            evidence_ids=item.evidence_ids,
            origin_type="relation_hint",
            verification_status="candidate",
            version=_next_fact_version(db, run.space_id, fact_key),
        )
        db.add(fact)
        db.flush()
        facts.append(fact)
        relation = WritingRelation(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            subject_candidate_id=subject_candidate.id if subject_candidate else None,
            predicate=item.predicate,
            object_candidate_id=object_candidate.id if object_candidate else None,
            fact_id=fact.id,
            claim_ids=[],
            evidence_ids=item.evidence_ids,
            verification_status="candidate",
            version=1,
        )
        db.add(relation)
        relations.append(relation)
    conflicts = detect_writing_fact_conflicts(
        db,
        space_id=run.space_id,
        fact_keys=[item.fact_key for item in facts],
    )
    return {
        "entities": len(entities),
        "claims": len(claims),
        "facts": len(facts),
        "relations": len(relations),
        "metrics": len(result.metrics),
        "ambiguities": len(result.ambiguities),
        "conflicts": conflicts,
    }


def process_writing_graph_version(
    db: Session,
    *,
    document: Document,
    version: DocumentVersion,
    model_config_id: str,
    api_key: str,
    model: str,
    base_url: str | None,
    actor_id: str | None = None,
    material_role: str = "task_data",
    generator: Callable[[str], dict[str, Any]] | None = None,
    request_parameters: dict[str, Any] | None = None,
    timeout: float = 120,
    max_retries: int = 2,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    evidence = ensure_writing_evidence(
        db, document=document, version=version, actor_id=actor_id,
    )
    totals = {
        "entities": 0,
        "claims": 0,
        "facts": 0,
        "relations": 0,
        "metrics": 0,
        "ambiguities": 0,
        "conflicts": 0,
    }
    requests = 0
    reused = 0
    for batch in evidence_batches(evidence):
        batch_key = content_hash({
            "strategy": WRITING_GRAPH_STRATEGY_VERSION,
            "schema": WRITING_GRAPH_SCHEMA_VERSION,
            "material_role": material_role,
            "evidence": [(item.id, item.content_hash) for item in batch],
        })
        run = db.scalar(select(WritingExtractionRun).where(
            WritingExtractionRun.document_version_id == version.id,
            WritingExtractionRun.batch_key == batch_key,
            WritingExtractionRun.strategy_version == WRITING_GRAPH_STRATEGY_VERSION,
            WritingExtractionRun.deleted_at.is_(None),
        ))
        if run and run.status == "succeeded":
            reused += 1
            for key in totals:
                totals[key] += int((run.metrics or {}).get(key) or 0)
            continue
        if run is None:
            run = WritingExtractionRun(
                tenant_id=version.tenant_id,
                space_id=document.space_id,
                document_version_id=version.id,
                model_config_id=model_config_id,
                strategy_version=WRITING_GRAPH_STRATEGY_VERSION,
                prompt_schema_version=WRITING_GRAPH_SCHEMA_VERSION,
                batch_key=batch_key,
                evidence_ids=[item.id for item in batch],
            )
            db.add(run)
            db.flush()
        run.status = "running"
        run.started_at = utcnow()
        requests += 1
        try:
            # Candidate rows from one model response are atomic.  A schema,
            # constraint or persistence failure rolls the batch back while the
            # extraction run itself remains available for diagnostics/retry.
            with db.begin_nested():
                extracted = extract_writing_knowledge(
                    [WritingEvidenceInput(
                        evidence_id=item.id,
                        text=item.text,
                        locator=item.locator,
                    ) for item in batch],
                    material_role=material_role,
                    api_key=api_key,
                    model=model,
                    base_url=base_url,
                    timeout=timeout,
                    max_retries=max_retries,
                    max_tokens=max_tokens,
                    request_parameters=request_parameters,
                    generator=generator,
                )
                metrics = persist_joint_extraction(db, run=run, result=extracted)
            run.status = "succeeded"
            run.metrics = metrics
            run.finished_at = utcnow()
            for key in totals:
                totals[key] += int(metrics.get(key) or 0)
        except Exception as exc:
            run.status = "failed"
            run.error_code = "WRITING_EXTRACTION_FAILED"
            run.error_message = str(exc)[:2000]
            run.finished_at = utcnow()
            raise
    return {
        "evidence": len(evidence),
        **totals,
        "model_requests": requests,
        "reused_batches": reused,
        "material_role": material_role,
        "strategy_version": WRITING_GRAPH_STRATEGY_VERSION,
    }


def _snapshot(row: Any) -> dict[str, Any]:
    values = {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in {"deleted_at"}
    }
    return json.loads(json.dumps(values, ensure_ascii=False, default=str))


def govern_writing_object(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    target_type: str,
    target_id: str,
    action: str,
    actor_id: str,
    reason: str,
    changes: dict[str, Any] | None = None,
) -> tuple[Any, WritingGovernanceAction]:
    models = {
        "entity": WritingEntityCandidate,
        "claim": WritingClaim,
        "fact": WritingFact,
        "relation": WritingRelation,
        "evidence": WritingEvidence,
    }
    model = models.get(target_type)
    if model is None or action not in {"accept", "modify_accept", "reject", "supersede"}:
        raise ValueError("不支持的写作知识治理操作")
    row = db.get(model, target_id)
    if row is None or row.deleted_at is not None or row.tenant_id != tenant_id or row.space_id != space_id:
        raise ValueError("写作知识对象不存在")
    before = _snapshot(row)
    allowed_changes = {
        "entity": {"entity_type", "canonical_name", "normalized_name", "aliases"},
        "claim": {"subject", "predicate", "object_value", "claim_type", "time_scope", "applicable_scope"},
        "fact": {"subject", "predicate", "object_value", "value_type", "unit", "time_scope", "applicable_scope", "subject_candidate_id", "object_candidate_id"},
        "relation": {"subject_candidate_id", "predicate", "object_candidate_id", "valid_from", "valid_to"},
        "evidence": set(),
    }[target_type]
    for key, value in (changes or {}).items():
        if key not in allowed_changes:
            raise ValueError(f"字段不可治理：{key}")
        setattr(row, key, value)
    if target_type == "entity" and changes and "canonical_name" in changes:
        row.normalized_name = normalized_name(row.canonical_name)
    status = {
        "accept": "verified", "modify_accept": "verified",
        "reject": "rejected", "supersede": "superseded",
    }[action]
    if target_type == "evidence":
        row.status = "current" if status == "verified" else status
    else:
        row.verification_status = status
    if isinstance(row, WritingFact) and status == "verified":
        if not row.evidence_ids and row.origin_type not in {"computation", "structured_query"}:
            raise ValueError("事实缺少来源依据，不能确认")
        if row.claim_ids:
            verified_claims = int(db.scalar(select(func.count()).select_from(WritingClaim).where(
                WritingClaim.id.in_(row.claim_ids),
                WritingClaim.tenant_id == tenant_id,
                WritingClaim.space_id == space_id,
                WritingClaim.verification_status == "verified",
                WritingClaim.deleted_at.is_(None),
            )) or 0)
            if verified_claims != len(set(row.claim_ids)):
                raise ValueError("事实引用的陈述尚未全部确认")
        row.verified_by = actor_id
        row.verified_at = utcnow()
    if isinstance(row, WritingRelation) and status == "verified":
        fact = db.get(WritingFact, row.fact_id)
        subject = db.get(WritingEntityCandidate, row.subject_candidate_id) if row.subject_candidate_id else None
        obj = db.get(WritingEntityCandidate, row.object_candidate_id) if row.object_candidate_id else None
        if fact is None or fact.verification_status != "verified":
            raise ValueError("关系对应事实尚未确认")
        if not subject or subject.verification_status != "verified" or not obj or obj.verification_status != "verified":
            raise ValueError("关系两端对象尚未确认")
    after = _snapshot(row)
    record = WritingGovernanceAction(
        tenant_id=tenant_id,
        space_id=space_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
        before_value=before,
        after_value=after,
        reason=reason,
        impact={},
        actor_id=actor_id,
    )
    db.add(record)
    db.flush()
    return row, record


def publish_writing_graph(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    actor_id: str,
) -> WritingGraphRelease:
    entities = list(db.scalars(select(WritingEntityCandidate).where(
        WritingEntityCandidate.tenant_id == tenant_id,
        WritingEntityCandidate.space_id == space_id,
        WritingEntityCandidate.verification_status == "verified",
        WritingEntityCandidate.deleted_at.is_(None),
    )))
    claims = list(db.scalars(select(WritingClaim).where(
        WritingClaim.tenant_id == tenant_id,
        WritingClaim.space_id == space_id,
        WritingClaim.verification_status == "verified",
        WritingClaim.deleted_at.is_(None),
    )))
    facts = list(db.scalars(select(WritingFact).where(
        WritingFact.tenant_id == tenant_id,
        WritingFact.space_id == space_id,
        WritingFact.verification_status == "verified",
        WritingFact.superseded_by.is_(None),
        WritingFact.deleted_at.is_(None),
    )))
    relations = list(db.scalars(select(WritingRelation).where(
        WritingRelation.tenant_id == tenant_id,
        WritingRelation.space_id == space_id,
        WritingRelation.verification_status == "verified",
        WritingRelation.deleted_at.is_(None),
    )))
    evidence_ids = sorted({str(value) for fact in facts for value in (fact.evidence_ids or [])})
    evidence = list(db.scalars(select(WritingEvidence).where(
        WritingEvidence.id.in_(evidence_ids),
        WritingEvidence.tenant_id == tenant_id,
        WritingEvidence.space_id == space_id,
        WritingEvidence.status == "current",
        WritingEvidence.deleted_at.is_(None),
    ))) if evidence_ids else []
    if facts and len(evidence) != len(evidence_ids):
        raise ValueError("部分已确认事实的来源已失效，不能发布写作图谱")
    if not facts:
        raise ValueError("当前空间没有可发布的已确认事实")
    objects: list[tuple[str, Any, int]] = []
    objects.extend(("evidence", row, 1) for row in evidence)
    objects.extend(("entity", row, 1) for row in entities)
    objects.extend(("claim", row, 1) for row in claims)
    objects.extend(("fact", row, row.version) for row in facts)
    objects.extend(("relation", row, row.version) for row in relations)
    manifest = [
        {"type": kind, "id": row.id, "version": version, "hash": content_hash(_snapshot(row))}
        for kind, row, version in objects
    ]
    checksum = content_hash(sorted(manifest, key=lambda item: (item["type"], item["id"], item["version"])))
    previous = db.scalar(select(WritingGraphRelease).where(
        WritingGraphRelease.tenant_id == tenant_id,
        WritingGraphRelease.space_id == space_id,
        WritingGraphRelease.status == "published",
        WritingGraphRelease.deleted_at.is_(None),
    ).order_by(WritingGraphRelease.release_number.desc()).limit(1))
    if previous and previous.checksum == checksum:
        return previous
    if previous:
        previous.status = "superseded"
    number = int(db.scalar(select(func.max(WritingGraphRelease.release_number)).where(
        WritingGraphRelease.space_id == space_id,
    )) or 0) + 1
    release = WritingGraphRelease(
        tenant_id=tenant_id,
        space_id=space_id,
        release_number=number,
        graph_name=f"writing_{space_id.replace('-', '')[:12]}_{number}",
        evidence_count=len(evidence),
        entity_count=len(entities),
        claim_count=len(claims),
        fact_count=len(facts),
        relation_count=len(relations),
        checksum=checksum,
        validation_report={
            "valid": True,
            "missing_evidence": 0,
            "unverified_relations": 0,
            "object_count": len(objects),
        },
        status="published",
        created_by=actor_id,
        published_at=utcnow(),
    )
    db.add(release)
    db.flush()
    for kind, row, version in objects:
        snapshot = _snapshot(row)
        db.add(WritingGraphReleaseItem(
            tenant_id=tenant_id,
            release_id=release.id,
            object_type=kind,
            object_id=row.id,
            object_version=version,
            content_hash=content_hash(snapshot),
            snapshot=snapshot,
        ))
    return release


def writing_graph_release_payload(db: Session, release: WritingGraphRelease) -> dict[str, Any]:
    items = list(db.scalars(select(WritingGraphReleaseItem).where(
        WritingGraphReleaseItem.release_id == release.id,
        WritingGraphReleaseItem.deleted_at.is_(None),
    )))
    grouped: dict[str, list[dict[str, Any]]] = {
        "evidence": [], "entity": [], "claim": [], "fact": [], "relation": [],
    }
    for item in items:
        grouped.setdefault(item.object_type, []).append(item.snapshot)
    return {
        "id": release.id,
        "space_id": release.space_id,
        "release_number": release.release_number,
        "graph_name": release.graph_name,
        "status": release.status,
        "checksum": release.checksum,
        "published_at": release.published_at,
        "counts": {
            "evidence": release.evidence_count,
            "entities": release.entity_count,
            "claims": release.claim_count,
            "facts": release.fact_count,
            "relations": release.relation_count,
        },
        "items": grouped,
        "validation_report": release.validation_report,
    }
