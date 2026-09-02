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
from apps.api.structured_schemas import (
    AgentStructuredExecuteRequest,
    AgentStructuredInspectValuesRequest,
    AgentStructuredObjectRequest,
    AgentStructuredRelationPathRequest,
    AgentStructuredSchemaSearchRequest,
    StructuredExecuteRequest,
)
from apps.api.utils import serialize_row
from packages.platform.audit import audit
from packages.platform.database import get_db
from packages.platform.knowledge_search import execute_hybrid_search
from packages.platform.curation import effective_chunk_text, effective_entity, effective_fact
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
    DataSourceSchemaVersion,
    DataPreviewPolicy,
    SemanticMappingSet,
    SemanticMappingVersion,
    SourceConnector,
    User,
)
from packages.platform.structured_data import (
    StructuredDataError,
    current_schema,
    get_or_create_preview_policy,
    inspect_distinct_values,
)
from packages.platform.structured_query import (
    apply_activated_metric_contracts,
    semantic_catalog_for_planner,
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


def _agent_citation_contract(result: dict[str, Any]) -> dict[str, Any]:
    """Attach immutable citation labels to ranked knowledge-tool results."""
    contracted = dict(result)
    contracted_items = []
    for raw_item in result.get("items") or []:
        item = dict(raw_item)
        rank = int(item.get("rank") or 0)
        item["citation_number"] = rank
        item["citation_label"] = f"[{rank}]"
        item["citation_title"] = item.get("title") or ""
        item["citation_rule"] = "引用本片段时必须原样使用 citation_label，不得重新编号"
        contracted_items.append(item)
    contracted["items"] = contracted_items
    contracted["citation_policy"] = {
        "immutable": True,
        "instruction": "引用编号是片段外键；只能复制 item.citation_label，不得按采用顺序重新编号",
    }
    return contracted


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
    return _agent_citation_contract(result)


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
    try:
        effective_text, curation = effective_chunk_text(db, chunk, include_superseded=True)
    except ValueError as exc:
        raise HTTPException(404, "知识片段已被人工屏蔽") from exc
    return {
        "chunk_id": chunk.id,
        "document_id": document.id,
        "version_id": version.id,
        "document_title": document.title,
        "text": effective_text,
        "curation": curation,
        "page_number": chunk.page_number,
        "structural_path": chunk.structural_path,
        "start_seconds": (chunk.source_span or {}).get("time_start"),
        "end_seconds": (chunk.source_span or {}).get("time_end"),
        "media_url": f"/api/v1/documents/{document.id}/media-content" if str(version.content_type or "").startswith(("audio/", "video/")) else None,
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
                _active(CanonicalEntity),
            )
        )
    )
    entity_values = {row.id: effective_entity(db, row) for row in entity_rows}
    entity_rows = [
        row for row in entity_rows
        if entity_values[row.id].get("status") in {"published", "active"}
    ]
    active_entity_ids = {row.id for row in entity_rows}
    if entity_term:
        entity_rows = [
            row
            for row in entity_rows
            if entity_term in entity_values[row.id]["canonical_name"].casefold()
            or any(entity_term in alias.casefold() for alias in (entity_values[row.id]["aliases"] or []))
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
    fact_values = {row.id: effective_fact(db, row) for row in fact_rows}
    fact_rows = [
        row for row in fact_rows
        if fact_values[row.id].get("status") == "published"
        and fact_values[row.id].get("subject_entity_id") in active_entity_ids
        and (
            not fact_values[row.id].get("object_entity_id")
            or fact_values[row.id].get("object_entity_id") in active_entity_ids
        )
    ]
    if entity_ids:
        fact_rows = [
            row
            for row in fact_rows
            if fact_values[row.id]["subject_entity_id"] in entity_ids or fact_values[row.id]["object_entity_id"] in entity_ids
        ]
    if relation_term:
        fact_rows = [row for row in fact_rows if relation_term in fact_values[row.id]["predicate"].casefold()]
    inferred_rows = list(
        db.scalars(
            select(InferredFact).where(
                InferredFact.space_id.in_(spaces),
                InferredFact.status == "published",
                _active(InferredFact),
            )
        )
    )
    inferred_rows = [
        row for row in inferred_rows
        if row.subject_entity_id in active_entity_ids
        and (not row.object_entity_id or row.object_entity_id in active_entity_ids)
    ]
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
    all_entity_ids = entity_ids | {
        fact_values[row.id]["subject_entity_id"] if origin == "asserted" else row.subject_entity_id
        for row, origin in combined_facts
    } | {
        fact_values[row.id]["object_entity_id"] for row in selected_asserted if fact_values[row.id]["object_entity_id"]
    } | {row.object_entity_id for row in selected_inferred if row.object_entity_id}
    entities = {
        row.id: row
        for row in db.scalars(select(CanonicalEntity).where(CanonicalEntity.id.in_(all_entity_ids)))
    } if all_entity_ids else {}
    final_entity_values = {row.id: effective_entity(db, row) for row in entities.values()}
    entities = {
        row_id: row for row_id, row in entities.items()
        if final_entity_values[row_id].get("status") in {"published", "active"}
    }
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
                "name": final_entity_values[row.id]["canonical_name"],
                "type": final_entity_values[row.id]["entity_type"],
                "aliases": final_entity_values[row.id]["aliases"],
                "properties": final_entity_values[row.id]["properties"],
                "confidence": final_entity_values[row.id]["confidence"],
                "space_id": row.space_id,
            }
            for row in entities.values()
        ],
        "facts": [
            {
                "id": row.id,
                "subject": final_entity_values[fact_values[row.id]["subject_entity_id"]]["canonical_name"] if fact_values[row.id]["subject_entity_id"] in entities else fact_values[row.id]["subject_entity_id"],
                "predicate": fact_values[row.id]["predicate"],
                "object": final_entity_values[fact_values[row.id]["object_entity_id"]]["canonical_name"] if fact_values[row.id]["object_entity_id"] in entities else fact_values[row.id]["object_value"],
                "confidence": fact_values[row.id]["confidence"],
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


def _agent_mapping_version(
    db: Session,
    claims: dict[str, Any],
    mapping_version_id: str,
) -> tuple[SemanticMappingSet, SemanticMappingVersion, SourceConnector, DataSourceSchemaVersion]:
    version = db.get(SemanticMappingVersion, mapping_version_id)
    if version is None or version.deleted_at is not None or version.tenant_id != claims.get("tenant_id"):
        raise HTTPException(404, "语义映射版本不存在")
    mapping_set = db.get(SemanticMappingSet, version.mapping_set_id)
    token_spaces = set(claims.get("space_ids") or [])
    if (
        mapping_set is None or mapping_set.deleted_at is not None
        or mapping_set.active_version_id != version.id or version.status != "active"
        or version.space_id not in token_spaces
    ):
        raise HTTPException(403, "语义映射未激活、已过期或超出凭据范围")
    source = db.get(SourceConnector, version.source_id)
    schema = db.get(DataSourceSchemaVersion, version.schema_version_id)
    current = current_schema(db, version.source_id)
    if source is None or source.deleted_at is not None or not source.enabled:
        raise HTTPException(409, "结构化数据源不可用")
    if schema is None or current is None or current.id != schema.id or current.schema_fingerprint != version.schema_fingerprint:
        raise HTTPException(409, "Schema 已变化，语义映射必须重新验证")
    return mapping_set, version, source, schema


@router.post("/structured/schema-search")
def agent_structured_schema_search(
    payload: AgentStructuredSchemaSearchRequest,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, payload.conversation_id)
    allowed_spaces = set(claims.get("space_ids") or [])
    requested_spaces = set(payload.space_ids or allowed_spaces)
    if not requested_spaces.issubset(allowed_spaces):
        raise HTTPException(403, "工具请求超出凭据知识空间范围")
    query = payload.query.casefold().strip()
    source_filter = set(payload.source_ids)
    candidates = list(db.scalars(select(SemanticMappingVersion).where(
        SemanticMappingVersion.tenant_id == claims["tenant_id"],
        SemanticMappingVersion.space_id.in_(requested_spaces),
        SemanticMappingVersion.status == "active",
        SemanticMappingVersion.deleted_at.is_(None),
    ))) if requested_spaces else []
    items: list[dict[str, Any]] = []
    for version in candidates:
        if source_filter and version.source_id not in source_filter:
            continue
        mapping_set = db.get(SemanticMappingSet, version.mapping_set_id)
        if mapping_set is None or mapping_set.active_version_id != version.id:
            continue
        source = db.get(SourceConnector, version.source_id)
        space = db.get(KnowledgeSpace, version.space_id)
        manifest = version.manifest or {}
        attributes_by_entity: dict[str, list[dict[str, Any]]] = {}
        for attribute in manifest.get("attributes") or []:
            attributes_by_entity.setdefault(attribute["entity_id"], []).append(attribute)
        relationships = manifest.get("relationships") or []
        for entity in manifest.get("entities") or []:
            attributes = attributes_by_entity.get(entity["id"], [])
            related = [
                item for item in relationships
                if entity["id"] in {item.get("from_entity_id"), item.get("to_entity_id")}
            ]
            searchable_terms = [
                entity.get("id", ""), entity.get("label", ""), entity.get("description", ""),
                *(item.get("label", "") for item in attributes),
                *(item.get("label", "") for item in related),
                source.name if source else "",
                space.name if space else "",
            ]
            searchable = " ".join(searchable_terms).casefold()
            query_tokens = [token for token in query.split() if token]
            token_score = sum(token in searchable for token in query_tokens) / max(1, len(query_tokens))
            label_matches = sum(
                bool(term and len(term.strip()) >= 2 and term.casefold() in query)
                for term in searchable_terms
            )
            label_score = min(1.0, label_matches / 2)
            score = 1.0 if query in searchable else max(token_score, label_score)
            if score <= 0:
                continue
            items.append({
                "semantic_object_id": entity["id"],
                "label": entity.get("label"),
                "description": entity.get("description"),
                "attribute_ids": [item["id"] for item in attributes],
                "relationship_ids": [item["id"] for item in related],
                "mapping_version_id": version.id,
                "source_id": version.source_id,
                "source_name": source.name if source else None,
                "space_id": version.space_id,
                "space_name": space.name if space else None,
                "metric_attributes": [{
                    "attribute_id": item.get("id"),
                    "label": item.get("label"),
                    "business_definition": item.get("business_definition") or "",
                    "default_aggregate": item.get("default_aggregate"),
                    "required_filters": item.get("required_filters") or [],
                } for item in attributes if item.get("is_measure")],
                "score": round(float(score), 4),
            })
    items.sort(key=lambda item: (item["score"], item.get("label") or ""), reverse=True)
    result = {
        "query": payload.query,
        "semantic_objects": items[: payload.limit],
        "mapping_versions": sorted({item["mapping_version_id"] for item in items[: payload.limit]}),
        "warnings": [] if items else ["未找到与问题匹配的已激活结构化语义对象"],
    }
    audit(db, claims["tenant_id"], claims["sub"], "agent.structured.schema_search", "conversation", payload.conversation_id, {"result_count": len(result["semantic_objects"]), "space_count": len(requested_spaces)})
    db.commit()
    return result


@router.post("/structured/object")
def agent_structured_get_object(
    payload: AgentStructuredObjectRequest,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, payload.conversation_id)
    _, version, source, schema = _agent_mapping_version(db, claims, payload.mapping_version_id)
    catalog = semantic_catalog_for_planner(version)
    entity = next((item for item in catalog["entities"] if item.get("id") == payload.semantic_object_id), None)
    if entity is None:
        raise HTTPException(404, "业务对象不存在")
    attributes = [item for item in catalog["attributes"] if item.get("entity_id") == entity["id"]]
    relationships = [
        item for item in catalog["relationships"]
        if entity["id"] in {item.get("from_entity_id"), item.get("to_entity_id")}
    ]
    return {
        "semantic_object": entity,
        "attributes": attributes,
        "relationships": relationships,
        "relationship_paths": [{
            "relationship_id": item["id"],
            "direction": "outgoing" if item.get("from_entity_id") == entity["id"] else "incoming",
            "other_entity_id": item.get("to_entity_id") if item.get("from_entity_id") == entity["id"] else item.get("from_entity_id"),
        } for item in relationships],
        "mapping_version_id": version.id,
        "mapping_status": version.status,
        "schema_version_id": schema.id,
        "data_freshness": source.last_sync_at.isoformat() if source.last_sync_at else None,
        "query_contract": {
            "plan_version": "chuanshen.semantic-query-plan/v1",
            "ir_version": "chuanshen.query-ir/v1",
            "binding_example": "e",
            "expression_examples": {
                "attribute": {
                    "kind": "attribute",
                    "attribute_id": attributes[0]["id"] if attributes else "attribute-id",
                    "binding": "e",
                },
                "literal": {"kind": "literal", "value": "示例值"},
                "comparison": {
                    "kind": "binary",
                    "operator": "=",
                    "left": {
                        "kind": "attribute",
                        "attribute_id": attributes[0]["id"] if attributes else "attribute-id",
                        "binding": "e",
                    },
                    "right": {"kind": "literal", "value": "示例值"},
                },
                "aggregate": {
                    "kind": "aggregate",
                    "function": "sum",
                    "expression": {
                        "kind": "attribute",
                        "attribute_id": next((item["id"] for item in attributes if item.get("is_measure")), attributes[0]["id"] if attributes else "measure-id"),
                        "binding": "e",
                    },
                },
            },
            "rules": [
                "只能引用本响应中的语义 ID",
                "指标的 default_aggregate 和 required_filters 属于已激活业务口径，必须采用",
                "required_filters 必须同时出现在 Plan filters 和 IR where；平台会在执行边界再次强制应用",
                "比较表达式使用 kind=binary 和 SQL 白名单运算符",
                "可选字段没有值时省略，不要传 null",
            ],
        },
    }


@router.post("/structured/relation-path")
def agent_structured_relation_path(
    payload: AgentStructuredRelationPathRequest,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, payload.conversation_id)
    _, version, _, _ = _agent_mapping_version(db, claims, payload.mapping_version_id)
    relationships = list((version.manifest or {}).get("relationships") or [])
    queue: list[tuple[str, list[dict[str, Any]]]] = [(payload.from_entity_id, [])]
    visited = {payload.from_entity_id}
    found: list[dict[str, Any]] | None = None
    while queue:
        entity_id, path = queue.pop(0)
        if entity_id == payload.to_entity_id:
            found = path
            break
        if len(path) >= payload.max_depth:
            continue
        for relation in relationships:
            if relation.get("from_entity_id") == entity_id:
                other, direction = relation.get("to_entity_id"), "forward"
            elif relation.get("to_entity_id") == entity_id:
                other, direction = relation.get("from_entity_id"), "reverse"
            else:
                continue
            if not other or other in visited:
                continue
            visited.add(other)
            queue.append((other, [*path, {
                "relationship_id": relation["id"],
                "from_entity_id": entity_id,
                "to_entity_id": other,
                "direction": direction,
                "cardinality": relation.get("cardinality", "unknown"),
                "evidence": relation.get("evidence") or [],
            }]))
    return {
        "found": found is not None,
        "from_entity_id": payload.from_entity_id,
        "to_entity_id": payload.to_entity_id,
        "mapping_version_id": version.id,
        "path": found or [],
        "relationship_ids": [item["relationship_id"] for item in (found or [])],
        "warnings": [] if found is not None else ["已激活映射中不存在可用关系路径"],
    }


@router.post("/structured/values")
def agent_structured_inspect_values(
    payload: AgentStructuredInspectValuesRequest,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, payload.conversation_id)
    _, version, source, schema = _agent_mapping_version(db, claims, payload.mapping_version_id)
    manifest = version.manifest or {}
    attribute = next((item for item in manifest.get("attributes") or [] if item.get("id") == payload.attribute_id), None)
    if attribute is None:
        raise HTTPException(404, "业务属性不存在")
    fragment = next((
        fragment
        for entity in manifest.get("entities") or []
        for fragment in entity.get("fragments") or []
        if fragment.get("id") == attribute.get("fragment_id")
    ), None)
    if fragment is None:
        raise HTTPException(409, "业务属性的数据片段映射无效")
    object_row = next(item for item in (schema.catalog or {}).get("objects") or [] if item.get("id") == fragment["object_id"])
    column_row = next(item for item in object_row.get("columns") or [] if item.get("id") == attribute["column_id"])
    policy = get_or_create_preview_policy(db, source)
    try:
        inspected = inspect_distinct_values(
            source,
            schema,
            policy,
            object_id=fragment["object_id"],
            column_id=attribute["column_id"],
            search=payload.search,
            limit=payload.limit,
        )
    except StructuredDataError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc
    return {
        "attribute_id": attribute["id"],
        "data_type": column_row.get("type_family"),
        "values": inspected["values"],
        "matched": inspected["matched"],
        "elapsed_ms": inspected["elapsed_ms"],
        "warnings": inspected["warnings"],
    }


@router.post("/structured/execute")
def agent_structured_execute(
    payload: AgentStructuredExecuteRequest,
    claims: dict[str, Any] = Depends(get_agent_claims),
    db: Session = Depends(get_db),
):
    _require_conversation(claims, payload.conversation_id)
    _, version, _, _ = _agent_mapping_version(db, claims, payload.mapping_version_id)
    if version.space_id not in set(claims.get("space_ids") or []):
        raise HTTPException(403, "结构化查询超出凭据知识空间范围")
    user = db.get(User, claims["sub"])
    if user is None or not user.enabled or user.deleted_at is not None:
        raise HTTPException(403, "会话用户不可用")
    from apps.api.structured_data import execute_structured_query_api

    effective_plan, effective_ir = apply_activated_metric_contracts(
        payload.semantic_query_plan,
        payload.query_ir,
        version,
    )

    return execute_structured_query_api(
        payload=StructuredExecuteRequest(
            mapping_version_id=payload.mapping_version_id,
            plan=effective_plan,
            query_ir=effective_ir,
            max_rows=payload.max_rows,
            conversation_id=payload.conversation_id,
        ),
        user=user,
        db=db,
    )
