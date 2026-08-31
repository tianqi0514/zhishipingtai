from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.deps import has_space_permission
from apps.api.schemas import (
    AgentCredentialRequest,
    AgentGraphQueryRequest,
    AgentKnowledgeReasonRequest,
    AgentKnowledgeSearchRequest,
)
from apps.api.utils import serialize_row
from packages.platform.audit import audit
from packages.platform.database import get_db
from packages.platform.knowledge_search import execute_hybrid_search
from packages.platform.analysis import execute_inference_run, inference_result_rows
from packages.platform.models import (
    AnalysisRuleSet,
    AgentCredential,
    CanonicalEntity,
    Chunk,
    Conversation,
    Document,
    DocumentProfile,
    DocumentVersion,
    Fact,
    GraphRelease,
    InferenceEvidence,
    InferenceRun,
    InferredFact,
    KnowledgeSpace,
    ModelConfig,
    User,
)
from packages.platform.security import (
    create_agent_access_token,
    decode_agent_access_token,
    decrypt_secret,
    verify_agent_service_secret,
)
from packages.semantica_adapter.extract import _effective_temperature


router = APIRouter(prefix="/internal/agent", tags=["agent-internal"])


def _active(model):
    return model.deleted_at.is_(None)


def _service_authenticated(x_agent_service_secret: str | None = Header(default=None)) -> None:
    try:
        accepted = verify_agent_service_secret(x_agent_service_secret)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if not accepted:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Agent 服务认证失败")


def _authorized_spaces(db: Session, user: User, requested: list[str]) -> list[str]:
    spaces = list(
        db.scalars(
            select(KnowledgeSpace).where(
                KnowledgeSpace.tenant_id == user.tenant_id,
                KnowledgeSpace.enabled.is_(True),
                _active(KnowledgeSpace),
            )
        )
    )
    requested_set = set(requested or [space.id for space in spaces])
    allowed = [
        space.id
        for space in spaces
        if space.id in requested_set and has_space_permission(db, user, space.id, "read")
    ]
    if requested_set - set(allowed):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "请求包含无权访问的知识空间")
    return allowed


def get_agent_claims(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少内部访问凭据")
    try:
        claims = decode_agent_access_token(authorization[7:].strip())
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "内部访问凭据无效或已过期") from exc
    if claims.get("scope") != "knowledge:agent":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "内部访问范围无效")
    credential = db.scalar(select(AgentCredential).where(AgentCredential.jti == claims.get("jti")))
    now = datetime.now(timezone.utc)
    if credential is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "内部访问凭据已撤销")
    expires_at = credential.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        credential.revoked_at is not None
        or expires_at <= now
        or credential.conversation_id != claims.get("conversation_id")
        or credential.user_id != claims.get("sub")
        or credential.tenant_id != claims.get("tenant_id")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "内部访问凭据已撤销")
    conversation = db.get(Conversation, credential.conversation_id)
    if conversation is None or conversation.status == "deleted":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "会话不可用")
    credential.last_used_at = now
    # Read-only tool routes still need a durable usage timestamp for revocation
    # audits. The dependency runs before route-side mutations, so committing
    # here cannot publish an unfinished search/graph transaction.
    db.commit()
    return {**claims, "credential": credential, "conversation": conversation}


def _require_conversation(claims: dict[str, Any], conversation_id: str) -> None:
    if conversation_id != claims.get("conversation_id"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "凭据不能访问其他会话")


@router.post("/credentials")
def issue_credential(
    payload: AgentCredentialRequest,
    _: None = Depends(_service_authenticated),
    db: Session = Depends(get_db),
):
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.harness_session_id == payload.harness_session_id,
            Conversation.status != "deleted",
        )
    )
    if conversation is None:
        raise HTTPException(404, "Harness 会话未映射到业务会话")
    user = db.get(User, conversation.user_id)
    if user is None or not user.enabled or user.deleted_at is not None:
        raise HTTPException(403, "会话用户不可用")
    spaces = _authorized_spaces(db, user, list((conversation.settings or {}).get("space_ids") or []))
    now = datetime.now(timezone.utc)
    for old in db.scalars(
        select(AgentCredential).where(
            AgentCredential.conversation_id == conversation.id,
            AgentCredential.revoked_at.is_(None),
        )
    ):
        old.revoked_at = now
    token, jti, expires_at = create_agent_access_token(
        conversation_id=conversation.id,
        harness_session_id=conversation.harness_session_id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        space_ids=spaces,
    )
    db.add(
        AgentCredential(
            jti=jti,
            conversation_id=conversation.id,
            tenant_id=user.tenant_id,
            user_id=user.id,
            space_ids=spaces,
            expires_at=expires_at,
        )
    )
    audit(
        db,
        user.tenant_id,
        None,
        "agent.credential.issue",
        "conversation",
        conversation.id,
        {"jti": jti, "space_count": len(spaces), "expires_at": expires_at.isoformat()},
    )
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "conversation_id": conversation.id,
        "space_ids": spaces,
    }


