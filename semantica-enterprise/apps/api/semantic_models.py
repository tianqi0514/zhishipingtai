from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import get_current_user, require_admin, require_space_permission
from apps.api.utils import serialize_row
from packages.platform.audit import audit
from packages.platform.curation import effective_chunk_text, effective_entities, effective_facts
from packages.platform.database import get_db
from packages.platform.models import (
    CanonicalEntity,
    Chunk,
    Document,
    EntityMention,
    Fact,
    KnowledgeSpace,
    Ontology,
    OntologySuggestion,
    OntologyTerm,
    OntologyVersion,
    User,
)


router = APIRouter(prefix="/ontologies", tags=["semantic-model-governance"])


def _active(model: type) -> Any:
    return model.deleted_at.is_(None)


def _ontology(db: Session, ontology_id: str, user: User, permission: str = "read") -> Ontology:
    row = db.get(Ontology, ontology_id)
    if row is None or row.deleted_at is not None or row.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="语义模型不存在")
    if row.space_id:
        require_space_permission(db, user, row.space_id, permission)
    return row


def _candidate_code(kind: str, label: str) -> str:
    digest = hashlib.sha256(f"{kind}:{label}".encode("utf-8")).hexdigest()[:16]
    return f"auto_{kind}_{digest}"


def _source_fingerprint(kind: str, label: str, evidence: list[dict[str, Any]]) -> str:
    canonical = {
        "kind": kind,
        "label": label,
        "evidence": sorted(evidence, key=lambda item: str(item.get("source_id") or "")),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def _evidence_payload(
    db: Session, chunk_id: str | None, source_id: str, label: str,
    *, tenant_id: str, space_id: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"source_id": source_id, "label": label}
    chunk = db.get(Chunk, chunk_id) if chunk_id else None
    if (
        chunk is None or chunk.deleted_at is not None or chunk.status != "published"
        or chunk.tenant_id != tenant_id or chunk.space_id != space_id
    ):
        return payload
    document = db.get(Document, chunk.document_id)
    if (
        document is None or document.deleted_at is not None
        or document.tenant_id != tenant_id or document.space_id != space_id
        or document.current_version_id != chunk.version_id
    ):
        return payload
    try:
        text, _ = effective_chunk_text(db, chunk)
    except ValueError:
        return payload
    payload.update(
        {
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "version_id": chunk.version_id,
            "document_title": document.title,
            "page_number": chunk.page_number,
            "structural_path": chunk.structural_path,
            "snippet": text[:240],
        }
    )
    return payload


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuggestionDecision(StrictModel):
    decision: Literal["accept", "reject"]
    label: str | None = Field(default=None, min_length=1, max_length=500)
    definition: str | None = Field(default=None, max_length=4000)
    aliases: list[str] | None = None


@router.post("/{ontology_id}/suggestions/generate")
def generate_semantic_model_suggestions(
    ontology_id: str,
    space_id: str | None = Query(default=None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Generate deterministic candidates from the current curated knowledge."""
    ontology = _ontology(db, ontology_id, admin, "manage")
    target_space_id = space_id or ontology.space_id
    if not target_space_id:
        raise HTTPException(status_code=422, detail="请先选择用于生成候选的知识空间")
    space = db.get(KnowledgeSpace, target_space_id)
    if space is None or space.deleted_at is not None or space.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404, detail="知识空间不存在")
    require_space_permission(db, admin, target_space_id, "manage")
    existing_terms = {
        (row.term_type, row.label.strip().casefold())
        for row in db.scalars(
            select(OntologyTerm).where(OntologyTerm.ontology_id == ontology.id, _active(OntologyTerm))
        )
    }
    candidates: list[dict[str, Any]] = []
    entity_rows = list(db.scalars(
        select(CanonicalEntity).where(
            CanonicalEntity.tenant_id == admin.tenant_id,
            CanonicalEntity.space_id == target_space_id,
            _active(CanonicalEntity),
        )
    ))
    entity_values = {
        entity_id: value for entity_id, value in effective_entities(db, entity_rows).items()
        if value.get("status") in {"published", "active"}
    }
    entities_by_type: dict[str, list[CanonicalEntity]] = {}
    for row in entity_rows:
        if row.id in entity_values:
            label = str(entity_values[row.id].get("entity_type") or "").strip()
            entities_by_type.setdefault(label, []).append(row)
    # Mentions retain their automatic identity after canonical overlays change.
    # Match that identity, not the old/new type alone, to avoid unrelated evidence.
    original_identities: dict[tuple[str, str], set[str]] = {}
    for row in entity_rows:
        for name in [row.normalized_name, row.canonical_name, *(row.aliases or [])]:
            key = (str(name).strip().casefold(), row.entity_type)
            original_identities.setdefault(key, set()).add(row.id)
    mentions_by_entity: dict[str, EntityMention] = {}
    for mention in db.scalars(
        select(EntityMention).where(
            EntityMention.tenant_id == admin.tenant_id,
            EntityMention.space_id == target_space_id,
            EntityMention.status == "published",
            _active(EntityMention),
        ).order_by(EntityMention.confidence.desc(), EntityMention.id)
    ):
        matching_ids = set().union(*(
            original_identities.get((str(name).strip().casefold(), mention.entity_type), set())
            for name in (mention.normalized_name, mention.text)
        ))
        if len(matching_ids) == 1:
            mentions_by_entity.setdefault(next(iter(matching_ids)), mention)
    for normalized, entities in sorted(entities_by_type.items(), key=lambda item: (-len(item[1]), item[0])):
        count = len(entities)
        if not normalized or ("class", normalized.casefold()) in existing_terms:
            continue
        evidence = []
        for item in sorted(entities, key=lambda row: (-float(entity_values[row.id].get("confidence") or 0), row.id))[:3]:
            value = entity_values[item.id]
            mention = mentions_by_entity.get(item.id)
            evidence.append({
                **_evidence_payload(
                    db, mention.chunk_id if mention else None, item.id,
                    str(value.get("canonical_name") or ""),
                    tenant_id=admin.tenant_id, space_id=target_space_id,
                ),
                "entity_id": item.id,
                "entity_type": normalized,
                "mention_id": mention.id if mention else None,
                "field_origins": value["field_origins"],
            })
        candidates.append(
            {
                "suggestion_kind": "class",
                "code": _candidate_code("class", normalized),
                "label": normalized,
                "definition": f"从当前空间 {int(count)} 个已发布知识对象中归纳出的对象类型。",
                "payload": {"term_type": "class", "aliases": [], "source": "published_entities"},
                "confidence": min(0.98, 0.7 + min(int(count), 20) / 100),
                "evidence_count": int(count),
                "evidence": evidence,
            }
        )
    fact_rows = list(db.scalars(
        select(Fact).where(
            Fact.tenant_id == admin.tenant_id,
            Fact.space_id == target_space_id,
            _active(Fact),
        )
    ))
    facts_by_predicate: dict[str, list[tuple[Fact, dict[str, Any]]]] = {}
    # Raw endpoints can refer to a merged loser while curated endpoints already
    # refer to its winner; validate only after resolving the overlays.
    fact_values = effective_facts(db, fact_rows)
    for row in fact_rows:
        value = fact_values[row.id]
        if value.get("status") != "published" or value.get("subject_entity_id") not in entity_values:
            continue
        if value.get("object_entity_id") and value["object_entity_id"] not in entity_values:
            continue
        normalized = str(value.get("predicate") or "").strip()
        facts_by_predicate.setdefault(normalized, []).append((row, value))
    for normalized, facts in sorted(facts_by_predicate.items(), key=lambda item: (-len(item[1]), item[0])):
        count = len(facts)
        if not normalized or ("relation", normalized.casefold()) in existing_terms:
            continue
        evidence = [
            {
                **_evidence_payload(
                    db, value.get("source_chunk_id"), item.id, normalized,
                    tenant_id=admin.tenant_id, space_id=target_space_id,
                ),
                "subject_entity_id": value["subject_entity_id"],
                "object_entity_id": value.get("object_entity_id"),
                "object_value": value.get("object_value"),
                "field_origins": value["field_origins"],
            }
            for item, value in sorted(facts, key=lambda pair: (-float(pair[1].get("confidence") or 0), pair[0].id))[:3]
        ]
        candidates.append(
            {
                "suggestion_kind": "relation",
                "code": _candidate_code("relation", normalized),
                "label": normalized,
                "definition": f"从当前空间 {int(count)} 条已发布知识关系中归纳出的关系类型。",
                "payload": {"term_type": "relation", "aliases": [], "source": "published_facts"},
                "confidence": min(0.98, 0.72 + min(int(count), 20) / 100),
                "evidence_count": int(count),
                "evidence": evidence,
            }
        )

    created = 0
    refreshed = 0
    candidate_keys = {(item["suggestion_kind"], item["code"]) for item in candidates}
    # Only withdraw unreviewed candidates from this generation scope. Human
    # decisions and published ontology versions remain intact.
    for row in db.scalars(select(OntologySuggestion).where(
        OntologySuggestion.tenant_id == admin.tenant_id,
        OntologySuggestion.ontology_id == ontology.id,
        OntologySuggestion.space_id == target_space_id,
        OntologySuggestion.status == "pending",
        _active(OntologySuggestion),
    )):
        if (
            (row.payload or {}).get("source") in {"published_entities", "published_facts"}
            and (row.suggestion_kind, row.code) not in candidate_keys
        ):
            row.deleted_at = datetime.now(timezone.utc)
    for candidate in candidates:
        fingerprint = _source_fingerprint(candidate["suggestion_kind"], candidate["label"], candidate["evidence"])
        row = db.scalar(
            select(OntologySuggestion).where(
                OntologySuggestion.ontology_id == ontology.id,
                OntologySuggestion.tenant_id == admin.tenant_id,
                OntologySuggestion.space_id == target_space_id,
                OntologySuggestion.suggestion_kind == candidate["suggestion_kind"],
                OntologySuggestion.code == candidate["code"],
            ).order_by(OntologySuggestion.deleted_at.is_not(None), OntologySuggestion.created_at.desc())
        )
        if row is None:
            row = OntologySuggestion(
                tenant_id=admin.tenant_id,
                ontology_id=ontology.id,
                space_id=target_space_id,
                source_fingerprint=fingerprint,
                status="pending",
                **candidate,
            )
            db.add(row)
            created += 1
        elif row.status == "pending":
            # Revive a withdrawn row after a governance rollback rather than
            # colliding with its retained unique source fingerprint.
            row.deleted_at = None
            row.source_fingerprint = fingerprint
            row.definition = candidate["definition"]
            row.payload = candidate["payload"]
            row.confidence = candidate["confidence"]
            row.evidence_count = candidate["evidence_count"]
            row.evidence = candidate["evidence"]
            refreshed += 1
    audit(
        db,
        admin.tenant_id,
        admin.id,
        "ontology.suggestions.generate",
        "ontology",
        ontology.id,
        {"space_id": target_space_id, "candidate_count": len(candidates), "created": created, "refreshed": refreshed},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="候选生成发生并发冲突，请重试") from exc
    return {
        "ontology_id": ontology.id,
        "space_id": target_space_id,
        "candidate_count": len(candidates),
        "created": created,
        "refreshed": refreshed,
        "blocking_issue": "当前空间还没有已发布的实体或关系" if not candidates else None,
    }


@router.get("/{ontology_id}/suggestions")
def list_semantic_model_suggestions(
    ontology_id: str,
    status: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ontology = _ontology(db, ontology_id, user)
    query = select(OntologySuggestion).where(
        OntologySuggestion.ontology_id == ontology.id,
        _active(OntologySuggestion),
    )
    if status:
        query = query.where(OntologySuggestion.status == status)
    return [serialize_row(row) for row in db.scalars(query.order_by(OntologySuggestion.confidence.desc()))]


@router.put("/{ontology_id}/suggestions/{suggestion_id}")
def decide_semantic_model_suggestion(
    ontology_id: str,
    suggestion_id: str,
    payload: SuggestionDecision,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ontology = _ontology(db, ontology_id, admin, "manage")
    row = db.get(OntologySuggestion, suggestion_id)
    if row is None or row.deleted_at is not None or row.ontology_id != ontology.id:
        raise HTTPException(status_code=404, detail="语义模型候选不存在")
    if row.status == "published":
        raise HTTPException(status_code=409, detail="已发布候选不能重复处理")
    if payload.label is not None:
        row.label = payload.label.strip()
    if payload.definition is not None:
        row.definition = payload.definition.strip()
    if payload.aliases is not None:
        row.payload = {**(row.payload or {}), "aliases": [item.strip() for item in payload.aliases if item.strip()]}
    row.status = "accepted" if payload.decision == "accept" else "rejected"
    row.decided_by = admin.id
    row.decided_at = datetime.now(timezone.utc)
    audit(db, admin.tenant_id, admin.id, f"ontology.suggestion.{payload.decision}", "ontology_suggestion", row.id)
    db.commit()
    return serialize_row(row)


@router.post("/{ontology_id}/validate-draft")
def validate_semantic_model_draft(
    ontology_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ontology = _ontology(db, ontology_id, admin, "manage")
    accepted = list(
        db.scalars(
            select(OntologySuggestion).where(
                OntologySuggestion.ontology_id == ontology.id,
                OntologySuggestion.status == "accepted",
                _active(OntologySuggestion),
            )
        )
    )
    active_terms = list(
        db.scalars(select(OntologyTerm).where(OntologyTerm.ontology_id == ontology.id, _active(OntologyTerm)))
    )
    known_codes = {row.code for row in active_terms}
    duplicates = sorted({row.code for row in accepted if row.code in known_codes})
    invalid = [row.id for row in accepted if not row.label.strip() or not row.code.strip()]
    return {
        "valid": not duplicates and not invalid,
        "accepted_count": len(accepted),
        "existing_term_count": len(active_terms),
        "duplicate_codes": duplicates,
        "invalid_suggestion_ids": invalid,
        "warnings": ["尚未接受任何候选，发布后模型内容不会变化"] if not accepted else [],
    }


def _publish_snapshot(db: Session, ontology: Ontology, user: User) -> OntologyVersion:
    terms = list(
        db.scalars(
            select(OntologyTerm).where(
                OntologyTerm.ontology_id == ontology.id,
                _active(OntologyTerm),
            ).order_by(OntologyTerm.term_type, OntologyTerm.code)
        )
    )
    manifest = {
        "ontology": {"code": ontology.code, "name": ontology.name, "namespace": ontology.namespace},
        "terms": [
            {
                "code": item.code,
                "label": item.label,
                "term_type": item.term_type,
                "parent_code": item.parent_code,
                "aliases": item.aliases or [],
                "definition": item.definition,
                "constraints": item.constraints or {},
                "enabled": item.enabled,
            }
            for item in terms
        ],
    }
    checksum = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    latest_snapshot = int(
        db.scalar(select(func.max(OntologyVersion.version)).where(OntologyVersion.ontology_id == ontology.id)) or 0
    )
    number = max(latest_snapshot, int(ontology.version or 0)) + 1
    row = OntologyVersion(
        tenant_id=user.tenant_id,
        ontology_id=ontology.id,
        version=number,
        manifest=manifest,
        checksum=checksum,
        status="published",
        created_by=user.id,
        published_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    ontology.version = number
    ontology.status = "published"
    ontology.config = {**(ontology.config or {}), "current_version_id": row.id, "current_checksum": checksum}
    return row


@router.post("/{ontology_id}/publish")
def publish_semantic_model_draft(
    ontology_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ontology = _ontology(db, ontology_id, admin, "manage")
    accepted = list(
        db.scalars(
            select(OntologySuggestion).where(
                OntologySuggestion.ontology_id == ontology.id,
                OntologySuggestion.status == "accepted",
                _active(OntologySuggestion),
            ).order_by(OntologySuggestion.created_at)
        )
    )
    existing = {
        row.code: row
        for row in db.scalars(select(OntologyTerm).where(OntologyTerm.ontology_id == ontology.id, _active(OntologyTerm)))
    }
    conflicts = [row.code for row in accepted if row.code in existing]
    if conflicts:
        raise HTTPException(status_code=409, detail={"message": "候选编码与现有词条重复", "codes": conflicts})
    for item in accepted:
        db.add(
            OntologyTerm(
                ontology_id=ontology.id,
                code=item.code,
                label=item.label,
                term_type="relation" if item.suggestion_kind == "relation" else "class",
                aliases=list((item.payload or {}).get("aliases") or []),
                definition=item.definition,
                constraints={"generated_from": (item.payload or {}).get("source"), "evidence_count": item.evidence_count},
                enabled=True,
            )
        )
        item.status = "published"
    db.flush()
    version = _publish_snapshot(db, ontology, admin)
    audit(
        db,
        admin.tenant_id,
        admin.id,
        "ontology.version.publish",
        "ontology_version",
        version.id,
        {"ontology_id": ontology.id, "version": version.version, "accepted_candidates": len(accepted)},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="语义模型版本发布冲突，请重试") from exc
    return {
        "version": serialize_row(version),
        "published_candidate_count": len(accepted),
        "term_count": len(version.manifest.get("terms") or []),
    }


@router.get("/{ontology_id}/versions")
def list_semantic_model_versions(
    ontology_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ontology = _ontology(db, ontology_id, user)
    return [
        serialize_row(row)
        for row in db.scalars(
            select(OntologyVersion).where(
                OntologyVersion.ontology_id == ontology.id,
                _active(OntologyVersion),
            ).order_by(OntologyVersion.version.desc())
        )
    ]
