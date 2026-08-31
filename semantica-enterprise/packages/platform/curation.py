from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.semantica_adapter.provenance import track_curation_decision

from .config import get_settings
from .models import (
    CanonicalEntity,
    Chunk,
    ContentElement,
    ConflictCase,
    CurationBatch,
    CurationCase,
    CurationDecision,
    CurationOverlay,
    Document,
    DocumentProfile,
    DocumentVersion,
    Fact,
    InferenceEvidence,
    InferredFact,
    User,
)


PROFILE_EDITABLE_FIELDS = {
    "summary", "classification", "document_type", "tags", "keywords",
    "main_objects", "time_range",
}
ENTITY_EDITABLE_FIELDS = {"canonical_name", "entity_type", "aliases", "properties", "confidence", "status"}
FACT_EDITABLE_FIELDS = {
    "subject_entity_id", "predicate", "object_entity_id", "object_value",
    "confidence", "status", "valid_from", "valid_to",
}
SUPPORTED_TARGETS = {
    "document_profile", "content_element", "chunk", "entity", "fact",
    "entity_pair", "quality_issue",
}
SUPPORTED_OPERATIONS = {
    "accept", "override", "reject", "suppress", "restore", "merge", "split",
    "must_link", "cannot_link", "lock", "unlock", "rollback", "resolve", "ignore",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


def _active_overlay_query(tenant_id: str, space_id: str):
    return select(CurationOverlay).where(
        CurationOverlay.tenant_id == tenant_id,
        CurationOverlay.space_id == space_id,
        CurationOverlay.status == "active",
        CurationOverlay.deleted_at.is_(None),
    )


@dataclass(frozen=True)
class TargetContext:
    tenant_id: str
    space_id: str
    target_type: str
    target_id: str
    document_id: str | None
    version_id: str | None
    fingerprint: str
    automatic_value: Any


@dataclass
class EffectiveElement:
    id: str
    tenant_id: str
    space_id: str
    document_id: str
    version_id: str
    element_id: str
    element_type: str
    ordinal: int
    text: str
    structural_path: str
    page_number: int | None
    bbox: list | None
    element_metadata: dict[str, Any]
    scope_tokens: list[str]


def resolve_target(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    target_type: str,
    target_id: str,
    version_id: str | None,
    field_path: str,
) -> TargetContext:
    if target_type not in SUPPORTED_TARGETS:
        raise ValueError("不支持的治理对象")
    document_id: str | None = None
    actual_version_id = version_id
    automatic: Any = None
    fingerprint_payload: Any
    if target_type == "document_profile":
        version = db.get(DocumentVersion, target_id)
        profile = db.scalar(select(DocumentProfile).where(DocumentProfile.version_id == target_id))
        document = db.get(Document, version.document_id) if version else None
        if version is None or document is None or document.tenant_id != tenant_id or document.space_id != space_id:
            raise ValueError("文档画像不存在")
        if field_path not in PROFILE_EDITABLE_FIELDS:
            raise ValueError("该画像字段属于自动指标，不能直接修改")
        document_id, actual_version_id = document.id, version.id
        automatic = getattr(profile, field_path, None) if profile else None
        fingerprint_payload = {"version_sha256": version.sha256, "field": field_path}
    elif target_type == "content_element":
        query = select(ContentElement).where(
            ContentElement.tenant_id == tenant_id,
            ContentElement.space_id == space_id,
            ContentElement.element_id == target_id,
            ContentElement.deleted_at.is_(None),
        )
        if version_id:
            query = query.where(ContentElement.version_id == version_id)
        row = db.scalar(query.order_by(ContentElement.created_at.desc()).limit(1))
        if row is None or field_path not in {"text", "status"}:
            raise ValueError("内容元素不存在或字段不可治理")
        document_id, actual_version_id = row.document_id, row.version_id
        automatic = row.text if field_path == "text" else "active"
        fingerprint_payload = {"element_id": row.element_id, "text": row.text}
    elif target_type == "chunk":
        row = db.get(Chunk, target_id)
        if row is None:
            row = db.scalar(select(Chunk).where(Chunk.chunk_id == target_id, Chunk.version_id == version_id))
        if row is None or row.tenant_id != tenant_id or row.space_id != space_id or field_path not in {"text", "status", "boost"}:
            raise ValueError("知识片段不存在或字段不可治理")
        target_id = row.chunk_id
        document_id, actual_version_id = row.document_id, row.version_id
        automatic = row.text if field_path == "text" else ("active" if field_path == "status" else 1.0)
        fingerprint_payload = {"chunk_id": row.chunk_id, "content_hash": row.content_hash}
    elif target_type == "entity":
        row = db.get(CanonicalEntity, target_id)
        if row is None or row.tenant_id != tenant_id or row.space_id != space_id or field_path not in ENTITY_EDITABLE_FIELDS:
            raise ValueError("知识实体不存在或字段不可治理")
        automatic = getattr(row, field_path)
        fingerprint_payload = {"id": row.id, "normalized_name": row.normalized_name, "type": row.entity_type}
    elif target_type == "fact":
        row = db.get(Fact, target_id)
        if row is None or row.tenant_id != tenant_id or row.space_id != space_id or field_path not in FACT_EDITABLE_FIELDS:
            raise ValueError("知识事实不存在或字段不可治理")
        automatic = getattr(row, field_path)
        source = db.get(Chunk, row.source_chunk_id) if row.source_chunk_id else None
        fingerprint_payload = {
            "id": row.id,
            "subject": row.subject_entity_id,
            "predicate": row.predicate,
            "object": row.object_entity_id or row.object_value,
            "source_chunk": source.chunk_id if source else None,
        }
    elif target_type == "quality_issue":
        version = db.get(DocumentVersion, version_id or target_id)
        document = db.get(Document, version.document_id) if version else None
        if version is None or document is None or document.tenant_id != tenant_id or document.space_id != space_id:
            raise ValueError("质量问题来源不存在")
        document_id, actual_version_id = document.id, version.id
        automatic = "open"
        fingerprint_payload = {"version_sha256": version.sha256, "issue": target_id}
    else:
        automatic = None
        fingerprint_payload = {"target": target_id, "field": field_path}
    return TargetContext(
        tenant_id=tenant_id,
        space_id=space_id,
        target_type=target_type,
        target_id=target_id,
        document_id=document_id,
        version_id=actual_version_id,
        fingerprint=stable_fingerprint(fingerprint_payload),
        automatic_value=automatic,
    )


def validate_decision(target_type: str, operation: str, field_path: str, value: Any) -> None:
    if target_type not in SUPPORTED_TARGETS or operation not in SUPPORTED_OPERATIONS:
        raise ValueError("不支持的治理操作")
    nullable_fact_fields = {"object_entity_id", "object_value", "valid_from", "valid_to"}
    if operation == "override" and value is None and not (
        target_type == "fact" and field_path in nullable_fact_fields
    ):
        raise ValueError("修正值不能为空")
    if target_type == "document_profile" and field_path not in PROFILE_EDITABLE_FIELDS:
        raise ValueError("该画像字段不能人工修改")
    if target_type == "entity_pair" and operation not in {"must_link", "cannot_link", "merge", "split"}:
        raise ValueError("实体组合仅支持合并、拆分或合并约束")


def create_decision(
    db: Session,
    *,
    user: User,
    space_id: str,
    target_type: str,
    target_id: str,
    field_path: str,
    operation: str,
    value: Any,
    version_id: str | None = None,
    scope: str = "version_only",
    reason_code: str = "manual_correction",
    reason_note: str = "",
    base_fingerprint: str | None = None,
    batch_id: str | None = None,
) -> tuple[CurationDecision, CurationBatch, TargetContext]:
    validate_decision(target_type, operation, field_path, value)
    if scope not in {"version_only", "document_future", "space"}:
        raise ValueError("治理作用范围不合法")
    context = resolve_target(
        db,
        tenant_id=user.tenant_id,
        space_id=space_id,
        target_type=target_type,
        target_id=target_id,
        version_id=version_id,
        field_path=field_path,
    )
    if base_fingerprint and base_fingerprint != context.fingerprint:
        raise ValueError("自动结果已发生变化，请刷新后重新治理")
    overlay = db.scalar(
        select(CurationOverlay).where(
            CurationOverlay.tenant_id == user.tenant_id,
            CurationOverlay.space_id == space_id,
            CurationOverlay.deleted_at.is_(None),
            CurationOverlay.target_type == target_type,
            CurationOverlay.target_id == context.target_id,
            CurationOverlay.field_path == field_path,
        )
    )
    previous = db.get(CurationDecision, overlay.decision_id) if overlay and overlay.status == "active" else None
    before_value = _json_value(overlay.effective_value if previous else context.automatic_value)
    value = _json_value(value)
    batch = db.get(CurationBatch, batch_id) if batch_id else None
    if batch is None:
        batch = CurationBatch(
            tenant_id=user.tenant_id,
            space_id=space_id,
            name=f"{target_type} · {operation}",
            created_by=user.id,
        )
        db.add(batch)
        db.flush()
    if batch.tenant_id != user.tenant_id or batch.space_id != space_id:
        raise ValueError("治理批次不属于当前知识空间")
    if previous:
        previous.status = "superseded"
    decision = CurationDecision(
        tenant_id=user.tenant_id,
        space_id=space_id,
        batch_id=batch.id,
        document_id=context.document_id,
        version_id=context.version_id,
        target_type=target_type,
        target_id=context.target_id,
        field_path=field_path,
        operation=operation,
        before_value=before_value,
        after_value=value,
        reason_code=reason_code,
        reason_note=reason_note,
        base_fingerprint=context.fingerprint,
        scope=scope,
        created_by=user.id,
        supersedes_id=previous.id if previous else None,
    )
    db.add(decision)
    db.flush()
    effective_value = value
    if operation in {"reject", "suppress"}:
        effective_value = "suppressed"
    elif operation in {"accept", "restore", "unlock"}:
        effective_value = context.automatic_value
    if overlay is None:
        overlay = CurationOverlay(
            tenant_id=user.tenant_id,
            space_id=space_id,
            decision_id=decision.id,
            target_type=target_type,
            target_id=context.target_id,
            field_path=field_path,
            operation=operation,
            effective_value=effective_value,
            base_fingerprint=context.fingerprint,
            scope=scope,
        )
        db.add(overlay)
    else:
        overlay.decision_id = decision.id
        overlay.operation = operation
        overlay.effective_value = effective_value
        overlay.base_fingerprint = context.fingerprint
        overlay.scope = scope
        overlay.status = "active"
    track_curation_decision(
        get_settings().provenance_storage_path,
        decision_id=decision.id,
        target_id=context.target_id,
        target_type=target_type,
        user_id=user.id,
        batch_id=batch.id,
        operation=operation,
        before_value=before_value,
        after_value=value,
        source_fingerprint=context.fingerprint,
        supersedes_id=previous.id if previous else None,
    )
    return decision, batch, context


def rollback_decision(db: Session, *, user: User, decision: CurationDecision) -> CurationOverlay | None:
    if decision.tenant_id != user.tenant_id or decision.status not in {"active", "superseded"}:
        raise ValueError("治理决定不可回滚")
    overlay = db.scalar(
        _active_overlay_query(user.tenant_id, decision.space_id).where(
            CurationOverlay.target_type == decision.target_type,
            CurationOverlay.target_id == decision.target_id,
            CurationOverlay.field_path == decision.field_path,
        )
    )
    if overlay is None or overlay.decision_id != decision.id:
        raise ValueError("只能回滚当前生效的治理决定")
    decision.status = "rolled_back"
    previous = db.get(CurationDecision, decision.supersedes_id) if decision.supersedes_id else None
    if previous and previous.deleted_at is None:
        previous.status = "active"
        overlay.decision_id = previous.id
        overlay.operation = previous.operation
        overlay.effective_value = previous.after_value
        overlay.base_fingerprint = previous.base_fingerprint
        overlay.scope = previous.scope
        return overlay
    overlay.status = "rolled_back"
    return None


def _decision_applies(decision: CurationDecision | None, version_id: str | None) -> bool:
    if decision is None or decision.status != "active":
        return False
    if decision.scope in {"space", "document_future"}:
        return True
    return not version_id or decision.version_id == version_id


def overlays_for_targets(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    target_type: str,
    target_ids: Iterable[str],
) -> dict[tuple[str, str], tuple[CurationOverlay, CurationDecision]]:
    ids = set(target_ids)
    if not ids:
        return {}
    rows = list(
        db.scalars(
            _active_overlay_query(tenant_id, space_id).where(
                CurationOverlay.target_type == target_type,
                CurationOverlay.target_id.in_(ids),
            )
        )
    )
    output: dict[tuple[str, str], tuple[CurationOverlay, CurationDecision]] = {}
    for overlay in rows:
        decision = db.get(CurationDecision, overlay.decision_id)
        if decision:
            output[(overlay.target_id, overlay.field_path)] = (overlay, decision)
    return output


def effective_profile(db: Session, version: DocumentVersion) -> dict[str, Any]:
    profile = db.scalar(select(DocumentProfile).where(DocumentProfile.version_id == version.id))
    if profile is None:
        raise ValueError("治理画像尚未生成")
    automatic = {field: getattr(profile, field) for field in PROFILE_EDITABLE_FIELDS}
    effective = dict(automatic)
    origins = {field: "automatic" for field in PROFILE_EDITABLE_FIELDS}
    decisions: list[dict[str, Any]] = []
    rows = db.scalars(
        _active_overlay_query(version.tenant_id, profile.space_id).where(
            CurationOverlay.target_type == "document_profile",
        )
    )
    for overlay in rows:
        decision = db.get(CurationDecision, overlay.decision_id)
        if decision is None or decision.document_id != version.document_id:
            continue
        if overlay.target_id != version.id and decision.scope != "document_future":
            continue
        if not _decision_applies(decision, version.id):
            continue
        if overlay.field_path in effective:
            effective[overlay.field_path] = overlay.effective_value
            origins[overlay.field_path] = "manual_inherited" if overlay.target_id != version.id else "manual"
            decisions.append({
                "id": decision.id,
                "field_path": decision.field_path,
                "operation": decision.operation,
                "scope": decision.scope,
                "created_by": decision.created_by,
                "created_at": decision.created_at,
                "inherited": overlay.target_id != version.id,
            })
    computed_fields = {
        "id", "tenant_id", "space_id", "document_id", "version_id", "language",
        "quality_score", "completeness_score", "readability_score", "structure_score",
        "media_confidence", "duplicate_ratio", "quality_issues", "recommended_actions",
        "deterministic_metrics", "policy_id", "policy_version", "model_config_id",
        "model_status", "model_error", "generated_at", "created_at", "updated_at",
    }
    payload = {field: getattr(profile, field) for field in computed_fields}
    payload.update(effective)
    payload.update({
        "automatic": automatic,
        "effective": effective,
        "field_origins": origins,
        "active_decisions": decisions,
        "base_fingerprint": stable_fingerprint({"version_sha256": version.sha256}),
    })
    return payload


def effective_elements(db: Session, rows: list[ContentElement], version_id: str) -> list[EffectiveElement]:
    if not rows:
        return []
    mapping = overlays_for_targets(
        db,
        tenant_id=rows[0].tenant_id,
        space_id=rows[0].space_id,
        target_type="content_element",
        target_ids=[row.element_id for row in rows],
    )
    output: list[EffectiveElement] = []
    for row in rows:
        status_entry = mapping.get((row.element_id, "status"))
        if status_entry and _decision_applies(status_entry[1], version_id) and status_entry[0].effective_value == "suppressed":
            continue
        text_entry = mapping.get((row.element_id, "text"))
        text = row.text
        metadata = dict(row.element_metadata or {})
        if text_entry and _decision_applies(text_entry[1], version_id):
            text = str(text_entry[0].effective_value or "")
            metadata = {
                **metadata,
                "curation": {"decision_id": text_entry[1].id, "origin": "manual"},
            }
        output.append(EffectiveElement(
            id=row.id,
            tenant_id=row.tenant_id,
            space_id=row.space_id,
            document_id=row.document_id,
            version_id=row.version_id,
            element_id=row.element_id,
            element_type=row.element_type,
            ordinal=row.ordinal,
            text=text,
            structural_path=row.structural_path,
            page_number=row.page_number,
            bbox=row.bbox,
            element_metadata=metadata,
            scope_tokens=list(row.scope_tokens or []),
        ))
    return output


def effective_chunk_payloads(
    db: Session,
    rows: list[Chunk],
    *,
    include_superseded: bool = False,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    mapping = overlays_for_targets(
        db,
        tenant_id=rows[0].tenant_id,
        space_id=rows[0].space_id,
        target_type="chunk",
        target_ids=[row.chunk_id for row in rows],
    )
    output: list[dict[str, Any]] = []
    for row in rows:
        allowed_statuses = {"staged", "published", "superseded"} if include_superseded else {"staged", "published"}
        if row.status not in allowed_statuses or row.deleted_at is not None:
            continue
        status_entry = mapping.get((row.chunk_id, "status"))
        if status_entry and _decision_applies(status_entry[1], row.version_id) and status_entry[0].effective_value == "suppressed":
            continue
        text_entry = mapping.get((row.chunk_id, "text"))
        boost_entry = mapping.get((row.chunk_id, "boost"))
        text = row.text
        decision_id = None
        if text_entry and _decision_applies(text_entry[1], row.version_id):
            text = str(text_entry[0].effective_value or "")
            decision_id = text_entry[1].id
        boost = 1.0
        if boost_entry and _decision_applies(boost_entry[1], row.version_id):
            try:
                boost = max(0.1, min(5.0, float(boost_entry[0].effective_value)))
                decision_id = decision_id or boost_entry[1].id
            except (TypeError, ValueError):
                boost = 1.0
        effective_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        output.append({
            "row": row,
            "text": text,
            "effective_hash": effective_hash,
            "boost": boost,
            "curation_decision_id": decision_id,
        })
    return output


def effective_chunk_text(
    db: Session,
    row: Chunk,
    *,
    include_superseded: bool = False,
) -> tuple[str, dict[str, Any]]:
    result = effective_chunk_payloads(db, [row], include_superseded=include_superseded)
    if not result:
        raise ValueError("知识片段已被人工屏蔽")
    item = result[0]
    return item["text"], {
        "effective_hash": item["effective_hash"],
        "boost": item["boost"],
        "curation_decision_id": item["curation_decision_id"],
    }


def effective_entity(db: Session, row: CanonicalEntity) -> dict[str, Any]:
    values = {
        "canonical_name": row.canonical_name,
        "entity_type": row.entity_type,
        "aliases": row.aliases or [],
        "properties": row.properties or {},
        "confidence": row.confidence,
        "status": row.status,
    }
    mapping = overlays_for_targets(
        db, tenant_id=row.tenant_id, space_id=row.space_id,
        target_type="entity", target_ids=[row.id],
    )
    origins = {field: "automatic" for field in values}
    for field in values:
        entry = mapping.get((row.id, field))
        if entry and _decision_applies(entry[1], None):
            values[field] = entry[0].effective_value
            origins[field] = "manual"
    return {**values, "field_origins": origins}


def effective_fact(db: Session, row: Fact) -> dict[str, Any]:
    values = {field: getattr(row, field) for field in FACT_EDITABLE_FIELDS}
    mapping = overlays_for_targets(
        db, tenant_id=row.tenant_id, space_id=row.space_id,
        target_type="fact", target_ids=[row.id],
    )
    origins = {field: "automatic" for field in values}
    for field in values:
        entry = mapping.get((row.id, field))
        if entry and _decision_applies(entry[1], None):
            values[field] = entry[0].effective_value
            origins[field] = "manual"
    return {**values, "field_origins": origins}


def entity_pair_constraints(db: Session, tenant_id: str, space_id: str) -> dict[str, list[dict[str, Any]]]:
    output = {"must_link": [], "cannot_link": []}
    rows = db.scalars(
        _active_overlay_query(tenant_id, space_id).where(CurationOverlay.target_type == "entity_pair")
    )
    for overlay in rows:
        decision = db.get(CurationDecision, overlay.decision_id)
        if decision and decision.operation in output and isinstance(overlay.effective_value, dict):
            output[decision.operation].append(dict(overlay.effective_value))
    return output


def constrained_canonical_entity(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    normalized_name: str,
    entity_type: str,
) -> CanonicalEntity | None:
    """Resolve a persisted must-link winner without changing Semantica output.

    Entity-pair constraints are applied both while Semantica groups mentions
    and when the platform maps the normalized group to a canonical row.  This
    second lookup is what prevents a later reprocessing run from recreating a
    human-merged loser as a new canonical entity.
    """
    wanted_name = normalized_name.strip().casefold()
    wanted_type = entity_type.strip()
    for item in entity_pair_constraints(db, tenant_id, space_id).get("must_link", []):
        names = {
            str(item.get("left_name") or "").strip().casefold(),
            str(item.get("right_name") or "").strip().casefold(),
        }
        types = {
            str(item.get("left_type") or "").strip(),
            str(item.get("right_type") or "").strip(),
        }
        if wanted_name not in names or (types - {""} and wanted_type not in types):
            continue
        winner = db.get(CanonicalEntity, item.get("winner_id"))
        if (
            winner is not None
            and winner.tenant_id == tenant_id
            and winner.space_id == space_id
            and winner.deleted_at is None
        ):
            return winner
    return None


def upsert_profile_cases(db: Session, profile: DocumentProfile) -> int:
    count = 0
    for issue in profile.quality_issues or []:
        fingerprint = stable_fingerprint({"version": profile.version_id, "issue": issue})
        existing = db.scalar(select(CurationCase).where(CurationCase.tenant_id == profile.tenant_id, CurationCase.fingerprint == fingerprint))
        if existing:
            if existing.status == "stale":
                existing.status = "open"
            continue
        text = str(issue)
        severity = "high" if any(term in text for term in ("没有可用正文", "未生成转写", "可信度偏低")) else "medium"
        db.add(CurationCase(
            tenant_id=profile.tenant_id,
            space_id=profile.space_id,
            document_id=profile.document_id,
            version_id=profile.version_id,
            target_type="document_profile",
            target_id=profile.version_id,
            case_type="quality_issue",
            severity=severity,
            title=text[:500],
            reason=text,
            evidence={"quality_score": profile.quality_score, "generated_at": profile.generated_at.isoformat()},
            fingerprint=fingerprint,
        ))
        count += 1
    return count


def upsert_conflict_cases(db: Session, tenant_id: str, space_id: str) -> int:
    """Project Semantica conflict detection into the unified curation queue."""
    count = 0
    for conflict in db.scalars(select(ConflictCase).where(
        ConflictCase.tenant_id == tenant_id,
        ConflictCase.space_id == space_id,
        ConflictCase.deleted_at.is_(None),
    )):
        fingerprint = stable_fingerprint({
            "conflict_id": conflict.id,
            "property": conflict.property_name,
            "values": conflict.conflicting_values,
        })
        if db.scalar(select(CurationCase).where(CurationCase.tenant_id == tenant_id, CurationCase.fingerprint == fingerprint)):
            continue
        db.add(CurationCase(
            tenant_id=tenant_id,
            space_id=space_id,
            target_type="fact",
            target_id=conflict.id,
            case_type="fact_conflict",
            severity="high" if conflict.status != "resolved" else "medium",
            title=f"事实冲突 · {conflict.property_name}"[:500],
            reason=f"同一主体属性存在多个候选值，Semantica 策略：{conflict.strategy}",
            evidence={
                "conflict_id": conflict.id,
                "entity_id": conflict.entity_id,
                "values": conflict.conflicting_values,
                "source_chunk_ids": conflict.source_chunk_ids,
                "strategy": conflict.strategy,
                "automatic_status": conflict.status,
            },
            fingerprint=fingerprint,
            status="handled" if conflict.status == "resolved" else "open",
        ))
        count += 1
    return count


def invalidate_inference_for_space(db: Session, tenant_id: str, space_id: str) -> int:
    active_facts = list(db.scalars(select(InferredFact).where(
        InferredFact.tenant_id == tenant_id,
        InferredFact.space_id == space_id,
        InferredFact.status == "published",
        InferredFact.deleted_at.is_(None),
    )))
    invalidated = 0
    for inferred in active_facts:
        evidence = list(db.scalars(select(InferenceEvidence).where(InferenceEvidence.inferred_fact_id == inferred.id)))
        invalid = False
        for item in evidence:
            if item.source_fact_id:
                fact = db.get(Fact, item.source_fact_id)
                if fact is None or effective_fact(db, fact).get("status") != "published":
                    invalid = True
            if item.source_chunk_id:
                chunk = db.get(Chunk, item.source_chunk_id)
                if chunk is None or not effective_chunk_payloads(db, [chunk]):
                    invalid = True
        if invalid:
            inferred.status = "invalidated"
            inferred.invalidated_at = utcnow()
            invalidated += 1
    return invalidated