@router.get("/model/{harness_session_id}")
def get_agent_model(
    harness_session_id: str,
    _: None = Depends(_service_authenticated),
    db: Session = Depends(get_db),
):
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.harness_session_id == harness_session_id,
            Conversation.status != "deleted",
        )
    )
    if conversation is None:
        raise HTTPException(404, "Harness 会话未映射到业务会话")
    model = db.scalar(
        select(ModelConfig)
        .where(
            ModelConfig.tenant_id == conversation.tenant_id,
            ModelConfig.model_kind == "llm",
            ModelConfig.enabled.is_(True),
            ModelConfig.is_default.is_(True),
            _active(ModelConfig),
        )
        .limit(1)
    )
    if model is None:
        raise HTTPException(409, "未配置默认大模型")
    api_key = decrypt_secret(model.api_key_encrypted)
    if not api_key:
        raise HTTPException(409, "默认大模型未配置 API Key")
    config = model.config or {}
    return {
        "provider": model.provider,
        "model_name": model.model_name,
        "base_url": model.base_url,
        "api_key": api_key,
        "timeout": int(config.get("timeout", 120)),
        "max_retries": int(config.get("max_retries", config.get("retry", 2))),
        "temperature": _effective_temperature(
            model.model_name, float(config.get("temperature", 0.2))
        ),
        "max_tokens": int(config.get("max_tokens", 4096)),
        "parameters": dict(config.get("parameters") or {}),
    }


@router.post("/knowledge/search")
def agent_knowledge_search(
    payload: AgentKnowledgeSearchRequest,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, payload.conversation_id)
    token_spaces = set(claims.get("space_ids") or [])
    requested = payload.space_ids or list(token_spaces)
    if not set(requested).issubset(token_spaces):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "工具请求超出凭据知识空间范围")
    result = execute_hybrid_search(
        db,
        tenant_id=claims["tenant_id"],
        user_id=claims["sub"],
        query=payload.query,
        space_ids=requested,
        top_k=payload.top_k,
        use_keyword=payload.use_keyword,
        use_vector=payload.use_vector,
        use_graph=payload.use_graph,
        use_reranker=payload.use_reranker,
        filters=payload.filters,
        audit_action="agent.knowledge.search",
    )
    db.commit()
    return result


@router.get("/knowledge/fragments/{chunk_id}")
def agent_get_fragment(
    chunk_id: str,
    conversation_id: str,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, conversation_id)
    chunk = db.get(Chunk, chunk_id)
    if (
        chunk is None
        or chunk.deleted_at is not None
        or chunk.tenant_id != claims.get("tenant_id")
        or chunk.space_id not in set(claims.get("space_ids") or [])
    ):
        raise HTTPException(404, "知识片段不存在或无权访问")
    document = db.get(Document, chunk.document_id)
    version = db.get(DocumentVersion, chunk.version_id)
    if document is None or version is None or document.tenant_id != claims.get("tenant_id"):
        raise HTTPException(404, "来源文档不存在")
    return {
        "chunk_id": chunk.id,
        "document_id": document.id,
        "version_id": version.id,
        "document_title": document.title,
        "text": chunk.text,
        "page_number": chunk.page_number,
        "structural_path": chunk.structural_path,
        "document_tags": document.tags or [],
        "document_version": version.version_number,
        "document_deleted": document.deleted_at is not None,
        "version_deleted": version.deleted_at is not None,
        "source": {
            "filename": version.filename,
            "content_type": version.content_type,
            "source_span": chunk.source_span or {},
        },
        "has_access": True,
    }


