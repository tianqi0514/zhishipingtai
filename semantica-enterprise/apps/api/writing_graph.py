from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.deps import get_current_user, require_space_permission
from packages.platform.audit import audit
from packages.platform.database import get_db
from packages.platform.models import (
    User,
    WritingClaim,
    WritingEntityCandidate,
    WritingEvidence,
    WritingExtractionRun,
    WritingFact,
    WritingGovernanceAction,
    WritingGraphRelease,
    WritingRelation,
)
from packages.platform.writing_graph import (
    govern_writing_object,
    publish_writing_graph,
    writing_graph_release_payload,
)


router = APIRouter(prefix="/writing-graph", tags=["writing-graph"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GovernanceDecisionRequest(StrictRequest):
    action: Literal["accept", "modify_accept", "reject", "supersede"]
    reason: str = Field(min_length=2, max_length=2000)
    changes: dict[str, Any] = Field(default_factory=dict)


class PublishWritingGraphRequest(StrictRequest):
    space_id: str


def active(model: Any) -> Any:
    return model.deleted_at.is_(None)


def serialize(row: Any) -> dict[str, Any]:
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name != "deleted_at"
    }


def writing_model(target_type: str) -> Any:
    model = {
        "evidence": WritingEvidence,
        "entity": WritingEntityCandidate,
        "claim": WritingClaim,
        "fact": WritingFact,
        "relation": WritingRelation,
    }.get(target_type)
    if model is None:
        raise HTTPException(404, "写作知识类型不存在")
    return model


def row_status(target_type: str, row: Any) -> str:
    return row.status if target_type == "evidence" else row.verification_status


@router.get("/governance/summary")
def governance_summary(
    space_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_space_permission(db, user, space_id, "read")
    counts: dict[str, dict[str, int]] = {}
    for target_type, model in (
        ("evidence", WritingEvidence), ("entity", WritingEntityCandidate),
        ("claim", WritingClaim), ("fact", WritingFact), ("relation", WritingRelation),
    ):
        status_column = model.status if target_type == "evidence" else model.verification_status
        rows = db.execute(select(status_column, func.count()).where(
            model.tenant_id == user.tenant_id,
            model.space_id == space_id,
            active(model),
        ).group_by(status_column)).all()
        counts[target_type] = {str(status): int(count) for status, count in rows}
    latest_run = db.scalar(select(WritingExtractionRun).where(
        WritingExtractionRun.tenant_id == user.tenant_id,
        WritingExtractionRun.space_id == space_id,
        active(WritingExtractionRun),
    ).order_by(WritingExtractionRun.created_at.desc()).limit(1))
    current_release = db.scalar(select(WritingGraphRelease).where(
        WritingGraphRelease.tenant_id == user.tenant_id,
        WritingGraphRelease.space_id == space_id,
        WritingGraphRelease.status == "published",
        active(WritingGraphRelease),
    ).order_by(WritingGraphRelease.release_number.desc()).limit(1))
    return {
        "space_id": space_id,
        "counts": counts,
        "pending_count": sum(values.get("candidate", 0) + values.get("conflicted", 0) for values in counts.values()),
        "latest_extraction": serialize(latest_run) if latest_run else None,
        "current_release": serialize(current_release) if current_release else None,
    }


@router.get("/governance/items")
def governance_items(
    space_id: str,
    target_type: Literal["evidence", "entity", "claim", "fact", "relation"] = "fact",
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_space_permission(db, user, space_id, "read")
    model = writing_model(target_type)
    query = select(model).where(
        model.tenant_id == user.tenant_id,
        model.space_id == space_id,
        active(model),
    )
    status_column = model.status if target_type == "evidence" else model.verification_status
    if status:
        query = query.where(status_column == status)
    rows = list(db.scalars(query.order_by(model.created_at.desc()).offset(offset).limit(limit)))
    count_query = select(func.count()).select_from(model).where(
        model.tenant_id == user.tenant_id,
        model.space_id == space_id,
        active(model),
    )
    if status:
        count_query = count_query.where(status_column == status)
    return {
        "total": int(db.scalar(count_query) or 0),
        "items": [{**serialize(row), "target_type": target_type, "display_status": row_status(target_type, row)} for row in rows],
    }


@router.get("/governance/items/{target_type}/{target_id}")
def governance_item(
    target_type: str,
    target_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    model = writing_model(target_type)
    row = db.get(model, target_id)
    if row is None or row.deleted_at is not None or row.tenant_id != user.tenant_id:
        raise HTTPException(404, "写作知识对象不存在")
    require_space_permission(db, user, row.space_id, "read")
    evidence_ids = list(getattr(row, "evidence_ids", []) or [])
    if isinstance(row, WritingEvidence):
        evidence = [row]
    else:
        evidence = list(db.scalars(select(WritingEvidence).where(
            WritingEvidence.id.in_(evidence_ids),
            WritingEvidence.tenant_id == user.tenant_id,
            active(WritingEvidence),
        ))) if evidence_ids else []
    history = list(db.scalars(select(WritingGovernanceAction).where(
        WritingGovernanceAction.tenant_id == user.tenant_id,
        WritingGovernanceAction.target_type == target_type,
        WritingGovernanceAction.target_id == target_id,
        active(WritingGovernanceAction),
    ).order_by(WritingGovernanceAction.created_at.desc())))
    return {
        **serialize(row),
        "target_type": target_type,
        "evidence": [serialize(item) for item in evidence],
        "governance_history": [serialize(item) for item in history],
    }


@router.post("/governance/items/{target_type}/{target_id}/decide")
def decide_governance_item(
    target_type: str,
    target_id: str,
    payload: GovernanceDecisionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    model = writing_model(target_type)
    current = db.get(model, target_id)
    if current is None or current.deleted_at is not None or current.tenant_id != user.tenant_id:
        raise HTTPException(404, "写作知识对象不存在")
    require_space_permission(db, user, current.space_id, "write")
    try:
        row, action = govern_writing_object(
            db,
            tenant_id=user.tenant_id,
            space_id=current.space_id,
            target_type=target_type,
            target_id=target_id,
            action=payload.action,
            actor_id=user.id,
            reason=payload.reason,
            changes=payload.changes,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(
        db, user.tenant_id, user.id, "writing_graph.governance.decide",
        target_type, target_id,
        {"action": payload.action, "space_id": current.space_id, "governance_action_id": action.id},
    )
    db.commit()
    return {"item": serialize(row), "action": serialize(action)}


@router.get("/releases")
def list_releases(
    space_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_space_permission(db, user, space_id, "read")
    return [serialize(row) for row in db.scalars(select(WritingGraphRelease).where(
        WritingGraphRelease.tenant_id == user.tenant_id,
        WritingGraphRelease.space_id == space_id,
        active(WritingGraphRelease),
    ).order_by(WritingGraphRelease.release_number.desc()))]


@router.post("/releases")
def create_release(
    payload: PublishWritingGraphRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_space_permission(db, user, payload.space_id, "write")
    try:
        release = publish_writing_graph(
            db, tenant_id=user.tenant_id, space_id=payload.space_id, actor_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(
        db, user.tenant_id, user.id, "writing_graph.release.publish",
        "writing_graph_release", release.id,
        {"space_id": payload.space_id, "release_number": release.release_number},
    )
    db.commit()
    return writing_graph_release_payload(db, release)


@router.get("/releases/{release_id}")
def get_release(
    release_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    release = db.get(WritingGraphRelease, release_id)
    if release is None or release.deleted_at is not None or release.tenant_id != user.tenant_id:
        raise HTTPException(404, "写作图谱版本不存在")
    require_space_permission(db, user, release.space_id, "read")
    return writing_graph_release_payload(db, release)


@router.get("/releases/{release_id}/graph")
def release_graph(
    release_id: str,
    view: Literal["business", "evidence"] = "business",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    release = db.get(WritingGraphRelease, release_id)
    if release is None or release.deleted_at is not None or release.tenant_id != user.tenant_id:
        raise HTTPException(404, "写作图谱版本不存在")
    require_space_permission(db, user, release.space_id, "read")
    payload = writing_graph_release_payload(db, release)
    items = payload["items"]
    entity_by_id = {item["id"]: item for item in items["entity"]}
    if view == "business":
        nodes = [{
            "id": item["id"], "type": "entity", "label": item["canonical_name"],
            "entity_type": item["entity_type"], "color": "#2878d0",
            "status": item["verification_status"],
        } for item in items["entity"]]
        edges = [{
            "id": item["id"],
            "source": item.get("subject_candidate_id") or item.get("subject_entity_id"),
            "target": item.get("object_candidate_id") or item.get("object_entity_id"),
            "label": item["predicate"], "type": "relation", "color": "#1199a6",
            "status": item["verification_status"], "fact_id": item["fact_id"],
        } for item in items["relation"] if (
            (item.get("subject_candidate_id") or item.get("subject_entity_id")) in entity_by_id
            and (item.get("object_candidate_id") or item.get("object_entity_id")) in entity_by_id
        )]
        return {"release": payload, "view": view, "nodes": nodes, "edges": edges}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    colors = {"evidence": "#7a55c7", "claim": "#df7a23", "fact": "#239b69", "entity": "#2878d0", "relation": "#1199a6"}
    for kind in ("evidence", "claim", "fact", "entity", "relation"):
        for item in items[kind]:
            label = (
                item.get("canonical_name") or item.get("predicate") or item.get("filename")
                or item.get("fact_key") or item["id"]
            )
            nodes.append({"id": item["id"], "type": kind, "label": label, "color": colors[kind]})
    known = {node["id"] for node in nodes}
    for claim in items["claim"]:
        for evidence_id in claim.get("evidence_ids") or []:
            if evidence_id in known:
                edges.append({
                    "id": f"{evidence_id}:{claim['id']}",
                    "source": evidence_id,
                    "target": claim["id"],
                    "label": "支持",
                    "type": "evidence_claim",
                    "color": "#8b6bd1",
                })
    for fact in items["fact"]:
        for claim_id in fact.get("claim_ids") or []:
            if claim_id in known:
                edges.append({
                    "id": f"{claim_id}:{fact['id']}",
                    "source": claim_id,
                    "target": fact["id"],
                    "label": "核验形成",
                    "type": "claim_fact",
                    "color": "#df7a23",
                })
        for entity_id, label in (
            (fact.get("subject_candidate_id") or fact.get("subject_entity_id"), "主体"),
            (fact.get("object_candidate_id") or fact.get("object_entity_id"), "客体"),
        ):
            if entity_id in known:
                edges.append({
                    "id": f"{fact['id']}:{entity_id}:{label}",
                    "source": fact["id"],
                    "target": entity_id,
                    "label": label,
                    "type": "fact_entity",
                    "color": "#239b69",
                })
    for relation in items["relation"]:
        if relation.get("fact_id") in known:
            edges.append({
                "id": f"{relation['fact_id']}:{relation['id']}",
                "source": relation["fact_id"],
                "target": relation["id"],
                "label": "投影",
                "type": "fact_relation",
                "color": "#1199a6",
            })
    return {"release": payload, "view": view, "nodes": nodes, "edges": edges}