@router.post("/knowledge/graph")
def agent_graph_query(
    payload: AgentGraphQueryRequest,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, payload.conversation_id)
    token_spaces = set(claims.get("space_ids") or [])
    spaces = payload.space_ids or list(token_spaces)
    if not set(spaces).issubset(token_spaces):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "工具请求超出凭据知识空间范围")
    entity_term = payload.entity_query.strip().casefold()
    relation_term = payload.relation_query.strip().casefold()
    entity_rows = list(
        db.scalars(
            select(CanonicalEntity).where(
                CanonicalEntity.space_id.in_(spaces),
                CanonicalEntity.status == "published",
                _active(CanonicalEntity),
            )
        )
    )
    if entity_term:
        entity_rows = [
            row
            for row in entity_rows
            if entity_term in row.canonical_name.casefold()
            or any(entity_term in alias.casefold() for alias in (row.aliases or []))
        ]
    entity_rows = entity_rows[: payload.limit]
    entity_ids = {row.id for row in entity_rows}
    fact_rows = list(
        db.scalars(
            select(Fact).where(
                Fact.space_id.in_(spaces),
                Fact.status == "published",
                _active(Fact),
            )
        )
    )
    if entity_ids:
        fact_rows = [
            row
            for row in fact_rows
            if row.subject_entity_id in entity_ids or row.object_entity_id in entity_ids
        ]
    if relation_term:
        fact_rows = [row for row in fact_rows if relation_term in row.predicate.casefold()]
    inferred_rows = list(
        db.scalars(
            select(InferredFact).where(
                InferredFact.space_id.in_(spaces),
                InferredFact.status == "published",
                _active(InferredFact),
            )
        )
    )
    if entity_ids:
        inferred_rows = [
            row for row in inferred_rows
            if row.subject_entity_id in entity_ids or row.object_entity_id in entity_ids
        ]
    if relation_term:
        inferred_rows = [row for row in inferred_rows if relation_term in row.predicate.casefold()]
    combined_facts = sorted(
        [(row, "asserted") for row in fact_rows] + [(row, "inferred") for row in inferred_rows],
        key=lambda item: (float(item[0].confidence or 0), item[0].created_at),
        reverse=True,
    )[: payload.limit]
    selected_asserted = [row for row, origin in combined_facts if origin == "asserted"]
    selected_inferred = [row for row, origin in combined_facts if origin == "inferred"]
    inferred_evidence: dict[str, InferenceEvidence] = {}
    if selected_inferred:
        for evidence in db.scalars(
            select(InferenceEvidence)
            .where(InferenceEvidence.inferred_fact_id.in_([item.id for item in selected_inferred]))
            .order_by(InferenceEvidence.inferred_fact_id, InferenceEvidence.ordinal)
        ):
            inferred_evidence.setdefault(evidence.inferred_fact_id, evidence)
    all_entity_ids = entity_ids | {row.subject_entity_id for row, _ in combined_facts} | {
        row.object_entity_id for row in selected_asserted if row.object_entity_id
    } | {row.object_entity_id for row in selected_inferred if row.object_entity_id}
    entities = {
        row.id: row
        for row in db.scalars(select(CanonicalEntity).where(CanonicalEntity.id.in_(all_entity_ids)))
    } if all_entity_ids else {}
    release = db.scalar(
        select(func.max(GraphRelease.release_number)).where(
            GraphRelease.space_id.in_(spaces),
            GraphRelease.status == "published",
            _active(GraphRelease),
        )
    )
    audit(db, claims["tenant_id"], claims["sub"], "agent.knowledge.graph", "conversation", payload.conversation_id)
    db.commit()
    return {
        "entities": [
            {
                "id": row.id,
                "name": row.canonical_name,
                "type": row.entity_type,
                "aliases": row.aliases or [],
                "properties": row.properties or {},
                "confidence": row.confidence,
                "space_id": row.space_id,
            }
            for row in entities.values()
        ],
        "facts": [
            {
                "id": row.id,
                "subject": entities[row.subject_entity_id].canonical_name if row.subject_entity_id in entities else row.subject_entity_id,
                "predicate": row.predicate,
                "object": entities[row.object_entity_id].canonical_name if row.object_entity_id in entities else row.object_value,
                "confidence": row.confidence,
                "evidence_chunk_id": row.source_chunk_id,
                "space_id": row.space_id,
                "origin_type": "asserted",
            }
            for row in selected_asserted
        ] + [
            {
                "id": row.id,
                "subject": entities[row.subject_entity_id].canonical_name if row.subject_entity_id in entities else row.subject_entity_id,
                "predicate": row.predicate,
                "object": entities[row.object_entity_id].canonical_name if row.object_entity_id in entities else row.object_value,
                "confidence": row.confidence,
                "evidence_chunk_id": inferred_evidence.get(row.id).source_chunk_id if inferred_evidence.get(row.id) else None,
                "space_id": row.space_id,
                "origin_type": "inferred",
                "proof": row.proof or {},
            }
            for row in selected_inferred
        ],
        "evidence_chunk_ids": sorted(
            {row.source_chunk_id for row in selected_asserted if row.source_chunk_id}
            | {
                evidence.source_chunk_id
                for evidence in inferred_evidence.values()
                if evidence.source_chunk_id
            }
        ),
        "graph_release": release,
    }


@router.post("/knowledge/reason")
def agent_knowledge_reason(
    payload: AgentKnowledgeReasonRequest,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, payload.conversation_id)
    token_spaces = set(claims.get("space_ids") or [])
    requested = set(payload.space_ids or token_spaces)
    if not requested.issubset(token_spaces):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "工具请求超出凭据知识空间范围")
    query = select(AnalysisRuleSet).where(
        AnalysisRuleSet.tenant_id == claims["tenant_id"],
        AnalysisRuleSet.enabled.is_(True),
        _active(AnalysisRuleSet),
    )
    if payload.rule_set_ids:
        query = query.where(AnalysisRuleSet.id.in_(payload.rule_set_ids))
    rule_sets = [row for row in db.scalars(query) if requested.intersection(set(row.space_ids or []))]
    if payload.rule_set_ids and len(rule_sets) != len(set(payload.rule_set_ids)):
        raise HTTPException(404, "规则集不存在、未启用或不在当前知识空间范围")
    if not rule_sets:
        return {
            "goal": payload.goal,
            "runs": [],
            "items": [],
            "warnings": ["当前知识空间没有可用的启用规则集"],
        }

    items: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    warnings: list[str] = []
    per_set_limit = max(1, payload.max_results // len(rule_sets))
    for rule_set in rule_sets:
        spaces = sorted(requested.intersection(set(rule_set.space_ids or [])))
        run = InferenceRun(
            tenant_id=claims["tenant_id"],
            rule_set_id=rule_set.id,
            requested_by=claims["sub"],
            trigger_type="agent",
            mode="preview",
            space_ids=spaces,
            run_input={"max_results": per_set_limit, "goal": payload.goal, "conversation_id": payload.conversation_id},
        )
        db.add(run)
        db.commit()
        try:
            result = execute_inference_run(db, run.id)
            run_items = inference_result_rows(db, run.id)
            items.extend({**item, "rule_set_id": rule_set.id, "rule_set_name": rule_set.name} for item in run_items)
            runs.append({"run_id": run.id, "rule_set_id": rule_set.id, "rule_set_name": rule_set.name, "metrics": result})
        except Exception as exc:
            db.rollback()
            failed = db.get(InferenceRun, run.id)
            if failed:
                failed.status = "failed"
                failed.error_code = "AGENT_INFERENCE_FAILED"
                failed.error_message = f"{type(exc).__name__}: {exc}"[:4000]
                failed.finished_at = datetime.now(timezone.utc)
                db.commit()
            warnings.append(f"规则集“{rule_set.name}”推理失败：{type(exc).__name__}")
    items = sorted(items, key=lambda item: float(item.get("confidence") or 0), reverse=True)[: payload.max_results]
    audit(
        db,
        claims["tenant_id"],
        claims["sub"],
        "agent.knowledge.reason",
        "conversation",
        payload.conversation_id,
        {"spaces": sorted(requested), "rule_sets": [row.id for row in rule_sets], "results": len(items)},
    )
    db.commit()
    return {"goal": payload.goal, "runs": runs, "items": items, "warnings": warnings}


@router.get("/knowledge/document-profiles/{document_or_version_id}")
def agent_document_profile(
    document_or_version_id: str,
    conversation_id: str,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, conversation_id)
    profile = db.scalar(
        select(DocumentProfile).where(
            (DocumentProfile.document_id == document_or_version_id)
            | (DocumentProfile.version_id == document_or_version_id)
        ).order_by(DocumentProfile.generated_at.desc()).limit(1)
    )
    if profile is None or profile.space_id not in set(claims.get("space_ids") or []):
        raise HTTPException(404, "文档画像不存在或无权访问")
    version = db.get(DocumentVersion, profile.version_id)
    result = serialize_row(profile)
    result["updated_at"] = version.updated_at.isoformat() if version else None
    result["incremental_status"] = (version.parse_summary or {}).get("incremental") if version else None
    return result


@router.get("/knowledge/spaces")
def agent_list_spaces(
    conversation_id: str,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, conversation_id)
    space_ids = claims.get("space_ids") or []
    rows = list(
        db.scalars(
            select(KnowledgeSpace).where(
                KnowledgeSpace.id.in_(space_ids),
                KnowledgeSpace.enabled.is_(True),
                _active(KnowledgeSpace),
            )
        )
    ) if space_ids else []
    return [{"id": row.id, "code": row.code, "name": row.name} for row in rows]
