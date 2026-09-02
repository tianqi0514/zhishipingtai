from __future__ import annotations

import hashlib
import html
import io
import json
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import (
    get_current_user,
    get_user_permissions,
    has_space_permission,
    require_admin,
    require_permission,
    require_space_permission,
)
from apps.api.schemas import (
    AnalysisRuleCreate,
    AnalysisRuleSetCreate,
    AnalysisRuleSetUpdate,
    AnalysisRuleUpdate,
    AnalysisScenarioCreate,
    AnalysisScenarioUpdate,
    ChunkPolicyCreate,
    ChunkPolicyUpdate,
    CurationBatchCreate,
    CurationCaseUpdate,
    CurationDecisionCreate,
    CurationProfileUpdate,
    DocumentUpdate,
    ExtractionPolicyCreate,
    ExtractionPolicyUpdate,
    EntityPairCuration,
    GrantCreate,
    GovernancePolicyCreate,
    GovernancePolicyUpdate,
    KnowledgeEntityCreate,
    KnowledgeEntityUpdate,
    KnowledgeFactCreate,
    KnowledgeFactUpdate,
    LoginRequest,
    ModelConfigCreate,
    ModelConfigUpdate,
    OntologyCreate,
    OntologyTermCreate,
    OntologyTermUpdate,
    OntologyUpdate,
    OrgCreate,
    OrgUpdate,
    ParserPolicyCreate,
    ParserPolicyUpdate,
    PasswordChange,
    RoleCreate,
    RoleUpdate,
    InferenceRunCreate,
    SavedGraphQueryCreate,
    SavedGraphQueryUpdate,
    SparqlAnalysisRequest,
    SearchRequest,
    SourceCreate,
    SourceConnectionTest,
    SourceSyncRequest,
    SourceUpdate,
    SpaceCreate,
    SpaceUpdate,
    UserCreate,
    UserUpdate,
)
from apps.api.utils import apply_patch, serialize_row
from apps.worker.tasks import (
    parse_version_task,
    process_version_task,
    publish_curation_task,
    run_inference_task,
    sync_source_task,
)
from packages.platform.audit import audit
from packages.platform.analysis import inference_result_rows
from packages.platform.config import get_settings
from packages.platform.database import get_db
from packages.platform.models import (
    AnalysisRule,
    AnalysisRuleSet,
    AnalysisRuleVersion,
    AnalysisScenario,
    ApplicationFeedback,
    AuditEvent,
    CanonicalEntity,
    Chunk,
    ChunkPolicy,
    ConflictCase,
    Conversation,
    ContentElement,
    CurationBatch,
    CurationCase,
    CurationDecision,
    CurationOverlay,
    Document,
    DocumentProfile,
    DocumentVersion,
    ExtractionPolicy,
    Fact,
    GovernancePolicy,
    GraphRelease,
    IndexRelease,
    InferenceEvidence,
    InferenceRun,
    InferredFact,
    Job,
    JobStep,
    KnowledgeSpace,
    KnowledgeRelease,
    ModelConfig,
    MediaParsingPolicy,
    Ontology,
    OntologyTerm,
    OrgUnit,
    ParserPolicy,
    QueryRun,
    Role,
    SavedGraphQuery,
    SourceConnector,
    SpaceGrant,
    Tenant,
    User,
    UserRole,
)
from packages.platform.knowledge_search import execute_hybrid_search
from packages.platform.graph_release import publish_graph_snapshot
from packages.platform.curation import (
    create_decision,
    effective_chunk_text,
    effective_elements,
    effective_entity,
    effective_fact,
    effective_profile,
    rollback_decision,
    stable_fingerprint,
)
from packages.platform.curation_workbench import (
    business_label,
    compact_value,
    curation_impacts,
    summarize_fields,
)
from packages.platform.index_release import publish_index_snapshot as publish_effective_index_snapshot
from packages.platform.security import (
    create_access_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    verify_password,
)
from packages.platform.storage import object_storage
from packages.platform.media import estimate_frame_count, media_type_for, probe_media, require_cloud_confirmation
from packages.platform.media_policy import resolve_media_policy
from packages.semantica_adapter.capability import build_capability_report
from packages.semantica_adapter.file_safety import UnsafeFileError, validate_file_identity
from packages.semantica_adapter.formats import FORMAT_CAPABILITIES
from packages.semantica_adapter.ingest import ingest_source
from packages.semantica_adapter.models import test_model_connection
from packages.semantica_adapter.graph import publish_graph, validate_graph
from packages.semantica_adapter.retrieval import fuse_results, keyword_search, vector_search
from packages.semantica_adapter.analyze import (
    canonical_rule_dsl,
    compile_rule,
    parse_rule_dsl,
    run_readonly_sparql,
    validate_rule_definition,
)


router = APIRouter()
settings = get_settings()


@router.get("/formats/capabilities")
def format_capabilities(user: User = Depends(get_current_user)):
    return [
        {"suffix": suffix, **capability}
        for suffix, capability in sorted(FORMAT_CAPABILITIES.items())
    ]


def _commit(db: Session, message: str = "数据冲突，请检查编码或名称") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, message) from exc


def _mark_dispatch_target_failed(db: Session, job: Job, message: str) -> None:
    """Keep a committed business object consistent when its Celery publish fails."""
    payload = job.input or {}
    if job.job_type == "parse_document" and payload.get("version_id"):
        version = db.get(DocumentVersion, payload["version_id"])
        if version:
            version.status = "failed"
            version.error_code = "QUEUE_DISPATCH_FAILED"
            version.error_message = message
            document = db.get(Document, version.document_id)
            if document:
                document.status = "failed"
    elif job.job_type == "sync_source" and payload.get("source_id"):
        source = db.get(SourceConnector, payload["source_id"])
        if source:
            source.last_sync_status = "failed"
    elif job.job_type == "knowledge_inference" and payload.get("inference_run_id"):
        run = db.get(InferenceRun, payload["inference_run_id"])
        if run:
            run.status = "failed"
            run.error_code = "QUEUE_DISPATCH_FAILED"
            run.error_message = message
            run.finished_at = datetime.now(timezone.utc)


def _dispatch_job(
    db: Session,
    job: Job,
    task: Any,
    *,
    on_failure: Callable[[str], None] | None = None,
) -> str | None:
    """Publish a durable job and turn broker failures into retryable job state.

    The database transaction has already committed before this function is
    called.  Returning a warning prevents upload clients from retrying and
    creating duplicate business objects while still exposing a failed job that
    can be retried from the task centre.
    """
    try:
        task.delay(job.id)
        return None
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"[:4000]
        job.status = "failed"
        job.error_code = "QUEUE_DISPATCH_FAILED"
        job.error_message = message
        job.finished_at = datetime.now(timezone.utc)
        _mark_dispatch_target_failed(db, job, message)
        if on_failure:
            on_failure(message)
        db.commit()
        return "任务队列暂不可用，任务已记录为失败，可在任务列表中重试"


def _active(model):
    return model.deleted_at.is_(None)


def _must(db: Session, model, row_id: str, name: str):
    row = db.get(model, row_id)
    if row is None or getattr(row, "deleted_at", None) is not None:
        raise HTTPException(404, f"{name}不存在")
    return row


def _must_tenant(db: Session, model, row_id: str, tenant_id: str, name: str):
    """Resolve a tenant-owned row without revealing cross-tenant identifiers."""
    row = _must(db, model, row_id, name)
    if getattr(row, "tenant_id", None) != tenant_id:
        raise HTTPException(404, f"{name}不存在")
    return row


def _soft_delete(db: Session, row: Any) -> None:
    row.deleted_at = datetime.now(timezone.utc)


def _normalized_entity_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()[:500]


def _flush_or_conflict(db: Session, message: str) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, message) from exc


def _publish_graph_snapshot(db: Session, tenant_id: str, space_id: str) -> GraphRelease:
    try:
        return publish_graph_snapshot(db, tenant_id, space_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"图谱存储发布失败：{type(exc).__name__}") from exc


def _publish_index_snapshot(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    graph_release: GraphRelease,
) -> IndexRelease:
    release, _ = publish_effective_index_snapshot(
        db,
        tenant_id=tenant_id,
        space_id=space_id,
        graph_release=graph_release,
    )
    return release


def _source_for_user(db: Session, row_id: str, user: User) -> SourceConnector:
    row = _must(db, SourceConnector, row_id, "连接器")
    if row.tenant_id != user.tenant_id:
        raise HTTPException(404, "连接器不存在")
    return row


def _active_source_jobs(db: Session, tenant_id: str, source_id: str) -> list[Job]:
    candidates = db.scalars(
        select(Job).where(
            Job.tenant_id == tenant_id,
            Job.job_type == "sync_source",
            Job.status.in_(["queued", "running"]),
            Job.deleted_at.is_(None),
        )
    )
    return [row for row in candidates if (row.input or {}).get("source_id") == source_id]


def _serialize_source(db: Session, row: SourceConnector, *, syncing: bool | None = None) -> dict[str, Any]:
    data = serialize_row(row)
    data["syncing"] = bool(_active_source_jobs(db, row.tenant_id, row.id)) if syncing is None else syncing
    data["document_count"] = db.scalar(
        select(func.count()).select_from(Document).where(
            Document.tenant_id == row.tenant_id,
            Document.source_id == row.id,
            Document.deleted_at.is_(None),
        )
    ) or 0
    return data


@router.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    username = payload.username.strip()
    user = db.scalar(select(User).where(User.username == username, _active(User)))
    if user is None or not user.enabled or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    token = create_access_token(user.id, user.tenant_id, user.is_admin)
    response.set_cookie(
        "access_token",
        token,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        path="/",
        max_age=settings.access_token_minutes * 60,
    )
    user_data = serialize_row(user)
    user_data["permissions"] = get_user_permissions(db, user)
    return {"user": user_data, "access_token": token, "token_type": "bearer"}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data = serialize_row(user)
    data["role_ids"] = list(db.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id)))
    data["permissions"] = get_user_permissions(db, user)
    return data


@router.put("/auth/password")
def change_password(payload: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not payload.current_password or not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "当前密码错误")
    user.password_hash = hash_password(payload.new_password)
    audit(db, user.tenant_id, user.id, "password.change", "user", user.id)
    db.commit()
    return {"ok": True}


@router.get("/capabilities")
def capabilities(_: User = Depends(get_current_user)):
    return build_capability_report().model_dump()


@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    spaces = [s for s in db.scalars(select(KnowledgeSpace).where(KnowledgeSpace.tenant_id == user.tenant_id, _active(KnowledgeSpace))) if has_space_permission(db, user, s.id, "read")]
    space_ids = [s.id for s in spaces]
    def count(model, *criteria):
        return db.scalar(select(func.count()).select_from(model).where(*criteria)) or 0
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    recent_documents = list(
        db.scalars(
            select(Document)
            .where(Document.space_id.in_(space_ids), _active(Document))
            .order_by(Document.updated_at.desc())
            .limit(6)
        )
    ) if space_ids else []
    recent_jobs = list(
        db.scalars(
            select(Job)
            .where(Job.tenant_id == user.tenant_id, _active(Job))
            .order_by(Job.created_at.desc())
            .limit(6)
        )
    )
    profiles = list(
        db.scalars(
            select(DocumentProfile).where(
                DocumentProfile.space_id.in_(space_ids),
                _active(DocumentProfile),
            )
        )
    ) if space_ids else []
    sources = list(
        db.scalars(
            select(SourceConnector).where(
                SourceConnector.space_id.in_(space_ids),
                _active(SourceConnector),
            )
        )
    ) if space_ids else []
    conversations = list(
        db.scalars(
            select(Conversation)
            .where(
                Conversation.tenant_id == user.tenant_id,
                Conversation.user_id == user.id,
                Conversation.status != "deleted",
                _active(Conversation),
            )
            .order_by(Conversation.updated_at.desc())
            .limit(5)
        )
    )
    successful_source_statuses = {"success", "succeeded", "fetched", "unchanged"}
    successful_sources = sum(row.last_sync_status in successful_source_statuses for row in sources)
    failed_sources = sum(row.last_sync_status == "failed" for row in sources)
    return {
        "spaces": len(spaces),
        "documents": count(Document, Document.space_id.in_(space_ids), _active(Document)) if space_ids else 0,
        "documents_today": count(Document, Document.space_id.in_(space_ids), Document.created_at >= today, _active(Document)) if space_ids else 0,
        "sources": len(sources),
        "elements": count(ContentElement, ContentElement.space_id.in_(space_ids), _active(ContentElement)) if space_ids else 0,
        "running_jobs": count(Job, Job.tenant_id == user.tenant_id, Job.status.in_(["queued", "running"]), _active(Job)),
        "failed_jobs": count(Job, Job.tenant_id == user.tenant_id, Job.status == "failed", _active(Job)),
        "quality_score": round(sum(row.quality_score for row in profiles) / len(profiles), 1) if profiles else None,
        "quality_issues": sum(len(row.quality_issues or []) for row in profiles),
        "source_status": {
            "success": successful_sources,
            "failed": failed_sources,
            "pending": len(sources) - successful_sources - failed_sources,
        },
        "recent_documents": [
            {"id": row.id, "title": row.title, "space_id": row.space_id, "status": row.status, "updated_at": row.updated_at}
            for row in recent_documents
        ],
        "recent_jobs": [
            {"id": row.id, "job_type": row.job_type, "status": row.status, "progress": row.progress, "created_at": row.created_at, "error_message": row.error_message}
            for row in recent_jobs
        ],
        "recent_conversations": [
            {"id": row.id, "title": row.title, "status": row.status, "updated_at": row.updated_at}
            for row in conversations
        ],
    }


# ---- Organization and local identity -------------------------------------------------

@router.get("/org-units")
def list_orgs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [serialize_row(x) for x in db.scalars(select(OrgUnit).where(OrgUnit.tenant_id == user.tenant_id, _active(OrgUnit)).order_by(OrgUnit.sort_order, OrgUnit.name))]


@router.post("/org-units")
def create_org(payload: OrgCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if payload.parent_id:
        _must_tenant(db, OrgUnit, payload.parent_id, admin.tenant_id, "上级组织")
    row = OrgUnit(tenant_id=admin.tenant_id, **payload.model_dump())
    db.add(row); audit(db, admin.tenant_id, admin.id, "org.create", "org", row.id, payload.model_dump())
    _commit(db); return serialize_row(row)


@router.put("/org-units/{row_id}")
def update_org(row_id: str, payload: OrgUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, OrgUnit, row_id, admin.tenant_id, "组织")
    values = payload.model_dump(exclude_unset=True)
    if values.get("parent_id") == row_id:
        raise HTTPException(400, "组织不能以自身为上级")
    # Reject cycles while permitting arbitrary depth.
    parent_id = values.get("parent_id")
    seen = {row_id}
    while parent_id:
        if parent_id in seen:
            raise HTTPException(400, "组织层级存在循环")
        seen.add(parent_id)
        parent = _must_tenant(db, OrgUnit, parent_id, admin.tenant_id, "上级组织")
        parent_id = parent.parent_id
    apply_patch(row, values, {"code", "name", "parent_id", "unit_type", "sort_order", "enabled"})
    audit(db, admin.tenant_id, admin.id, "org.update", "org", row.id, values); _commit(db)
    return serialize_row(row)


@router.delete("/org-units/{row_id}")
def delete_org(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, OrgUnit, row_id, admin.tenant_id, "组织")
    children = db.scalar(select(func.count()).select_from(OrgUnit).where(OrgUnit.tenant_id == admin.tenant_id, OrgUnit.parent_id == row.id, _active(OrgUnit)))
    users = db.scalar(select(func.count()).select_from(User).where(User.tenant_id == admin.tenant_id, User.org_unit_id == row.id, _active(User)))
    if children or users:
        raise HTTPException(409, "请先迁移下级组织和用户")
    _soft_delete(db, row); audit(db, admin.tenant_id, admin.id, "org.delete", "org", row.id); db.commit()
    return {"ok": True}


@router.get("/roles")
def list_roles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [serialize_row(x) for x in db.scalars(select(Role).where(Role.tenant_id == user.tenant_id, _active(Role)).order_by(Role.name))]


@router.post("/roles")
def create_role(payload: RoleCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = Role(tenant_id=admin.tenant_id, **payload.model_dump()); db.add(row)
    audit(db, admin.tenant_id, admin.id, "role.create", "role", row.id); _commit(db); return serialize_row(row)


@router.put("/roles/{row_id}")
def update_role(row_id: str, payload: RoleUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, Role, row_id, admin.tenant_id, "角色")
    apply_patch(row, payload.model_dump(exclude_unset=True), {"name", "permissions", "enabled"})
    audit(db, admin.tenant_id, admin.id, "role.update", "role", row.id); db.commit(); return serialize_row(row)


@router.delete("/roles/{row_id}")
def delete_role(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, Role, row_id, admin.tenant_id, "角色")
    if row.builtin:
        raise HTTPException(409, "内置角色不可删除")
    db.query(UserRole).filter(UserRole.role_id == row.id).delete()
    _soft_delete(db, row); audit(db, admin.tenant_id, admin.id, "role.delete", "role", row.id); db.commit()
    return {"ok": True}


@router.get("/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = []
    for user in db.scalars(select(User).where(User.tenant_id == admin.tenant_id, _active(User)).order_by(User.username)):
        data = serialize_row(user)
        data["role_ids"] = list(db.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id)))
        rows.append(data)
    return rows


def _set_roles(db: Session, user_id: str, role_ids: list[str], tenant_id: str) -> None:
    db.query(UserRole).filter(UserRole.user_id == user_id).delete()
    for role_id in dict.fromkeys(role_ids):
        _must_tenant(db, Role, role_id, tenant_id, "角色")
        db.add(UserRole(user_id=user_id, role_id=role_id))


@router.post("/users")
def create_user(payload: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    values = payload.model_dump(exclude={"password", "role_ids"})
    if values.get("org_unit_id"):
        _must_tenant(db, OrgUnit, values["org_unit_id"], admin.tenant_id, "组织")
    row = User(tenant_id=admin.tenant_id, password_hash=hash_password(payload.password), **values)
    db.add(row); db.flush(); _set_roles(db, row.id, payload.role_ids, admin.tenant_id)
    audit(db, admin.tenant_id, admin.id, "user.create", "user", row.id); _commit(db)
    data = serialize_row(row); data["role_ids"] = payload.role_ids; return data


@router.put("/users/{row_id}")
def update_user(row_id: str, payload: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, User, row_id, admin.tenant_id, "用户"); values = payload.model_dump(exclude_unset=True)
    roles = values.pop("role_ids", None)
    if values.get("org_unit_id"):
        _must_tenant(db, OrgUnit, values["org_unit_id"], admin.tenant_id, "组织")
    apply_patch(row, values, {"display_name", "email", "org_unit_id", "is_admin", "enabled"})
    if roles is not None: _set_roles(db, row.id, roles, admin.tenant_id)
    audit(db, admin.tenant_id, admin.id, "user.update", "user", row.id); _commit(db)
    data = serialize_row(row); data["role_ids"] = list(db.scalars(select(UserRole.role_id).where(UserRole.user_id == row.id))); return data


@router.put("/users/{row_id}/password")
def reset_password(row_id: str, payload: PasswordChange, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, User, row_id, admin.tenant_id, "用户"); row.password_hash = hash_password(payload.new_password)
    audit(db, admin.tenant_id, admin.id, "user.password.reset", "user", row.id); db.commit(); return {"ok": True}


@router.delete("/users/{row_id}")
def delete_user(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if row_id == admin.id: raise HTTPException(409, "不能删除当前账号")
    row = _must_tenant(db, User, row_id, admin.tenant_id, "用户"); row.enabled = False; _soft_delete(db, row)
    audit(db, admin.tenant_id, admin.id, "user.delete", "user", row.id); db.commit(); return {"ok": True}


# ---- Runtime configuration -----------------------------------------------------------

@router.get("/model-configs")
def list_models(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [serialize_row(x) for x in db.scalars(select(ModelConfig).where(ModelConfig.tenant_id == user.tenant_id, _active(ModelConfig)).order_by(ModelConfig.model_kind, ModelConfig.name))]


@router.post("/model-configs")
def create_model(payload: ModelConfigCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    values = payload.model_dump(exclude={"api_key"}); values["api_key_encrypted"] = encrypt_secret(payload.api_key)
    if values.get("is_default"):
        db.query(ModelConfig).filter(ModelConfig.tenant_id == admin.tenant_id, ModelConfig.model_kind == payload.model_kind).update({"is_default": False})
    row = ModelConfig(tenant_id=admin.tenant_id, **values); db.add(row)
    audit(db, admin.tenant_id, admin.id, "model.create", "model_config", row.id); _commit(db); return serialize_row(row)


@router.put("/model-configs/{row_id}")
def update_model(row_id: str, payload: ModelConfigUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, ModelConfig, row_id, admin.tenant_id, "模型配置"); values = payload.model_dump(exclude_unset=True)
    key = values.pop("api_key", None); clear = values.pop("clear_api_key", False)
    if key is not None: row.api_key_encrypted = encrypt_secret(key)
    elif clear: row.api_key_encrypted = None
    target_kind = values.get("model_kind", row.model_kind)
    if values.get("is_default"):
        db.query(ModelConfig).filter(ModelConfig.tenant_id == admin.tenant_id, ModelConfig.model_kind == target_kind, ModelConfig.id != row.id).update({"is_default": False})
    apply_patch(row, values, {"name", "model_kind", "provider", "model_name", "base_url", "config", "enabled", "is_default"})
    audit(db, admin.tenant_id, admin.id, "model.update", "model_config", row.id); db.commit(); return serialize_row(row)


@router.post("/model-configs/{row_id}/test")
def test_model(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, ModelConfig, row_id, admin.tenant_id, "模型配置")
    started = time.perf_counter()
    try:
        model_secret = decrypt_secret(row.api_key_encrypted)
        credential_id = str((row.config or {}).get("credential_model_config_id") or "").strip()
        if not model_secret and credential_id:
            credential = _must_tenant(db, ModelConfig, credential_id, admin.tenant_id, "凭据模型配置")
            model_secret = decrypt_secret(credential.api_key_encrypted)
        result = test_model_connection(
            provider=row.provider, model_kind=row.model_kind, model_name=row.model_name,
            base_url=row.base_url, api_key=model_secret, config=row.config or {},
        )
        row.last_test_status = "success"; row.last_test_message = result.get("message", "连接成功")
    except Exception as exc:
        row.last_test_status = "failed"; row.last_test_message = f"{type(exc).__name__}: {exc}"[:1000]
    row.last_test_at = datetime.now(timezone.utc); db.commit()
    return {
        "status": row.last_test_status,
        "message": row.last_test_message,
        "tested_at": row.last_test_at.isoformat(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


@router.delete("/model-configs/{row_id}")
def delete_model(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, ModelConfig, row_id, admin.tenant_id, "模型配置"); _soft_delete(db, row)
    audit(db, admin.tenant_id, admin.id, "model.delete", "model_config", row.id); db.commit(); return {"ok": True}


@router.get("/parser-policies")
def list_policies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [serialize_row(x) for x in db.scalars(select(ParserPolicy).where(ParserPolicy.tenant_id == user.tenant_id, _active(ParserPolicy)).order_by(ParserPolicy.name))]


@router.post("/parser-policies")
def create_policy(payload: ParserPolicyCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if payload.is_default: db.query(ParserPolicy).filter(ParserPolicy.tenant_id == admin.tenant_id).update({"is_default": False})
    row = ParserPolicy(tenant_id=admin.tenant_id, **payload.model_dump()); db.add(row)
    audit(db, admin.tenant_id, admin.id, "parser.create", "parser_policy", row.id); _commit(db); return serialize_row(row)


@router.put("/parser-policies/{row_id}")
def update_policy(row_id: str, payload: ParserPolicyUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, ParserPolicy, row_id, admin.tenant_id, "解析策略"); values = payload.model_dump(exclude_unset=True)
    if values.get("is_default"): db.query(ParserPolicy).filter(ParserPolicy.tenant_id == admin.tenant_id, ParserPolicy.id != row.id).update({"is_default": False})
    apply_patch(row, values, {"name", "parser_type", "enable_ocr", "ocr_language", "extract_tables", "extract_images", "max_pages", "config", "enabled", "is_default"})
    audit(db, admin.tenant_id, admin.id, "parser.update", "parser_policy", row.id); db.commit(); return serialize_row(row)


@router.delete("/parser-policies/{row_id}")
def delete_policy(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must_tenant(db, ParserPolicy, row_id, admin.tenant_id, "解析策略")
    used = db.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.parser_policy_id == row.id))
    if used: raise HTTPException(409, "该策略已被文档版本使用")
    _soft_delete(db, row); audit(db, admin.tenant_id, admin.id, "parser.delete", "parser_policy", row.id); db.commit(); return {"ok": True}


# ---- Spaces and ACL ------------------------------------------------------------------

@router.get("/spaces")
def list_spaces(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(KnowledgeSpace).where(KnowledgeSpace.tenant_id == user.tenant_id, _active(KnowledgeSpace)).order_by(KnowledgeSpace.name))
    return [serialize_row(x) for x in rows if has_space_permission(db, user, x.id, "read")]


@router.post("/spaces")
def create_space(payload: SpaceCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.is_admin: raise HTTPException(403, "需要管理员权限")
    values = payload.model_dump(); values["owner_id"] = values.get("owner_id") or user.id
    if values.get("media_policy_id"):
        _must_tenant(db, MediaParsingPolicy, values["media_policy_id"], user.tenant_id, "媒体解析策略")
    _must_tenant(db, User, values["owner_id"], user.tenant_id, "空间负责人")
    row = KnowledgeSpace(tenant_id=user.tenant_id, **values); db.add(row); db.flush()
    db.add(SpaceGrant(tenant_id=user.tenant_id, space_id=row.id, subject_type="user", subject_id=row.owner_id, permission="manage", effect="allow"))
    audit(db, user.tenant_id, user.id, "space.create", "space", row.id); _commit(db); return serialize_row(row)


@router.put("/spaces/{row_id}")
def update_space(row_id: str, payload: SpaceUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must_tenant(db, KnowledgeSpace, row_id, user.tenant_id, "知识空间"); require_space_permission(db, user, row.id, "manage")
    values = payload.model_dump(exclude_unset=True)
    if values.get("owner_id"):
        _must_tenant(db, User, values["owner_id"], user.tenant_id, "空间负责人")
    if values.get("media_policy_id"):
        _must_tenant(db, MediaParsingPolicy, values["media_policy_id"], user.tenant_id, "媒体解析策略")
    apply_patch(row, values, {"code", "name", "description", "owner_id", "media_policy_id", "enabled"})
    audit(db, user.tenant_id, user.id, "space.update", "space", row.id); _commit(db); return serialize_row(row)


@router.delete("/spaces/{row_id}")
def delete_space(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must_tenant(db, KnowledgeSpace, row_id, user.tenant_id, "知识空间"); require_space_permission(db, user, row.id, "manage")
    documents = db.scalar(select(func.count()).select_from(Document).where(Document.space_id == row.id, _active(Document)))
    if documents: raise HTTPException(409, "请先删除空间内文档")
    _soft_delete(db, row); audit(db, user.tenant_id, user.id, "space.delete", "space", row.id); db.commit(); return {"ok": True}


@router.get("/spaces/{space_id}/grants")
def list_grants(space_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, space_id, "manage")
    return [serialize_row(x) for x in db.scalars(select(SpaceGrant).where(SpaceGrant.tenant_id == user.tenant_id, SpaceGrant.space_id == space_id, _active(SpaceGrant)))]


@router.post("/spaces/{space_id}/grants")
def create_grant(space_id: str, payload: GrantCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, space_id, "manage")
    models = {"user": User, "role": Role, "org": OrgUnit}; _must_tenant(db, models[payload.subject_type], payload.subject_id, user.tenant_id, "授权对象")
    existing = db.scalar(select(SpaceGrant).where(
        SpaceGrant.tenant_id == user.tenant_id,
        SpaceGrant.space_id == space_id,
        SpaceGrant.subject_type == payload.subject_type,
        SpaceGrant.subject_id == payload.subject_id,
        SpaceGrant.permission == payload.permission,
        SpaceGrant.effect == payload.effect,
        _active(SpaceGrant),
    ))
    if existing:
        return serialize_row(existing)
    row = SpaceGrant(tenant_id=user.tenant_id, space_id=space_id, **payload.model_dump()); db.add(row)
    audit(db, user.tenant_id, user.id, "grant.create", "space_grant", row.id); db.commit(); return serialize_row(row)


@router.put("/spaces/{space_id}/grants/{grant_id}")
def update_grant(space_id: str, grant_id: str, payload: GrantCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, space_id, "manage"); row = _must_tenant(db, SpaceGrant, grant_id, user.tenant_id, "授权")
    if row.space_id != space_id: raise HTTPException(404, "授权不存在")
    models = {"user": User, "role": Role, "org": OrgUnit}; _must_tenant(db, models[payload.subject_type], payload.subject_id, user.tenant_id, "授权对象")
    apply_patch(row, payload.model_dump(), {"subject_type", "subject_id", "permission", "effect"}); db.commit(); return serialize_row(row)


@router.delete("/spaces/{space_id}/grants/{grant_id}")
def delete_grant(space_id: str, grant_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, space_id, "manage"); row = _must_tenant(db, SpaceGrant, grant_id, user.tenant_id, "授权")
    if row.space_id != space_id: raise HTTPException(404, "授权不存在")
    _soft_delete(db, row); db.commit(); return {"ok": True}


# ---- Documents, versions and elements ------------------------------------------------

@router.get("/documents")
def list_documents(space_id: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(Document).where(
        Document.tenant_id == user.tenant_id,
        _active(Document),
    ).order_by(Document.updated_at.desc())
    if space_id: query = query.where(Document.space_id == space_id)
    return [serialize_row(x) for x in db.scalars(query) if has_space_permission(db, user, x.space_id, "read")]


@router.post("/documents/upload")
async def upload_document(
    space_id: str = Form(...),
    parser_policy_id: str | None = Form(None),
    media_policy_id: str | None = Form(None),
    media_policy_override: str | None = Form(None),
    cloud_processing_confirmed: bool = Form(False),
    frame_budget_confirmed: bool = Form(False),
    document_id: str | None = Form(None),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_space_permission(db, user, space_id, "write")
    filename = Path(file.filename or "upload.bin").name
    suffix = Path(filename).suffix.lower()
    capability = FORMAT_CAPABILITIES.get(suffix)
    if capability is None:
        raise HTTPException(415, f"暂不支持的文件格式：{suffix or '无扩展名'}")
    family = capability["family"]
    family_limits = {
        "image": settings.max_image_upload_bytes,
        "audio": settings.max_audio_upload_bytes,
        "video": settings.max_video_upload_bytes,
        "archive": settings.max_archive_upload_bytes,
    }
    upload_limit = int(family_limits.get(family, settings.max_upload_bytes))
    digest = hashlib.sha256(); total = 0
    with tempfile.NamedTemporaryFile(prefix="semantica-upload-", suffix=Path(filename).suffix, delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > upload_limit: raise HTTPException(413, "文件超过该媒介类型的上传大小限制")
                digest.update(chunk); temp.write(chunk)
        except Exception:
            temp_path.unlink(missing_ok=True); raise
    sha256 = digest.hexdigest()
    try:
        if total == 0:
            raise HTTPException(400, "不能上传空文件")
        try:
            identity = validate_file_identity(temp_path, file.content_type)
        except UnsafeFileError as exc:
            raise HTTPException(415, str(exc)) from exc
        if document_id:
            document = _must(db, Document, document_id, "文档")
            if document.tenant_id != user.tenant_id: raise HTTPException(404, "文档不存在")
            if document.space_id != space_id: raise HTTPException(400, "文档不属于该空间")
        else:
            document = Document(tenant_id=user.tenant_id, space_id=space_id, title=filename, owner_id=user.id)
            db.add(document); db.flush()
        existing = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id, DocumentVersion.sha256 == sha256))
        if existing: raise HTTPException(409, "相同内容的版本已存在")
        version_number = (db.scalar(select(func.max(DocumentVersion.version_number)).where(DocumentVersion.document_id == document.id)) or 0) + 1
        if parser_policy_id:
            selected_policy = _must(db, ParserPolicy, parser_policy_id, "解析策略")
            if selected_policy.tenant_id != user.tenant_id: raise HTTPException(404, "解析策略不存在")
        else:
            policy = db.scalar(select(ParserPolicy).where(ParserPolicy.tenant_id == user.tenant_id, ParserPolicy.is_default.is_(True), _active(ParserPolicy)))
            parser_policy_id = policy.id if policy else None
        object_key = f"{user.tenant_id}/{space_id}/{document.id}/{sha256}/{filename}"
        stored_content_type = (
            file.content_type
            if file.content_type and file.content_type != "application/octet-stream"
            else identity.detected_mime
        )
        resolved_media_version_id = None
        resolved_media_snapshot: dict[str, Any] = {}
        media_type = media_type_for(filename, stored_content_type)
        if media_type in {"image", "audio", "video"}:
            try:
                override = json.loads(media_policy_override) if media_policy_override else {}
            except json.JSONDecodeError as exc:
                raise HTTPException(400, "单次媒体策略覆盖不是有效 JSON") from exc
            try:
                media_version, resolved_media_snapshot = resolve_media_policy(
                    db,
                    tenant_id=user.tenant_id,
                    media_type=media_type,
                    explicit_policy_id=media_policy_id,
                    space_id=space_id,
                    override=override,
                    actor_id=user.id,
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            resolved_media_version_id = media_version.id if media_version else None
            try:
                require_cloud_confirmation(
                    resolved_media_snapshot.get("config") or {},
                    cloud_processing_confirmed,
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            if media_type == "video":
                media_probe = probe_media(temp_path)
                estimate = estimate_frame_count(
                    media_probe.get("duration_seconds") or 0,
                    resolved_media_snapshot.get("config") or {},
                )
                if (estimate["limited"] or estimate["estimated_frames"] > 100) and not frame_budget_confirmed:
                    raise HTTPException(
                        409,
                        f"预计抽取 {estimate['estimated_frames']} 个关键帧，请调整参数或确认按当前设置继续",
                    )
        object_storage.put_file(object_key, temp_path, stored_content_type)
        version = DocumentVersion(
            tenant_id=user.tenant_id, document_id=document.id, version_number=version_number,
            filename=filename, content_type=stored_content_type, size=total,
            sha256=sha256, object_key=object_key, parser_policy_id=parser_policy_id,
            media_policy_version_id=resolved_media_version_id,
            media_policy_snapshot=resolved_media_snapshot,
        )
        db.add(version); db.flush()
        job = Job(tenant_id=user.tenant_id, job_type="parse_document", idempotency_key=f"parse:{version.id}", input={"version_id": version.id})
        db.add(job); audit(db, user.tenant_id, user.id, "document.upload", "document", document.id, {"version_id": version.id}); _commit(db)
        warning = _dispatch_job(db, job, parse_version_task)
        response = {"document": serialize_row(document), "version": serialize_row(version), "job": serialize_row(job)}
        if warning:
            response["warning"] = warning
        return response
    finally:
        temp_path.unlink(missing_ok=True)


@router.get("/documents/{row_id}")
def get_document(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must(db, Document, row_id, "文档")
    if row.tenant_id != user.tenant_id: raise HTTPException(404, "文档不存在")
    require_space_permission(db, user, row.space_id, "read")
    data = serialize_row(row)
    data["versions"] = [serialize_row(x) for x in db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == row.id, _active(DocumentVersion)).order_by(DocumentVersion.version_number.desc()))]
    profile = db.scalar(
        select(DocumentProfile).where(
            DocumentProfile.version_id == row.current_version_id,
            _active(DocumentProfile),
        )
    ) if row.current_version_id else None
    data["profile"] = serialize_row(profile) if profile else None
    return data


@router.put("/documents/{row_id}")
def update_document(row_id: str, payload: DocumentUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must(db, Document, row_id, "文档")
    if row.tenant_id != user.tenant_id: raise HTTPException(404, "文档不存在")
    require_space_permission(db, user, row.space_id, "write")
    apply_patch(row, payload.model_dump(exclude_unset=True), {"title", "tags", "owner_id"})
    audit(db, user.tenant_id, user.id, "document.update", "document", row.id); db.commit(); return serialize_row(row)


@router.delete("/documents/{row_id}")
def delete_document(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must(db, Document, row_id, "文档")
    if row.tenant_id != user.tenant_id: raise HTTPException(404, "文档不存在")
    require_space_permission(db, user, row.space_id, "write")
    versions = list(
        db.scalars(
            select(DocumentVersion).where(
                DocumentVersion.document_id == row.id,
                _active(DocumentVersion),
            )
        )
    )
    version_ids = {version.id for version in versions}
    active_document_jobs = db.scalars(
        select(Job).where(
            Job.tenant_id == user.tenant_id,
            Job.job_type.in_(["parse_document", "process_knowledge"]),
            Job.status.in_(["queued", "running"]),
            Job.deleted_at.is_(None),
        )
    )
    if any((job.input or {}).get("version_id") in version_ids for job in active_document_jobs):
        raise HTTPException(409, "文档正在解析或加工，完成后才能删除")
    chunks = list(
        db.scalars(
            select(Chunk).where(
                Chunk.document_id == row.id,
                _active(Chunk),
            )
        )
    )
    chunk_ids = {chunk.id for chunk in chunks}
    facts = (
        list(
            db.scalars(
                select(Fact).where(
                    Fact.source_chunk_id.in_(chunk_ids),
                    _active(Fact),
                )
            )
        )
        if chunk_ids
        else []
    )
    had_index_release = db.scalar(
        select(IndexRelease.id)
        .where(
            IndexRelease.tenant_id == user.tenant_id,
            IndexRelease.space_id == row.space_id,
            IndexRelease.status == "published",
            _active(IndexRelease),
        )
        .limit(1)
    ) is not None
    had_graph_release = db.scalar(
        select(GraphRelease.id)
        .where(
            GraphRelease.tenant_id == user.tenant_id,
            GraphRelease.space_id == row.space_id,
            GraphRelease.status == "published",
            _active(GraphRelease),
        )
        .limit(1)
    ) is not None
    for fact in facts:
        _soft_delete(db, fact)
    for version in versions:
        for element in db.scalars(
            select(ContentElement).where(
                ContentElement.version_id == version.id,
                _active(ContentElement),
            )
        ):
            _soft_delete(db, element)
        object_storage.delete(version.object_key)
        _soft_delete(db, version)
    _soft_delete(db, row)
    audit(
        db,
        user.tenant_id,
        user.id,
        "document.delete",
        "document",
        row.id,
        {"versions": len(versions), "chunks_retained_for_provenance": len(chunks), "facts_retired": len(facts)},
    )
    db.commit()

    publication: dict[str, Any] = {"graph_release": None, "index_release": None}
    warnings: list[str] = []
    if had_graph_release or had_index_release:
        try:
            graph_release = _publish_graph_snapshot(db, user.tenant_id, row.space_id)
            db.flush()
            publication["graph_release"] = graph_release.release_number
            if had_index_release:
                index_release = _publish_index_snapshot(
                    db,
                    tenant_id=user.tenant_id,
                    space_id=row.space_id,
                    graph_release=graph_release,
                )
                publication["index_release"] = index_release.release_number
            audit(
                db,
                user.tenant_id,
                user.id,
                "document.delete.publish",
                "document",
                row.id,
                publication,
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            # Logical deletion and PostgreSQL authorization are already
            # committed. Retrieval also validates every hit against the
            # current document version, so a projection outage cannot expose
            # stale content; the next publication will rebuild the snapshot.
            warnings.append(f"外部索引清理将在下次发布时重试：{type(exc).__name__}")
    return {"ok": True, "publication": publication, "warnings": warnings}


@router.get("/versions/{version_id}/elements")
def list_elements(version_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    version = _must(db, DocumentVersion, version_id, "版本"); document = _must(db, Document, version.document_id, "文档")
    require_space_permission(db, user, document.space_id, "read")
    raw_rows = list(db.scalars(select(ContentElement).where(ContentElement.version_id == version_id, _active(ContentElement)).order_by(ContentElement.ordinal)))
    automatic_by_element = {row.element_id: row for row in raw_rows}
    rows = effective_elements(db, raw_rows, version.id)
    return {
        "total": len(rows),
        "items": [
            {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "space_id": row.space_id,
                "document_id": row.document_id,
                "version_id": row.version_id,
                "element_id": row.element_id,
                "element_type": row.element_type,
                "ordinal": row.ordinal,
                "text": row.text,
                "automatic_text": automatic_by_element[row.element_id].text,
                "field_origin": "manual" if (row.element_metadata or {}).get("curation") else "automatic",
                "structural_path": row.structural_path,
                "page_number": row.page_number,
                "bbox": row.bbox,
                "element_metadata": row.element_metadata,
                "scope_tokens": row.scope_tokens,
            }
            for row in rows[offset:offset + limit]
        ],
    }


@router.get("/versions/{version_id}/profile")
def get_version_profile(
    version_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    version = _must(db, DocumentVersion, version_id, "版本")
    document = _must(db, Document, version.document_id, "文档")
    require_space_permission(db, user, document.space_id, "read")
    try:
        return effective_profile(db, version)
    except ValueError as exc:
        raise HTTPException(404, "该版本尚未生成治理画像")


@router.post("/versions/{version_id}/profile/retry")
def retry_version_profile(
    version_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    version = _must(db, DocumentVersion, version_id, "版本")
    document = _must(db, Document, version.document_id, "文档")
    require_space_permission(db, user, document.space_id, "write")
    job = Job(
        tenant_id=user.tenant_id,
        job_type="process_knowledge",
        idempotency_key=f"profile-retry:{version.id}:{int(time.time() * 1000)}",
        input={"version_id": version.id, "force": True, "reason": "profile_retry"},
    )
    db.add(job)
    audit(db, user.tenant_id, user.id, "profile.retry", "document_version", version.id)
    db.commit()
    warning = _dispatch_job(db, job, process_version_task)
    response = serialize_row(job)
    if warning:
        response["warning"] = warning
    return response


@router.get("/versions/{version_id}/download")
def download_version(version_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    version = _must(db, DocumentVersion, version_id, "版本"); document = _must(db, Document, version.document_id, "文档")
    require_space_permission(db, user, document.space_id, "read")
    payload = object_storage.get_bytes(version.object_key)
    return StreamingResponse(io.BytesIO(payload), media_type=version.content_type, headers={"Content-Disposition": f'attachment; filename="{Path(version.filename).name}"'})


# ---- Sources and durable jobs ---------------------------------------------------------

@router.get("/sources")
def list_sources(space_id: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(SourceConnector).where(
        SourceConnector.tenant_id == user.tenant_id,
        _active(SourceConnector),
    ).order_by(SourceConnector.name)
    if space_id: query = query.where(SourceConnector.space_id == space_id)
    active_source_ids = {
        (row.input or {}).get("source_id")
        for row in db.scalars(
            select(Job).where(
                Job.tenant_id == user.tenant_id,
                Job.job_type == "sync_source",
                Job.status.in_(["queued", "running"]),
                Job.deleted_at.is_(None),
            )
        )
    }
    result = []
    for row in db.scalars(query):
        if has_space_permission(db, user, row.space_id, "read"):
            result.append(_serialize_source(db, row, syncing=row.id in active_source_ids))
    return result


@router.post("/sources")
def create_source(payload: SourceCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, payload.space_id, "write")
    if payload.media_policy_id:
        _must_tenant(db, MediaParsingPolicy, payload.media_policy_id, user.tenant_id, "媒体解析策略")
    values = payload.model_dump(exclude={"secret"}); values["secret_encrypted"] = encrypt_secret(payload.secret)
    row = SourceConnector(tenant_id=user.tenant_id, **values); db.add(row)
    audit(db, user.tenant_id, user.id, "source.create", "source", row.id); _commit(db); return serialize_row(row)


@router.put("/sources/{row_id}")
def update_source(row_id: str, payload: SourceUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _source_for_user(db, row_id, user); require_space_permission(db, user, row.space_id, "write")
    values = payload.model_dump(exclude_unset=True, exclude_none=True); secret = values.pop("secret", None); clear = values.pop("clear_secret", False)
    if "media_policy_id" in payload.model_fields_set:
        values["media_policy_id"] = payload.media_policy_id
    target_space_id = values.get("space_id")
    if target_space_id and target_space_id != row.space_id:
        require_space_permission(db, user, target_space_id, "write")
    candidate = SourceCreate(
        space_id=target_space_id or row.space_id,
        name=values.get("name", row.name),
        source_type=values.get("source_type", row.source_type),
        config=values.get("config", row.config or {}),
        media_policy_id=values.get("media_policy_id", row.media_policy_id),
        enabled=values.get("enabled", row.enabled),
    )
    values["source_type"] = candidate.source_type
    values["config"] = candidate.config
    if secret is not None: row.secret_encrypted = encrypt_secret(secret)
    elif clear: row.secret_encrypted = None
    if candidate.media_policy_id:
        _must_tenant(db, MediaParsingPolicy, candidate.media_policy_id, user.tenant_id, "媒体解析策略")
    apply_patch(row, values, {"space_id", "name", "source_type", "config", "media_policy_id", "enabled"})
    audit(db, user.tenant_id, user.id, "source.update", "source", row.id); db.commit(); return serialize_row(row)


@router.delete("/sources/{row_id}")
def delete_source(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _source_for_user(db, row_id, user); require_space_permission(db, user, row.space_id, "write")
    if _active_source_jobs(db, user.tenant_id, row.id):
        raise HTTPException(409, "数据源正在同步，完成后才能删除")
    retained_documents = db.scalar(
        select(func.count()).select_from(Document).where(
            Document.tenant_id == user.tenant_id,
            Document.source_id == row.id,
            Document.deleted_at.is_(None),
        )
    ) or 0
    row.enabled = False
    _soft_delete(db, row)
    audit(
        db,
        user.tenant_id,
        user.id,
        "source.delete",
        "source",
        row.id,
        {"retained_documents": retained_documents},
    )
    db.commit()
    return {"ok": True, "retained_documents": retained_documents}


@router.post("/sources/test")
def test_source_connection(payload: SourceConnectionTest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    source: SourceConnector | None = None
    saved_secret: str | None = None
    if payload.source_id:
        source = _source_for_user(db, payload.source_id, user)
        require_space_permission(db, user, source.space_id, "write")
        saved_secret = decrypt_secret(source.secret_encrypted)
    else:
        require_space_permission(db, user, payload.space_id or "", "write")

    secret = None if payload.clear_secret else (payload.secret or saved_secret)
    started = time.perf_counter()
    try:
        result = ingest_source(
            source_type=payload.source_type,
            source_name=source.name if source else "连接测试",
            config=payload.config,
            secret=secret,
        )
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        message = str(exc).strip() or type(exc).__name__
        audit(
            db,
            user.tenant_id,
            user.id,
            "source.test",
            "source",
            source.id if source else None,
            {"status": "failed", "elapsed_ms": elapsed_ms, "error": message[:500]},
        )
        db.commit()
        raise HTTPException(400, f"连接失败：{message[:500]}") from exc

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    audit(
        db,
        user.tenant_id,
        user.id,
        "source.test",
        "source",
        source.id if source else None,
        {"status": "success", "elapsed_ms": elapsed_ms, **result.metadata},
    )
    db.commit()
    return {
        "status": "success",
        "message": "连接成功",
        "elapsed_ms": elapsed_ms,
        "bytes": len(result.body),
        "content_type": result.content_type,
        "title": result.title,
        **result.metadata,
    }


@router.get("/sources/{row_id}/jobs")
def list_source_jobs(
    row_id: str,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _source_for_user(db, row_id, user)
    require_space_permission(db, user, row.space_id, "read")
    candidates = db.scalars(
        select(Job).where(
            Job.tenant_id == user.tenant_id,
            Job.job_type == "sync_source",
            Job.deleted_at.is_(None),
        ).order_by(Job.created_at.desc()).limit(500)
    )
    jobs = [job for job in candidates if (job.input or {}).get("source_id") == row.id][:limit]
    return {"source": _serialize_source(db, row), "items": [_serialize_job(db, job) for job in jobs]}


@router.post("/sources/{row_id}/sync")
def sync_source(
    row_id: str,
    payload: SourceSyncRequest | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _source_for_user(db, row_id, user); require_space_permission(db, user, row.space_id, "write")
    if not row.enabled: raise HTTPException(409, "连接器已停用")
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(select(func.pg_advisory_xact_lock(func.hashtext(row.id))))
    if _active_source_jobs(db, user.tenant_id, row.id):
        raise HTTPException(409, "该数据源已有同步任务正在运行")
    request_data = payload.model_dump() if payload else {}
    if (request_data.get("media_policy_id") or request_data.get("media_policy_override")) and not bool((row.config or {}).get("media_allow_sync_override")):
        raise HTTPException(409, "该数据源未允许单次同步覆盖媒体解析策略")
    if request_data.get("media_policy_id"):
        _must_tenant(db, MediaParsingPolicy, request_data["media_policy_id"], user.tenant_id, "媒体解析策略")
    job = Job(
        tenant_id=user.tenant_id,
        job_type="sync_source",
        idempotency_key=f"sync:{row.id}:{datetime.now(timezone.utc).isoformat()}",
        input={"source_id": row.id, **request_data},
    )
    db.add(job); db.commit()
    warning = _dispatch_job(db, job, sync_source_task)
    response = serialize_row(job)
    if warning:
        response["warning"] = warning
    return response


@router.get("/jobs")
def list_jobs(status: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(Job).where(Job.tenant_id == user.tenant_id).order_by(Job.created_at.desc()).limit(200)
    if status: query = query.where(Job.status == status)
    return [_serialize_job(db, x) for x in db.scalars(query)]


def _serialize_job(db: Session, row: Job) -> dict[str, Any]:
    data = serialize_row(row)
    payload = row.input or {}
    if row.job_type == "parse_document" and payload.get("version_id"):
        target = db.get(DocumentVersion, payload["version_id"])
        data["target_name"] = target.filename if target else "文档版本已删除"
    elif row.job_type == "process_knowledge" and payload.get("version_id"):
        target = db.get(DocumentVersion, payload["version_id"])
        data["target_name"] = target.filename if target else "文档版本已删除"
    elif row.job_type == "sync_source" and payload.get("source_id"):
        target = db.get(SourceConnector, payload["source_id"])
        data["target_name"] = target.name if target else "数据源已删除"
    else:
        data["target_name"] = "—"
    return data


@router.get("/jobs/{row_id}")
def get_job(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must(db, Job, row_id, "任务")
    if row.tenant_id != user.tenant_id: raise HTTPException(404, "任务不存在")
    data = _serialize_job(db, row); data["steps"] = [serialize_row(x) for x in db.scalars(select(JobStep).where(JobStep.job_id == row.id).order_by(JobStep.sequence))]
    return data


@router.post("/jobs/{row_id}/retry")
def retry_job(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    old = _must(db, Job, row_id, "任务")
    if old.tenant_id != user.tenant_id: raise HTTPException(404, "任务不存在")
    if old.status != "failed": raise HTTPException(409, "只有失败任务可重试")
    job_input = dict(old.input or {})
    if old.job_type in {"parse_document", "process_knowledge"} and job_input.get("version_id"):
        version = _must_tenant(db, DocumentVersion, job_input["version_id"], user.tenant_id, "文档版本")
        document = _must_tenant(db, Document, version.document_id, user.tenant_id, "文档")
        require_space_permission(db, user, document.space_id, "write")
    elif old.job_type in {"sync_source", "curation_publish"} and job_input.get("space_id"):
        require_space_permission(db, user, job_input["space_id"], "write")
    if old.job_type == "knowledge_inference":
        previous_run = db.get(InferenceRun, job_input.get("inference_run_id"))
        if previous_run is None or previous_run.tenant_id != user.tenant_id:
            raise HTTPException(404, "原推理运行不存在")
        require_permission = "write" if previous_run.mode == "publish" else "read"
        _require_analysis_spaces(db, user, previous_run.space_ids or [], require_permission)
        retry_run = InferenceRun(
            tenant_id=previous_run.tenant_id,
            rule_set_id=previous_run.rule_set_id,
            scenario_id=previous_run.scenario_id,
            requested_by=user.id,
            trigger_type="retry",
            mode=previous_run.mode,
            space_ids=list(previous_run.space_ids or []),
            run_input={**(previous_run.run_input or {}), "retry_of": previous_run.id},
        )
        db.add(retry_run)
        db.flush()
        job_input = {"inference_run_id": retry_run.id}
    job = Job(tenant_id=old.tenant_id, job_type=old.job_type, idempotency_key=f"retry:{old.id}:{datetime.now(timezone.utc).isoformat()}", input=job_input, max_attempts=old.max_attempts)
    db.add(job); db.commit()
    tasks = {
        "parse_document": parse_version_task,
        "sync_source": sync_source_task,
        "process_knowledge": process_version_task,
        "curation_publish": publish_curation_task,
        "knowledge_inference": run_inference_task,
    }
    task = tasks.get(job.job_type)
    if task is None:
        raise HTTPException(400, "不支持重试该任务")
    warning = _dispatch_job(db, job, task)
    response = _serialize_job(db, job)
    if warning:
        response["warning"] = warning
    return response


@router.delete("/jobs/{row_id}")
def delete_job(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, Job, row_id, "任务")
    if row.tenant_id != admin.tenant_id: raise HTTPException(404, "任务不存在")
    if row.status in {"queued", "running"}: raise HTTPException(409, "运行中的任务不可删除")
    _soft_delete(db, row); audit(db, admin.tenant_id, admin.id, "job.delete", "job", row.id); db.commit(); return {"ok": True}


@router.get("/audit-events")
def list_audit(auditor: User = Depends(require_permission("audit.read")), db: Session = Depends(get_db)):
    return [serialize_row(x) for x in db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == auditor.tenant_id).order_by(AuditEvent.created_at.desc()).limit(300))]


# ---- M5-M7 processing policy CRUD ----------------------------------------------------

def _set_only_default(db: Session, model, tenant_id: str, row_id: str) -> None:
    for row in db.scalars(select(model).where(model.tenant_id == tenant_id, model.deleted_at.is_(None))):
        row.is_default = row.id == row_id


def _delete_config_row(db: Session, row: Any, model, tenant_id: str) -> None:
    if row.is_default:
        replacement = db.scalar(
            select(model).where(
                model.tenant_id == tenant_id,
                model.id != row.id,
                model.enabled.is_(True),
                model.deleted_at.is_(None),
            )
        )
        if replacement is None:
            raise HTTPException(409, "至少保留一条可用的默认配置")
        replacement.is_default = True
    _soft_delete(db, row)


@router.get("/chunk-policies")
def list_chunk_policies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [serialize_row(row) for row in db.scalars(select(ChunkPolicy).where(ChunkPolicy.tenant_id == user.tenant_id, _active(ChunkPolicy)).order_by(ChunkPolicy.created_at))]


@router.post("/chunk-policies")
def create_chunk_policy(payload: ChunkPolicyCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = ChunkPolicy(tenant_id=admin.tenant_id, **payload.model_dump())
    db.add(row); db.flush()
    if row.is_default: _set_only_default(db, ChunkPolicy, admin.tenant_id, row.id)
    audit(db, admin.tenant_id, admin.id, "chunk_policy.create", "chunk_policy", row.id); db.commit(); return serialize_row(row)


@router.put("/chunk-policies/{row_id}")
def update_chunk_policy(row_id: str, payload: ChunkPolicyUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, ChunkPolicy, row_id, "切片策略")
    if row.tenant_id != admin.tenant_id: raise HTTPException(404, "切片策略不存在")
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    size = int(values.get("chunk_size", row.chunk_size)); overlap = int(values.get("chunk_overlap", row.chunk_overlap))
    if overlap >= size: raise HTTPException(422, "重叠长度必须小于切片长度")
    apply_patch(row, values, {"name", "method", "chunk_size", "chunk_overlap", "config", "enabled", "is_default"}); row.policy_version += 1
    if row.is_default: _set_only_default(db, ChunkPolicy, admin.tenant_id, row.id)
    audit(db, admin.tenant_id, admin.id, "chunk_policy.update", "chunk_policy", row.id); db.commit(); return serialize_row(row)


@router.delete("/chunk-policies/{row_id}")
def delete_chunk_policy(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, ChunkPolicy, row_id, "切片策略")
    if row.tenant_id != admin.tenant_id: raise HTTPException(404, "切片策略不存在")
    _delete_config_row(db, row, ChunkPolicy, admin.tenant_id); db.commit(); return {"ok": True}


@router.get("/extraction-policies")
def list_extraction_policies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [serialize_row(row) for row in db.scalars(select(ExtractionPolicy).where(ExtractionPolicy.tenant_id == user.tenant_id, _active(ExtractionPolicy)).order_by(ExtractionPolicy.created_at))]


@router.post("/extraction-policies")
def create_extraction_policy(payload: ExtractionPolicyCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    model_id = payload.model_config_id or db.scalar(select(ModelConfig.id).where(ModelConfig.tenant_id == admin.tenant_id, ModelConfig.model_kind == "llm", ModelConfig.is_default.is_(True), ModelConfig.deleted_at.is_(None)))
    if not model_id: raise HTTPException(422, "请选择大模型")
    model = _must(db, ModelConfig, model_id, "模型")
    if model.tenant_id != admin.tenant_id or model.model_kind != "llm": raise HTTPException(422, "抽取策略只能使用本租户大模型")
    row = ExtractionPolicy(tenant_id=admin.tenant_id, **{**payload.model_dump(), "model_config_id": model_id})
    db.add(row); db.flush()
    if row.is_default: _set_only_default(db, ExtractionPolicy, admin.tenant_id, row.id)
    db.commit(); return serialize_row(row)


@router.put("/extraction-policies/{row_id}")
def update_extraction_policy(row_id: str, payload: ExtractionPolicyUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, ExtractionPolicy, row_id, "抽取策略")
    if row.tenant_id != admin.tenant_id: raise HTTPException(404, "抽取策略不存在")
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    if values.get("model_config_id"):
        model = _must(db, ModelConfig, values["model_config_id"], "模型")
        if model.tenant_id != admin.tenant_id or model.model_kind != "llm": raise HTTPException(422, "抽取策略只能使用本租户大模型")
    apply_patch(row, values, {"name", "model_config_id", "min_confidence", "max_chunks", "entity_types", "relation_types", "config", "enabled", "is_default"}); row.policy_version += 1
    if row.is_default: _set_only_default(db, ExtractionPolicy, admin.tenant_id, row.id)
    db.commit(); return serialize_row(row)


@router.delete("/extraction-policies/{row_id}")
def delete_extraction_policy(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, ExtractionPolicy, row_id, "抽取策略")
    if row.tenant_id != admin.tenant_id: raise HTTPException(404, "抽取策略不存在")
    _delete_config_row(db, row, ExtractionPolicy, admin.tenant_id); db.commit(); return {"ok": True}


@router.get("/governance-policies")
def list_governance_policies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [serialize_row(row) for row in db.scalars(select(GovernancePolicy).where(GovernancePolicy.tenant_id == user.tenant_id, _active(GovernancePolicy)).order_by(GovernancePolicy.created_at))]


@router.post("/governance-policies")
def create_governance_policy(payload: GovernancePolicyCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = GovernancePolicy(tenant_id=admin.tenant_id, **payload.model_dump()); db.add(row); db.flush()
    if row.is_default: _set_only_default(db, GovernancePolicy, admin.tenant_id, row.id)
    db.commit(); return serialize_row(row)


@router.put("/governance-policies/{row_id}")
def update_governance_policy(row_id: str, payload: GovernancePolicyUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, GovernancePolicy, row_id, "治理策略")
    if row.tenant_id != admin.tenant_id: raise HTTPException(404, "治理策略不存在")
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    apply_patch(row, values, {"name", "similarity_threshold", "publish_confidence", "conflict_strategy", "config", "enabled", "is_default"}); row.policy_version += 1
    if row.is_default: _set_only_default(db, GovernancePolicy, admin.tenant_id, row.id)
    db.commit(); return serialize_row(row)


@router.delete("/governance-policies/{row_id}")
def delete_governance_policy(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, GovernancePolicy, row_id, "治理策略")
    if row.tenant_id != admin.tenant_id: raise HTTPException(404, "治理策略不存在")
    _delete_config_row(db, row, GovernancePolicy, admin.tenant_id); db.commit(); return {"ok": True}


# ---- M11-M14 governed knowledge analysis ---------------------------------------------


def _analysis_space_ids(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _require_analysis_spaces(db: Session, user: User, space_ids: list[str], permission: str) -> list[str]:
    normalized = _analysis_space_ids(space_ids)
    if not normalized:
        raise HTTPException(400, "至少选择一个知识空间")
    for space_id in normalized:
        require_space_permission(db, user, space_id, permission)
    return normalized


def _visible_analysis_spaces(db: Session, user: User, space_ids: list[str]) -> bool:
    return bool(space_ids) and all(has_space_permission(db, user, space_id, "read") for space_id in space_ids)


def _must_rule_set(db: Session, user: User, row_id: str, permission: str = "read") -> AnalysisRuleSet:
    row = _must_tenant(db, AnalysisRuleSet, row_id, user.tenant_id, "规则集")
    _require_analysis_spaces(db, user, row.space_ids or [], permission)
    return row


def _prepare_rule_definition(
    definition: dict[str, Any] | None,
    dsl: str | None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    normalized = validate_rule_definition(definition or parse_rule_dsl(dsl or ""))
    canonical = canonical_rule_dsl(normalized)
    compiled = compile_rule(
        rule_id="validation",
        rule_version_id="validation",
        definition=normalized,
        dsl=canonical,
        confidence=1.0,
    )
    return normalized, canonical, {
        "engine": "semantica.reasoning.DatalogReasoner",
        "datalog": compiled.datalog,
        "head_token": compiled.head_token,
        "head_predicate": compiled.head_predicate,
        "conditions": [list(item) for item in compiled.conditions],
    }


def _inference_jobs(db: Session, tenant_id: str) -> dict[str, Job]:
    """Resolve the newest real Celery job for each inference run."""
    result: dict[str, Job] = {}
    for job in db.scalars(
        select(Job)
        .where(Job.tenant_id == tenant_id, Job.job_type == "knowledge_inference", _active(Job))
        .order_by(Job.created_at.desc())
        .limit(1000)
    ):
        run_id = str((job.input or {}).get("inference_run_id") or "")
        if run_id and run_id not in result:
            result[run_id] = job
    return result


def _inference_run_payload(row: InferenceRun, job: Job | None = None) -> dict[str, Any]:
    data = serialize_row(row)
    if row.started_at:
        finished = row.finished_at or datetime.now(timezone.utc)
        data["duration_ms"] = max(0, round((finished - row.started_at).total_seconds() * 1000))
    else:
        data["duration_ms"] = None
    data["job_id"] = job.id if job else None
    data["job_status"] = job.status if job else None
    return data


@router.get("/analysis/rule-sets")
def list_analysis_rule_sets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = list(
        db.scalars(
            select(AnalysisRuleSet)
            .where(AnalysisRuleSet.tenant_id == user.tenant_id, _active(AnalysisRuleSet))
            .order_by(AnalysisRuleSet.name)
        )
    )
    result = []
    for row in rows:
        if not _visible_analysis_spaces(db, user, row.space_ids or []):
            continue
        data = serialize_row(row)
        data["rule_count"] = db.scalar(
            select(func.count()).select_from(AnalysisRule).where(
                AnalysisRule.rule_set_id == row.id, _active(AnalysisRule)
            )
        ) or 0
        result.append(data)
    return result


@router.post("/analysis/rule-sets")
def create_analysis_rule_set(
    payload: AnalysisRuleSetCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    values = payload.model_dump()
    values["space_ids"] = _require_analysis_spaces(db, user, values["space_ids"], "manage")
    row = AnalysisRuleSet(tenant_id=user.tenant_id, **values)
    db.add(row)
    audit(db, user.tenant_id, user.id, "analysis.rule_set.create", "analysis_rule_set", row.id)
    _commit(db, "同名规则集已存在")
    return serialize_row(row)


@router.put("/analysis/rule-sets/{row_id}")
def update_analysis_rule_set(
    row_id: str,
    payload: AnalysisRuleSetUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_rule_set(db, user, row_id, "manage")
    values = payload.model_dump(exclude_unset=True)
    if "space_ids" in values:
        values["space_ids"] = _require_analysis_spaces(db, user, values["space_ids"] or [], "manage")
    apply_patch(
        row,
        values,
        {"name", "description", "category", "space_ids", "auto_run", "auto_publish", "config", "enabled"},
    )
    audit(db, user.tenant_id, user.id, "analysis.rule_set.update", "analysis_rule_set", row.id)
    _commit(db, "同名规则集已存在")
    return serialize_row(row)


@router.delete("/analysis/rule-sets/{row_id}")
def delete_analysis_rule_set(
    row_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_rule_set(db, user, row_id, "manage")
    active_run = db.scalar(
        select(InferenceRun).where(
            InferenceRun.rule_set_id == row.id,
            InferenceRun.status.in_(["queued", "running"]),
            _active(InferenceRun),
        )
    )
    if active_run:
        raise HTTPException(409, "规则集存在运行中的推理任务")
    for rule in db.scalars(select(AnalysisRule).where(AnalysisRule.rule_set_id == row.id, _active(AnalysisRule))):
        _soft_delete(db, rule)
    for scenario in db.scalars(
        select(AnalysisScenario).where(AnalysisScenario.rule_set_id == row.id, _active(AnalysisScenario))
    ):
        scenario.rule_set_id = None
    _soft_delete(db, row)
    audit(db, user.tenant_id, user.id, "analysis.rule_set.delete", "analysis_rule_set", row.id)
    db.commit()
    return {"ok": True}


@router.get("/analysis/rule-sets/{rule_set_id}/rules")
def list_analysis_rules(
    rule_set_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _must_rule_set(db, user, rule_set_id)
    return [
        serialize_row(row)
        for row in db.scalars(
            select(AnalysisRule)
            .where(AnalysisRule.rule_set_id == rule_set_id, _active(AnalysisRule))
            .order_by(AnalysisRule.priority, AnalysisRule.name)
        )
    ]


@router.post("/analysis/rule-sets/{rule_set_id}/rules")
def create_analysis_rule(
    rule_set_id: str,
    payload: AnalysisRuleCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _must_rule_set(db, user, rule_set_id, "manage")
    values = payload.model_dump()
    raw_definition = values.pop("definition")
    normalized, dsl, compiled = _prepare_rule_definition(raw_definition, values.pop("dsl"))
    row = AnalysisRule(
        tenant_id=user.tenant_id,
        rule_set_id=rule_set_id,
        definition=normalized,
        dsl=dsl,
        head_predicate=normalized["conclusion"]["predicate"],
        body_predicates=[item["predicate"] for item in normalized["conditions"]],
        current_version=1,
        **values,
    )
    db.add(row)
    _flush_or_conflict(db, "规则集中已存在同名规则")
    version = AnalysisRuleVersion(
        tenant_id=user.tenant_id,
        rule_id=row.id,
        version=1,
        definition=normalized,
        dsl=dsl,
        compiled=compiled,
        created_by=user.id,
    )
    db.add(version)
    audit(db, user.tenant_id, user.id, "analysis.rule.create", "analysis_rule", row.id)
    _commit(db, "规则集中已存在同名规则")
    data = serialize_row(row)
    data["version_id"] = version.id
    return data


@router.post("/analysis/rules/validate")
def validate_analysis_rule(payload: AnalysisRuleCreate, user: User = Depends(get_current_user)):
    definition = payload.definition.model_dump() if payload.definition else None
    normalized, dsl, compiled = _prepare_rule_definition(definition, payload.dsl)
    return {"valid": True, "definition": normalized, "dsl": dsl, "compiled": compiled}


@router.put("/analysis/rules/{row_id}")
def update_analysis_rule(
    row_id: str,
    payload: AnalysisRuleUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, AnalysisRule, row_id, user.tenant_id, "规则")
    _must_rule_set(db, user, row.rule_set_id, "manage")
    values = payload.model_dump(exclude_unset=True)
    definition_present = "definition" in values and values["definition"] is not None
    dsl_present = "dsl" in values and values["dsl"] is not None
    version = None
    if definition_present or dsl_present:
        raw_definition = values.pop("definition", None)
        raw_dsl = values.pop("dsl", None)
        normalized, dsl, compiled = _prepare_rule_definition(raw_definition, raw_dsl)
        row.definition = normalized
        row.dsl = dsl
        row.head_predicate = normalized["conclusion"]["predicate"]
        row.body_predicates = [item["predicate"] for item in normalized["conditions"]]
        row.current_version += 1
        version = AnalysisRuleVersion(
            tenant_id=user.tenant_id,
            rule_id=row.id,
            version=row.current_version,
            definition=normalized,
            dsl=dsl,
            compiled=compiled,
            created_by=user.id,
        )
        db.add(version)
    else:
        values.pop("definition", None)
        values.pop("dsl", None)
    apply_patch(
        row,
        values,
        {"name", "description", "priority", "confidence", "valid_from", "valid_to", "enabled"},
    )
    if row.valid_from and row.valid_to and row.valid_from >= row.valid_to:
        raise HTTPException(422, "规则失效时间必须晚于生效时间")
    audit(db, user.tenant_id, user.id, "analysis.rule.update", "analysis_rule", row.id)
    _commit(db, "规则集中已存在同名规则")
    data = serialize_row(row)
    if version:
        data["version_id"] = version.id
    return data


@router.get("/analysis/rules/{row_id}/versions")
def list_analysis_rule_versions(
    row_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, AnalysisRule, row_id, user.tenant_id, "规则")
    _must_rule_set(db, user, row.rule_set_id)
    return [
        serialize_row(version)
        for version in db.scalars(
            select(AnalysisRuleVersion)
            .where(AnalysisRuleVersion.rule_id == row.id)
            .order_by(AnalysisRuleVersion.version.desc())
        )
    ]


@router.delete("/analysis/rules/{row_id}")
def delete_analysis_rule(
    row_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, AnalysisRule, row_id, user.tenant_id, "规则")
    _must_rule_set(db, user, row.rule_set_id, "manage")
    _soft_delete(db, row)
    audit(db, user.tenant_id, user.id, "analysis.rule.delete", "analysis_rule", row.id)
    db.commit()
    return {"ok": True}


@router.get("/analysis/scenarios")
def list_analysis_scenarios(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(AnalysisScenario)
        .where(AnalysisScenario.tenant_id == user.tenant_id, _active(AnalysisScenario))
        .order_by(AnalysisScenario.name)
    )
    jobs = _inference_jobs(db, user.tenant_id)
    result = []
    for row in rows:
        if not _visible_analysis_spaces(db, user, row.space_ids or []):
            continue
        data = serialize_row(row)
        last_run = db.scalar(
            select(InferenceRun)
            .where(InferenceRun.scenario_id == row.id, _active(InferenceRun))
            .order_by(InferenceRun.created_at.desc())
            .limit(1)
        )
        data["last_run"] = _inference_run_payload(last_run, jobs.get(last_run.id)) if last_run else None
        result.append(data)
    return result


@router.post("/analysis/scenarios")
def create_analysis_scenario(
    payload: AnalysisScenarioCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    values = payload.model_dump()
    values["space_ids"] = _require_analysis_spaces(db, user, values["space_ids"], "manage")
    if values.get("rule_set_id"):
        rule_set = _must_rule_set(db, user, values["rule_set_id"], "manage")
        if not set(values["space_ids"]).issubset(set(rule_set.space_ids or [])):
            raise HTTPException(400, "场景知识空间必须属于所选规则集")
    row = AnalysisScenario(tenant_id=user.tenant_id, **values)
    db.add(row)
    audit(db, user.tenant_id, user.id, "analysis.scenario.create", "analysis_scenario", row.id)
    _commit(db, "同名分析场景已存在")
    return serialize_row(row)


@router.put("/analysis/scenarios/{row_id}")
def update_analysis_scenario(
    row_id: str,
    payload: AnalysisScenarioUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, AnalysisScenario, row_id, user.tenant_id, "分析场景")
    _require_analysis_spaces(db, user, row.space_ids or [], "manage")
    values = payload.model_dump(exclude_unset=True)
    if "space_ids" in values:
        values["space_ids"] = _require_analysis_spaces(db, user, values["space_ids"] or [], "manage")
    target_rule_set = values.get("rule_set_id", row.rule_set_id)
    if target_rule_set:
        rule_set = _must_rule_set(db, user, target_rule_set, "manage")
        if not set(values.get("space_ids", row.space_ids)).issubset(set(rule_set.space_ids or [])):
            raise HTTPException(400, "场景知识空间必须属于所选规则集")
    apply_patch(
        row,
        values,
        {"name", "description", "category", "rule_set_id", "space_ids", "input_schema", "config", "enabled"},
    )
    audit(db, user.tenant_id, user.id, "analysis.scenario.update", "analysis_scenario", row.id)
    _commit(db, "同名分析场景已存在")
    return serialize_row(row)


@router.delete("/analysis/scenarios/{row_id}")
def delete_analysis_scenario(
    row_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, AnalysisScenario, row_id, user.tenant_id, "分析场景")
    _require_analysis_spaces(db, user, row.space_ids or [], "manage")
    _soft_delete(db, row)
    audit(db, user.tenant_id, user.id, "analysis.scenario.delete", "analysis_scenario", row.id)
    db.commit()
    return {"ok": True}


@router.post("/analysis/inference-runs")
def create_inference_run(
    payload: InferenceRunCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rule_set = _must_rule_set(db, user, payload.rule_set_id)
    spaces = _analysis_space_ids(payload.space_ids or rule_set.space_ids or [])
    if not set(spaces).issubset(set(rule_set.space_ids or [])):
        raise HTTPException(400, "推理范围超出规则集知识空间")
    spaces = _require_analysis_spaces(db, user, spaces, "write" if payload.mode == "publish" else "read")
    if payload.scenario_id:
        scenario = _must_tenant(db, AnalysisScenario, payload.scenario_id, user.tenant_id, "分析场景")
        if scenario.rule_set_id and scenario.rule_set_id != rule_set.id:
            raise HTTPException(400, "分析场景与规则集不匹配")
        if not set(spaces).issubset(set(scenario.space_ids or [])):
            raise HTTPException(400, "推理范围超出分析场景知识空间")
    run = InferenceRun(
        tenant_id=user.tenant_id,
        rule_set_id=rule_set.id,
        scenario_id=payload.scenario_id,
        requested_by=user.id,
        trigger_type="manual",
        mode=payload.mode,
        space_ids=spaces,
        run_input={"max_results": payload.max_results},
    )
    db.add(run)
    db.flush()
    job = Job(
        tenant_id=user.tenant_id,
        job_type="knowledge_inference",
        idempotency_key=f"inference:{run.id}",
        input={"inference_run_id": run.id},
    )
    db.add(job)
    audit(
        db, user.tenant_id, user.id, "analysis.inference.create", "inference_run", run.id,
        {"space_ids": spaces, "mode": payload.mode},
    )
    db.commit()
    warning = _dispatch_job(db, job, run_inference_task)
    data = serialize_row(run)
    data["job_id"] = job.id
    if warning:
        data["warning"] = warning
    return data


@router.get("/analysis/inference-runs")
def list_inference_runs(
    rule_set_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(InferenceRun).where(InferenceRun.tenant_id == user.tenant_id, _active(InferenceRun))
    if rule_set_id:
        _must_rule_set(db, user, rule_set_id)
        query = query.where(InferenceRun.rule_set_id == rule_set_id)
    jobs = _inference_jobs(db, user.tenant_id)
    rows = db.scalars(query.order_by(InferenceRun.created_at.desc()).limit(limit))
    return [
        _inference_run_payload(row, jobs.get(row.id))
        for row in rows
        if _visible_analysis_spaces(db, user, row.space_ids or [])
    ]


@router.get("/analysis/inference-runs/{run_id}")
def get_inference_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, InferenceRun, run_id, user.tenant_id, "推理运行")
    _require_analysis_spaces(db, user, row.space_ids or [], "read")
    data = _inference_run_payload(row, _inference_jobs(db, user.tenant_id).get(row.id))
    data["items"] = inference_result_rows(db, row.id)
    return data


@router.get("/analysis/inference-runs/{run_id}/rollback-preview")
def preview_inference_rollback(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, InferenceRun, run_id, user.tenant_id, "推理运行")
    _require_analysis_spaces(db, user, row.space_ids or [], "write")
    published = db.scalar(
        select(func.count()).select_from(InferredFact).where(
            InferredFact.run_id == row.id,
            InferredFact.status == "published",
            _active(InferredFact),
        )
    ) or 0
    return {
        "run_id": row.id,
        "can_rollback": row.mode == "publish" and row.status == "succeeded" and published > 0,
        "invalidated_facts": published,
        "space_ids": list(row.space_ids or []),
        "graph_releases_to_create": len(row.space_ids or []) if published else 0,
        "effects": ["失效本次发布的推导事实", "生成新的可追溯图谱版本", "保留历史运行与证据链"],
    }


@router.post("/analysis/inference-runs/{run_id}/rollback")
def rollback_inference_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, InferenceRun, run_id, user.tenant_id, "推理运行")
    _require_analysis_spaces(db, user, row.space_ids or [], "write")
    invalidated = 0
    for fact in db.scalars(
        select(InferredFact).where(InferredFact.run_id == row.id, InferredFact.status == "published", _active(InferredFact))
    ):
        fact.status = "invalidated"
        fact.invalidated_at = datetime.now(timezone.utc)
        invalidated += 1
    releases: dict[str, int] = {}
    for space_id in row.space_ids or []:
        release = _publish_graph_snapshot(db, user.tenant_id, space_id)
        db.flush()
        releases[space_id] = release.release_number
    audit(
        db, user.tenant_id, user.id, "analysis.inference.rollback", "inference_run", row.id,
        {"invalidated": invalidated, "graph_releases": releases},
    )
    db.commit()
    return {"ok": True, "invalidated": invalidated, "graph_releases": releases}


@router.get("/analysis/saved-queries")
def list_saved_graph_queries(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(SavedGraphQuery)
        .where(
            SavedGraphQuery.tenant_id == user.tenant_id,
            SavedGraphQuery.user_id == user.id,
            _active(SavedGraphQuery),
        )
        .order_by(SavedGraphQuery.updated_at.desc())
    )
    return [serialize_row(row) for row in rows if _visible_analysis_spaces(db, user, row.space_ids or [])]


@router.post("/analysis/saved-queries")
def create_saved_graph_query(
    payload: SavedGraphQueryCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    values = payload.model_dump()
    values["space_ids"] = _require_analysis_spaces(db, user, values["space_ids"], "read")
    row = SavedGraphQuery(tenant_id=user.tenant_id, user_id=user.id, **values)
    db.add(row)
    audit(db, user.tenant_id, user.id, "analysis.query.create", "saved_graph_query", row.id)
    _commit(db, "同名查询已存在")
    return serialize_row(row)


@router.put("/analysis/saved-queries/{row_id}")
def update_saved_graph_query(
    row_id: str,
    payload: SavedGraphQueryUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, SavedGraphQuery, row_id, user.tenant_id, "保存的查询")
    if row.user_id != user.id:
        raise HTTPException(404, "保存的查询不存在")
    values = payload.model_dump(exclude_unset=True)
    if "space_ids" in values:
        values["space_ids"] = _require_analysis_spaces(db, user, values["space_ids"] or [], "read")
    apply_patch(row, values, {"name", "query_type", "space_ids", "query_text", "config", "enabled"})
    audit(db, user.tenant_id, user.id, "analysis.query.update", "saved_graph_query", row.id)
    _commit(db, "同名查询已存在")
    return serialize_row(row)


@router.delete("/analysis/saved-queries/{row_id}")
def delete_saved_graph_query(
    row_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _must_tenant(db, SavedGraphQuery, row_id, user.tenant_id, "保存的查询")
    if row.user_id != user.id:
        raise HTTPException(404, "保存的查询不存在")
    _soft_delete(db, row)
    audit(db, user.tenant_id, user.id, "analysis.query.delete", "saved_graph_query", row.id)
    db.commit()
    return {"ok": True}


@router.post("/analysis/sparql")
def execute_analysis_sparql(
    payload: SparqlAnalysisRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    space_ids = _require_analysis_spaces(db, user, payload.space_ids, "read")
    entities = list(
        db.scalars(
            select(CanonicalEntity).where(
                CanonicalEntity.tenant_id == user.tenant_id,
                CanonicalEntity.space_id.in_(space_ids),
                _active(CanonicalEntity),
            )
        )
    )
    entity_values = {row.id: effective_entity(db, row) for row in entities}
    entities = [
        row for row in entities
        if entity_values[row.id].get("status") in {"published", "active"}
    ]
    active_entity_ids = {row.id for row in entities}
    facts = list(
        db.scalars(
            select(Fact).where(
                Fact.tenant_id == user.tenant_id,
                Fact.space_id.in_(space_ids),
                _active(Fact),
            )
        )
    )
    fact_values = {row.id: effective_fact(db, row) for row in facts}
    facts = [
        row for row in facts
        if fact_values[row.id].get("status") == "published"
        and fact_values[row.id].get("subject_entity_id") in active_entity_ids
        and (
            not fact_values[row.id].get("object_entity_id")
            or fact_values[row.id].get("object_entity_id") in active_entity_ids
        )
    ]
    inferred = list(
        db.scalars(
            select(InferredFact).where(
                InferredFact.tenant_id == user.tenant_id,
                InferredFact.space_id.in_(space_ids),
                InferredFact.status == "published",
                _active(InferredFact),
            )
        )
    )
    inferred = [
        row for row in inferred
        if row.subject_entity_id in active_entity_ids
        and (not row.object_entity_id or row.object_entity_id in active_entity_ids)
    ]
    started = time.perf_counter()
    try:
        result = run_readonly_sparql(
            entities=[
                {
                    "id": row.id,
                    "name": entity_values[row.id]["canonical_name"],
                    "type": entity_values[row.id]["entity_type"],
                }
                for row in entities
            ],
            facts=[{**serialize_row(row), **fact_values[row.id]} for row in facts],
            inferred_facts=[serialize_row(row) for row in inferred],
            query=payload.query,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    duration_ms = round((time.perf_counter() - started) * 1000)
    query_run = QueryRun(
        tenant_id=user.tenant_id,
        user_id=user.id,
        query=payload.query,
        space_ids=space_ids,
        retrieval_policy={"type": "sparql", "read_only": True},
        result_count=result["total"],
        results=result["rows"],
        metrics={"duration_ms": duration_ms, **result["projection"]},
    )
    db.add(query_run)
    db.flush()
    audit(
        db, user.tenant_id, user.id, "analysis.sparql.execute", "query", query_run.id,
        {"space_ids": space_ids, "result_count": result["total"], "duration_ms": duration_ms},
    )
    db.commit()
    return {"query_id": query_run.id, "duration_ms": duration_ms, **result}


# ---- P0-P3 human curation ------------------------------------------------------------

def _queue_curation_projection(
    db: Session,
    *,
    user: User,
    batch: CurationBatch,
    version_id: str | None = None,
    rebuild_from_elements: bool = False,
) -> Job:
    job_type = "process_knowledge" if rebuild_from_elements else "curation_publish"
    payload: dict[str, Any] = {
        "space_id": batch.space_id,
        "batch_id": batch.id,
        "curation_batch_id": batch.id,
        "reason": "human_curation",
    }
    if rebuild_from_elements:
        if not version_id:
            raise HTTPException(400, "内容治理缺少文档版本")
        payload.update({"version_id": version_id, "force": True})
    job = Job(
        tenant_id=user.tenant_id,
        job_type=job_type,
        idempotency_key=f"curation:{batch.id}:{version_id or batch.space_id}",
        input=payload,
    )
    batch.status = "publishing"
    batch.publish_error = None
    db.add(job)
    db.flush()
    return job


def _dispatch_curation_job(db: Session, job: Job, batch: CurationBatch) -> None:
    try:
        if job.job_type == "process_knowledge":
            process_version_task.delay(job.id)
        else:
            publish_curation_task.delay(job.id)
    except Exception as exc:
        job.status = "failed"
        job.error_code = "QUEUE_DISPATCH_FAILED"
        job.error_message = f"{type(exc).__name__}: {exc}"[:4000]
        job.finished_at = datetime.now(timezone.utc)
        batch.status = "publish_failed"
        batch.publish_error = job.error_message
        db.commit()
        raise HTTPException(503, "治理任务提交失败") from exc


def _curation_jobs(db: Session, tenant_id: str, batch_id: str) -> list[Job]:
    rows = db.scalars(
        select(Job).where(
            Job.tenant_id == tenant_id,
            Job.job_type.in_(["process_knowledge", "curation_publish"]),
            _active(Job),
        ).order_by(Job.created_at.desc())
    )
    return [row for row in rows if (row.input or {}).get("curation_batch_id") == batch_id]


def _curation_target_name(db: Session, decision: CurationDecision) -> str:
    if decision.document_id:
        document = db.get(Document, decision.document_id)
        if document and document.deleted_at is None:
            return document.title
    if decision.target_type == "entity":
        row = db.get(CanonicalEntity, decision.target_id)
        if row:
            return str(effective_entity(db, row).get("canonical_name") or "知识实体")
    if decision.target_type == "fact":
        row = db.get(Fact, decision.target_id)
        if row:
            values = effective_fact(db, row)
            subject = db.get(CanonicalEntity, values.get("subject_entity_id"))
            obj = db.get(CanonicalEntity, values.get("object_entity_id")) if values.get("object_entity_id") else None
            subject_name = effective_entity(db, subject).get("canonical_name") if subject else "未知主体"
            object_name = effective_entity(db, obj).get("canonical_name") if obj else values.get("object_value") or "未知客体"
            return f"{subject_name} —{values.get('predicate') or '关系'}→ {object_name}"
    if decision.target_type == "entity_pair" and isinstance(decision.after_value, dict):
        left = db.get(CanonicalEntity, decision.after_value.get("left_id"))
        right = db.get(CanonicalEntity, decision.after_value.get("right_id"))
        left_name = effective_entity(db, left).get("canonical_name") if left else decision.after_value.get("left_name")
        right_name = effective_entity(db, right).get("canonical_name") if right else decision.after_value.get("right_name")
        return f"{left_name or '实体 A'} 与 {right_name or '实体 B'}"
    return business_label("target", decision.target_type)


def _curation_decision_payload(db: Session, decision: CurationDecision) -> dict[str, Any]:
    actor = db.get(User, decision.created_by)
    return {
        **serialize_row(decision),
        "target_label": business_label("target", decision.target_type),
        "target_display": _curation_target_name(db, decision),
        "field_label": business_label("field", decision.field_path),
        "operation_label": business_label("operation", decision.operation),
        "scope_label": business_label("scope", decision.scope),
        "before_display": compact_value(decision.before_value),
        "after_display": compact_value(decision.after_value),
        "actor_name": actor.display_name if actor else "系统用户",
        "impacts": curation_impacts(decision.target_type, decision.field_path),
    }


def _curation_batch_payload(db: Session, batch: CurationBatch, *, include_decisions: bool = True) -> dict[str, Any]:
    decisions = list(db.scalars(
        select(CurationDecision).where(
            CurationDecision.batch_id == batch.id,
            _active(CurationDecision),
        ).order_by(CurationDecision.created_at)
    ))
    jobs = _curation_jobs(db, batch.tenant_id, batch.id)
    latest_job = jobs[0] if jobs else None
    release = db.scalar(
        select(KnowledgeRelease).where(
            KnowledgeRelease.curation_batch_id == batch.id,
            _active(KnowledgeRelease),
        ).order_by(KnowledgeRelease.release_number.desc()).limit(1)
    )
    graph_release = db.get(GraphRelease, release.graph_release_id) if release else None
    index_release = db.get(IndexRelease, release.index_release_id) if release else None
    actor = db.get(User, batch.created_by)
    target_names = list(dict.fromkeys(_curation_target_name(db, row) for row in decisions))
    fields = [row.field_path for row in decisions]
    impacts = list(dict.fromkeys(
        impact for row in decisions for impact in curation_impacts(row.target_type, row.field_path)
    ))
    reasons = [row.reason_note.strip() for row in decisions if row.reason_note and row.reason_note.strip()]
    instant = batch.status == "published" and not jobs
    display_status = "即时生效" if instant else business_label("batch_status", batch.status)
    progress_value = latest_job.progress if latest_job else (100 if batch.status in {"published", "rolled_back"} else 0)
    payload = {
        **serialize_row(batch),
        "display_status": display_status,
        "actor_name": actor.display_name if actor else "系统用户",
        "decision_count": len(decisions),
        "field_summary": summarize_fields(fields),
        "target_display": "、".join(target_names[:2]) + (f"等 {len(target_names)} 个对象" if len(target_names) > 2 else ""),
        "reason_note": reasons[0] if reasons else "未填写调整说明",
        "scope_label": business_label("scope", decisions[0].scope) if decisions else "—",
        "impacts": impacts,
        "progress": progress_value,
        "can_rollback": any(row.status == "active" for row in decisions),
        "job": _serialize_job(db, latest_job) if latest_job else None,
        "knowledge_release": release.release_number if release else None,
        "graph_release": graph_release.release_number if graph_release else (latest_job.result or {}).get("graph_release") if latest_job else None,
        "index_release": index_release.release_number if index_release else (latest_job.result or {}).get("index_release") if latest_job else None,
    }
    if include_decisions:
        payload["decisions"] = [_curation_decision_payload(db, row) for row in decisions]
    return payload


def _curation_case_payload(db: Session, row: CurationCase, *, detail: bool = False) -> dict[str, Any]:
    document = db.get(Document, row.document_id) if row.document_id else None
    version = db.get(DocumentVersion, row.version_id) if row.version_id else None
    evidence = dict(row.evidence or {})
    payload: dict[str, Any] = {
        **serialize_row(row),
        "document_title": document.title if document else None,
        "case_type_label": business_label("case_type", row.case_type),
        "severity_label": business_label("severity", row.severity),
        "target_label": business_label("target", row.target_type),
        "target_display": document.title if document else business_label("target", row.target_type),
        "impacts": curation_impacts(row.target_type),
        "resolution": evidence.get("resolution"),
        "resolution_note": evidence.get("resolution_note") or "",
        "deep_link": {
            "view": "documents" if document else "knowledge",
            "document_id": document.id if document else None,
            "version_id": version.id if version else None,
            "panel": "profile" if row.target_type == "document_profile" else None,
            "target_id": evidence.get("entity_id") or row.target_id,
        },
    }
    if not detail:
        return payload
    payload["evidence"] = evidence
    payload["automatic"] = {}
    payload["effective"] = {}
    payload["comparison"] = []
    payload["recommended_actions"] = []
    if row.target_type == "document_profile" and version:
        try:
            profile = effective_profile(db, version)
            payload["automatic"] = profile.get("automatic") or {}
            payload["effective"] = profile.get("effective") or {}
            payload["recommended_actions"] = profile.get("recommended_actions") or []
            payload["quality_score"] = profile.get("quality_score")
            payload["comparison"] = [
                {
                    "field": field,
                    "field_label": business_label("field", field),
                    "automatic": profile.get("automatic", {}).get(field),
                    "effective": profile.get("effective", {}).get(field),
                    "automatic_display": compact_value(profile.get("automatic", {}).get(field)),
                    "effective_display": compact_value(profile.get("effective", {}).get(field)),
                    "origin": profile.get("field_origins", {}).get(field, "automatic"),
                    "origin_label": "人工调整" if profile.get("field_origins", {}).get(field) != "automatic" else "系统生成",
                }
                for field in ("summary", "classification", "document_type", "tags", "keywords", "main_objects", "time_range")
            ]
        except ValueError:
            pass
    elif row.case_type == "fact_conflict":
        values = evidence.get("values") or []
        payload["automatic"] = {"candidate_values": values, "strategy": evidence.get("strategy")}
        payload["effective"] = {"status": evidence.get("automatic_status")}
        payload["recommended_actions"] = ["查看冲突证据后，在知识图谱中保留正确事实或修正关系"]
    return payload


@router.post("/curation/batches")
def create_curation_batch(payload: CurationBatchCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, payload.space_id, "write")
    row = CurationBatch(tenant_id=user.tenant_id, space_id=payload.space_id, name=payload.name.strip(), created_by=user.id)
    db.add(row)
    audit(db, user.tenant_id, user.id, "curation.batch.create", "curation_batch", row.id, {"space_id": row.space_id})
    db.commit()
    return serialize_row(row)


@router.get("/curation/batches")
def list_curation_batches(
    space_id: str,
    status: str | None = None,
    query: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=300),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_space_permission(db, user, space_id, "read")
    statement = select(CurationBatch).where(
        CurationBatch.tenant_id == user.tenant_id,
        CurationBatch.space_id == space_id,
        _active(CurationBatch),
    )
    if status:
        statement = statement.where(CurationBatch.status == status)
    rows = list(db.scalars(statement.order_by(CurationBatch.created_at.desc())))
    items = [_curation_batch_payload(db, row) for row in rows]
    term = query.strip().casefold()
    if term:
        items = [
            item for item in items
            if term in " ".join([
                item.get("name") or "", item.get("target_display") or "",
                item.get("actor_name") or "", item.get("field_summary") or "",
                item.get("reason_note") or "",
            ]).casefold()
        ]
    return {"total": len(items), "items": items[offset:offset + limit]}


@router.get("/curation/batches/{batch_id}")
def get_curation_batch(batch_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must_tenant(db, CurationBatch, batch_id, user.tenant_id, "治理批次")
    require_space_permission(db, user, row.space_id, "read")
    return _curation_batch_payload(db, row)


@router.post("/curation/profiles/{version_id}")
def update_curation_profile(
    version_id: str,
    payload: CurationProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    version = _must_tenant(db, DocumentVersion, version_id, user.tenant_id, "文档版本")
    document = _must_tenant(db, Document, version.document_id, user.tenant_id, "文档")
    if document.space_id != payload.space_id:
        raise HTTPException(400, "文档版本不属于所选知识空间")
    require_space_permission(db, user, document.space_id, "write")
    try:
        projected = effective_profile(db, version)
    except ValueError as exc:
        raise HTTPException(409, "该版本尚未生成可治理画像") from exc
    changes = {
        field: value for field, value in payload.changes.items()
        if value != (projected.get("effective") or {}).get(field)
    }
    if not changes:
        raise HTTPException(409, "内容没有变化")
    case = None
    if payload.case_id:
        case = _must_tenant(db, CurationCase, payload.case_id, user.tenant_id, "治理待办")
        if case.space_id != document.space_id or case.version_id != version.id:
            raise HTTPException(400, "治理待办与当前文档版本不匹配")
    batch = CurationBatch(
        tenant_id=user.tenant_id,
        space_id=document.space_id,
        name=f"调整《{document.title}》画像"[:300],
        created_by=user.id,
    )
    db.add(batch)
    db.flush()
    decisions: list[CurationDecision] = []
    try:
        for field, value in changes.items():
            decision, _, _ = create_decision(
                db,
                user=user,
                space_id=document.space_id,
                target_type="document_profile",
                target_id=version.id,
                version_id=version.id,
                field_path=field,
                operation="override",
                value=value,
                scope=payload.scope,
                reason_code="profile_correction",
                reason_note=payload.reason_note.strip(),
                batch_id=batch.id,
            )
            decisions.append(decision)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    batch.status = "published"
    batch.published_at = datetime.now(timezone.utc)
    if case:
        case.status = "handled"
        case.handled_by = user.id
        case.handled_at = datetime.now(timezone.utc)
        case.evidence = {
            **(case.evidence or {}),
            "resolution": "corrected",
            "resolution_note": payload.reason_note.strip(),
            "curation_batch_id": batch.id,
        }
    audit(
        db, user.tenant_id, user.id, "curation.profile.update", "curation_batch", batch.id,
        {"version_id": version.id, "fields": sorted(changes), "case_id": payload.case_id},
    )
    db.commit()
    return {
        "batch": _curation_batch_payload(db, batch),
        "profile": effective_profile(db, version),
        "decision_count": len(decisions),
    }


@router.get("/curation/summary")
def curation_summary(space_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, space_id, "read")
    case_rows = list(db.scalars(select(CurationCase).where(CurationCase.space_id == space_id, _active(CurationCase))))
    decision_rows = list(db.scalars(select(CurationDecision).where(CurationDecision.space_id == space_id, _active(CurationDecision))))
    latest_release = db.scalar(select(KnowledgeRelease).where(KnowledgeRelease.space_id == space_id, _active(KnowledgeRelease)).order_by(KnowledgeRelease.release_number.desc()).limit(1))
    return {
        "open_cases": sum(row.status == "open" for row in case_rows),
        "high_cases": sum(row.status == "open" and row.severity == "high" for row in case_rows),
        "active_decisions": sum(row.status == "active" for row in decision_rows),
        "published_batches": db.scalar(select(func.count()).select_from(CurationBatch).where(CurationBatch.space_id == space_id, CurationBatch.status == "published", _active(CurationBatch))) or 0,
        "knowledge_release": serialize_row(latest_release) if latest_release else None,
    }


@router.get("/curation/workbench")
def curation_workbench(
    space_id: str,
    status: str | None = None,
    severity: str | None = None,
    case_type: str | None = None,
    document_id: str | None = None,
    query: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=300),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_space_permission(db, user, space_id, "read")
    all_cases = list(db.scalars(
        select(CurationCase).where(
            CurationCase.tenant_id == user.tenant_id,
            CurationCase.space_id == space_id,
            _active(CurationCase),
        ).order_by(CurationCase.created_at.desc())
    ))
    rows = all_cases
    if status and status != "all":
        rows = [row for row in rows if row.status == status]
    if severity:
        rows = [row for row in rows if row.severity == severity]
    if case_type:
        rows = [row for row in rows if row.case_type == case_type]
    if document_id:
        rows = [row for row in rows if row.document_id == document_id]
    term = query.strip().casefold()
    items = [_curation_case_payload(db, row) for row in rows]
    if term:
        items = [
            item for item in items
            if term in " ".join([
                item.get("title") or "", item.get("reason") or "",
                item.get("document_title") or "", item.get("case_type_label") or "",
            ]).casefold()
        ]
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    # The query already returns newest first.  Python's stable sort keeps that
    # order within each severity while putting urgent work first.
    items.sort(key=lambda item: severity_rank.get(item.get("severity"), 9))
    recent_since = datetime.now(timezone.utc) - timedelta(days=7)
    recent_batches = list(db.scalars(select(CurationBatch).where(
        CurationBatch.tenant_id == user.tenant_id,
        CurationBatch.space_id == space_id,
        CurationBatch.created_at >= recent_since,
        _active(CurationBatch),
    )))
    batches = list(db.scalars(select(CurationBatch).where(
        CurationBatch.tenant_id == user.tenant_id,
        CurationBatch.space_id == space_id,
        _active(CurationBatch),
    )))
    latest_release = db.scalar(select(KnowledgeRelease).where(
        KnowledgeRelease.tenant_id == user.tenant_id,
        KnowledgeRelease.space_id == space_id,
        _active(KnowledgeRelease),
    ).order_by(KnowledgeRelease.release_number.desc()).limit(1))
    latest_profile = db.scalar(select(DocumentProfile).where(
        DocumentProfile.tenant_id == user.tenant_id,
        DocumentProfile.space_id == space_id,
        _active(DocumentProfile),
    ).order_by(DocumentProfile.generated_at.desc()).limit(1))
    document_ids = list(dict.fromkeys(row.document_id for row in all_cases if row.document_id))
    documents = [db.get(Document, row_id) for row_id in document_ids]
    return {
        "summary": {
            "open_cases": sum(row.status == "open" for row in all_cases),
            "high_cases": sum(row.status == "open" and row.severity == "high" for row in all_cases),
            "failed_batches": sum(row.status == "publish_failed" for row in batches),
            "recent_adjustments": len(recent_batches),
        },
        "total": len(items),
        "items": items[offset:offset + limit],
        "case_total": len(all_cases),
        "has_knowledge": bool(latest_release or latest_profile),
        "current_release": latest_release.release_number if latest_release else None,
        "last_governance_at": latest_profile.generated_at.isoformat() if latest_profile else None,
        "documents": [
            {"id": document.id, "title": document.title}
            for document in documents if document and document.deleted_at is None
        ],
    }


@router.get("/curation/cases")
def list_curation_cases(
    space_id: str,
    status: str | None = None,
    case_type: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_space_permission(db, user, space_id, "read")
    query = select(CurationCase).where(CurationCase.space_id == space_id, _active(CurationCase))
    if status:
        query = query.where(CurationCase.status == status)
    if case_type:
        query = query.where(CurationCase.case_type == case_type)
    rows = list(db.scalars(query.order_by(CurationCase.created_at.desc())))
    items = []
    for row in rows[offset:offset + limit]:
        data = serialize_row(row)
        document = db.get(Document, row.document_id) if row.document_id else None
        data["document_title"] = document.title if document else None
        items.append(data)
    return {"total": len(rows), "items": items}


@router.get("/curation/cases/{case_id}")
def get_curation_case(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must_tenant(db, CurationCase, case_id, user.tenant_id, "治理待办")
    require_space_permission(db, user, row.space_id, "read")
    return _curation_case_payload(db, row, detail=True)


@router.put("/curation/cases/{case_id}")
def update_curation_case(case_id: str, payload: CurationCaseUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must_tenant(db, CurationCase, case_id, user.tenant_id, "治理待办")
    require_space_permission(db, user, row.space_id, "write")
    row.status = payload.status
    row.handled_by = user.id if payload.status != "open" else None
    row.handled_at = datetime.now(timezone.utc) if payload.status != "open" else None
    resolution = payload.resolution or ("reopened" if payload.status == "open" else "ignored" if payload.status == "ignored" else "accepted_automatic")
    row.evidence = {
        **(row.evidence or {}),
        "resolution": resolution,
        "resolution_note": payload.reason_note.strip(),
        "resolved_at": datetime.now(timezone.utc).isoformat() if payload.status != "open" else None,
    }
    # Application feedback and curation are one business loop. Once a linked
    # governance case is handled, project the outcome back to feedback; when
    # reopened, return it to the governance state. Semantica's automatic
    # results stay immutable because only workflow records are synchronized.
    if row.target_type == "application_feedback":
        feedback = db.get(ApplicationFeedback, row.target_id)
        if feedback and feedback.tenant_id == user.tenant_id and feedback.deleted_at is None:
            feedback.status = "converted" if payload.status == "open" else "triaged"
    audit(db, user.tenant_id, user.id, "curation.case.update", "curation_case", row.id, {"status": row.status, "resolution": resolution})
    db.commit()
    return _curation_case_payload(db, row, detail=True)


@router.get("/curation/targets/search")
def search_curation_targets(
    space_id: str,
    query: str = Query("", max_length=200),
    target_type: str | None = None,
    limit: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_space_permission(db, user, space_id, "read")
    term = query.strip().casefold()
    items: list[dict[str, Any]] = []

    def add(item: dict[str, Any]) -> None:
        if len(items) < limit:
            items.append(item)

    if target_type in {None, "document_profile", "document"}:
        documents = list(db.scalars(select(Document).where(
            Document.tenant_id == user.tenant_id,
            Document.space_id == space_id,
            _active(Document),
        ).order_by(Document.updated_at.desc()).limit(100)))
        for document in documents:
            if term and term not in document.title.casefold() and term not in " ".join(document.tags or []).casefold():
                continue
            version = db.get(DocumentVersion, document.current_version_id) if document.current_version_id else None
            if not version or version.deleted_at is not None:
                continue
            add({
                "target_type": "document_profile",
                "target_label": "文档画像",
                "target_id": version.id,
                "document_id": document.id,
                "version_id": version.id,
                "title": document.title,
                "subtitle": f"V{version.version_number} · {version.filename}",
                "panel": "profile",
            })
    if len(items) < limit and target_type in {None, "entity"}:
        entities = db.scalars(select(CanonicalEntity).where(
            CanonicalEntity.tenant_id == user.tenant_id,
            CanonicalEntity.space_id == space_id,
            _active(CanonicalEntity),
        ).order_by(CanonicalEntity.updated_at.desc()).limit(200))
        for entity in entities:
            effective = effective_entity(db, entity)
            if effective.get("status") not in {"published", "active"}:
                continue
            text = " ".join([effective.get("canonical_name") or "", effective.get("entity_type") or "", *(effective.get("aliases") or [])])
            if term and term not in text.casefold():
                continue
            add({
                "target_type": "entity", "target_label": "知识实体", "target_id": entity.id,
                "title": effective.get("canonical_name"), "subtitle": effective.get("entity_type") or "未分类实体",
                "panel": "graph",
            })
            if len(items) >= limit:
                break
    if len(items) < limit and target_type in {None, "fact"}:
        facts = db.scalars(select(Fact).where(
            Fact.tenant_id == user.tenant_id,
            Fact.space_id == space_id,
            _active(Fact),
        ).order_by(Fact.updated_at.desc()).limit(200))
        for fact in facts:
            effective = effective_fact(db, fact)
            if effective.get("status") != "published":
                continue
            subject = db.get(CanonicalEntity, effective.get("subject_entity_id"))
            obj = db.get(CanonicalEntity, effective.get("object_entity_id")) if effective.get("object_entity_id") else None
            subject_name = effective_entity(db, subject).get("canonical_name") if subject else "未知主体"
            object_name = effective_entity(db, obj).get("canonical_name") if obj else effective.get("object_value") or "未知客体"
            title = f"{subject_name} —{effective.get('predicate') or '关系'}→ {object_name}"
            if term and term not in title.casefold():
                continue
            add({
                "target_type": "fact", "target_label": "知识关系", "target_id": fact.id,
                "title": title, "subtitle": "知识图谱关系", "panel": "graph",
            })
            if len(items) >= limit:
                break
    if len(items) < limit and target_type in {"content_element", "chunk"} and term:
        documents = {
            row.id: row for row in db.scalars(select(Document).where(
                Document.tenant_id == user.tenant_id,
                Document.space_id == space_id,
                _active(Document),
            ))
        }
        if target_type == "content_element":
            raw = list(db.scalars(select(ContentElement).where(
                ContentElement.tenant_id == user.tenant_id,
                ContentElement.space_id == space_id,
                _active(ContentElement),
            ).order_by(ContentElement.updated_at.desc()).limit(300)))
            by_version: dict[str, list[ContentElement]] = {}
            for row in raw:
                document = documents.get(row.document_id)
                if document and document.current_version_id == row.version_id:
                    by_version.setdefault(row.version_id, []).append(row)
            for version_id, rows in by_version.items():
                for row in effective_elements(db, rows, version_id):
                    if term not in row.text.casefold():
                        continue
                    document = documents.get(row.document_id)
                    add({
                        "target_type": "content_element", "target_label": "原始内容", "target_id": row.element_id,
                        "document_id": row.document_id, "version_id": row.version_id,
                        "title": document.title if document else "文档内容",
                        "subtitle": f"{row.structural_path} · {compact_value(row.text, limit=80)}",
                        "panel": "elements",
                    })
                    if len(items) >= limit:
                        break
        else:
            rows = db.scalars(select(Chunk).where(
                Chunk.tenant_id == user.tenant_id,
                Chunk.space_id == space_id,
                _active(Chunk),
            ).order_by(Chunk.updated_at.desc()).limit(300))
            for row in rows:
                document = documents.get(row.document_id)
                if not document or document.current_version_id != row.version_id:
                    continue
                try:
                    text, _ = effective_chunk_text(db, row)
                except ValueError:
                    continue
                if term not in text.casefold():
                    continue
                add({
                    "target_type": "chunk", "target_label": "检索片段", "target_id": row.chunk_id,
                    "document_id": row.document_id, "version_id": row.version_id,
                    "title": document.title, "subtitle": f"{row.structural_path} · {compact_value(text, limit=80)}",
                    "panel": "chunks",
                })
                if len(items) >= limit:
                    break
    return {"total": len(items), "items": items}


@router.get("/curation/decisions")
def list_curation_decisions(
    space_id: str,
    target_type: str | None = None,
    target_id: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_space_permission(db, user, space_id, "read")
    query = select(CurationDecision).where(CurationDecision.space_id == space_id, _active(CurationDecision))
    if target_type:
        query = query.where(CurationDecision.target_type == target_type)
    if target_id:
        query = query.where(CurationDecision.target_id == target_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(CurationDecision.created_at.desc()).offset(offset).limit(limit))
    return {"total": total, "items": [serialize_row(row) for row in rows]}


@router.post("/curation/decisions")
def add_curation_decision(payload: CurationDecisionCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, payload.space_id, "write")
    try:
        decision, batch, context = create_decision(
            db,
            user=user,
            space_id=payload.space_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            field_path=payload.field_path,
            operation=payload.operation,
            value=payload.value,
            version_id=payload.version_id,
            scope=payload.scope,
            reason_code=payload.reason_code,
            reason_note=payload.reason_note,
            base_fingerprint=payload.base_fingerprint,
            batch_id=payload.batch_id,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    job: Job | None = None
    if payload.auto_publish:
        if payload.target_type == "document_profile":
            batch.status = "published"
            batch.published_at = datetime.now(timezone.utc)
        else:
            job = _queue_curation_projection(
                db,
                user=user,
                batch=batch,
                version_id=context.version_id,
                rebuild_from_elements=payload.target_type == "content_element",
            )
    audit(db, user.tenant_id, user.id, "curation.decision.create", "curation_decision", decision.id, {"target_type": payload.target_type, "target_id": context.target_id, "operation": decision.operation, "scope": decision.scope})
    db.commit()
    if job:
        _dispatch_curation_job(db, job, batch)
    return {"decision": serialize_row(decision), "batch": serialize_row(batch), "job": serialize_row(job) if job else None, "fingerprint": context.fingerprint}


@router.post("/curation/decisions/{decision_id}/rollback")
def rollback_curation_decision(decision_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    decision = _must_tenant(db, CurationDecision, decision_id, user.tenant_id, "治理决定")
    require_space_permission(db, user, decision.space_id, "write")
    try:
        rollback_decision(db, user=user, decision=decision)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    batch = CurationBatch(tenant_id=user.tenant_id, space_id=decision.space_id, name="回滚人工治理", created_by=user.id)
    db.add(batch)
    db.flush()
    if decision.target_type == "document_profile":
        batch.status = "published"
        batch.published_at = datetime.now(timezone.utc)
        job = None
    else:
        job = _queue_curation_projection(db, user=user, batch=batch, version_id=decision.version_id, rebuild_from_elements=decision.target_type == "content_element")
    audit(db, user.tenant_id, user.id, "curation.decision.rollback", "curation_decision", decision.id, {"target_type": decision.target_type, "target_id": decision.target_id})
    db.commit()
    if job:
        _dispatch_curation_job(db, job, batch)
    return {"ok": True, "batch": serialize_row(batch), "job": serialize_row(job) if job else None}


@router.post("/curation/batches/{batch_id}/rollback")
def rollback_curation_batch(batch_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    source_batch = _must_tenant(db, CurationBatch, batch_id, user.tenant_id, "治理批次")
    require_space_permission(db, user, source_batch.space_id, "write")
    decisions = list(db.scalars(
        select(CurationDecision).where(
            CurationDecision.batch_id == source_batch.id,
            CurationDecision.status == "active",
            _active(CurationDecision),
        ).order_by(CurationDecision.created_at.desc())
    ))
    if not decisions:
        raise HTTPException(409, "该批次没有当前生效的决定")
    try:
        for decision in decisions:
            rollback_decision(db, user=user, decision=decision)
    except ValueError as exc:
        db.rollback(); raise HTTPException(409, str(exc)) from exc
    source_batch.status = "rolled_back"
    rollback_batch = CurationBatch(
        tenant_id=user.tenant_id,
        space_id=source_batch.space_id,
        name=f"回滚 · {source_batch.name}"[:300],
        created_by=user.id,
    )
    db.add(rollback_batch); db.flush()
    content_decision = next((item for item in decisions if item.target_type == "content_element"), None)
    projection_decisions = [item for item in decisions if item.target_type != "document_profile"]
    if projection_decisions:
        job = _queue_curation_projection(
            db, user=user, batch=rollback_batch,
            version_id=content_decision.version_id if content_decision else None,
            rebuild_from_elements=content_decision is not None,
        )
    else:
        rollback_batch.status = "published"
        rollback_batch.published_at = datetime.now(timezone.utc)
        job = None
    audit(db, user.tenant_id, user.id, "curation.batch.rollback", "curation_batch", source_batch.id, {"decision_count": len(decisions)})
    db.commit()
    if job:
        _dispatch_curation_job(db, job, rollback_batch)
    return {"ok": True, "rolled_back": len(decisions), "batch": serialize_row(rollback_batch), "job": serialize_row(job) if job else None}


@router.post("/curation/entities/pair")
def curate_entity_pair(payload: EntityPairCuration, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, payload.space_id, "write")
    left = _must_tenant(db, CanonicalEntity, payload.left_entity_id, user.tenant_id, "左侧实体")
    right = _must_tenant(db, CanonicalEntity, payload.right_entity_id, user.tenant_id, "右侧实体")
    if left.space_id != payload.space_id or right.space_id != payload.space_id or left.id == right.id:
        raise HTTPException(400, "实体必须是当前知识空间内两个不同节点")
    winner = None
    if payload.operation in {"merge", "must_link"}:
        winner_id = payload.winner_entity_id or left.id
        winner = left if winner_id == left.id else right if winner_id == right.id else None
        if winner is None:
            raise HTTPException(400, "保留节点必须是待合并节点之一")
    batch = CurationBatch(tenant_id=user.tenant_id, space_id=payload.space_id, name="实体合并治理" if winner else "实体拆分治理", created_by=user.id)
    db.add(batch)
    db.flush()
    pair_value = {
        "left_id": left.id,
        "right_id": right.id,
        "left_name": left.normalized_name,
        "right_name": right.normalized_name,
        "left_type": left.entity_type,
        "right_type": right.entity_type,
        "winner_id": winner.id if winner else None,
        "requested_operation": payload.operation,
    }
    constraint_operation = "must_link" if winner else "cannot_link"
    pair_id = stable_fingerprint({"space_id": payload.space_id, "entities": sorted([left.id, right.id])})
    try:
        pair_decision, _, _ = create_decision(
            db, user=user, space_id=payload.space_id, target_type="entity_pair",
            target_id=pair_id, field_path="link", operation=constraint_operation,
            value=pair_value, scope="space", reason_code="entity_resolution",
            reason_note=payload.reason_note, batch_id=batch.id,
        )
        if winner:
            loser = right if winner.id == left.id else left
            create_decision(
                db, user=user, space_id=payload.space_id, target_type="entity",
                target_id=loser.id, field_path="status", operation="reject", value="suppressed",
                scope="space", reason_code="entity_merged", reason_note=payload.reason_note,
                batch_id=batch.id,
            )
            for fact in db.scalars(select(Fact).where(Fact.space_id == payload.space_id, _active(Fact), (Fact.subject_entity_id == loser.id) | (Fact.object_entity_id == loser.id))):
                field_path = "subject_entity_id" if fact.subject_entity_id == loser.id else "object_entity_id"
                create_decision(
                    db, user=user, space_id=payload.space_id, target_type="fact",
                    target_id=fact.id, field_path=field_path, operation="override", value=winner.id,
                    scope="space", reason_code="entity_merged", reason_note=payload.reason_note,
                    batch_id=batch.id,
                )
        else:
            for entity in (left, right):
                create_decision(
                    db, user=user, space_id=payload.space_id, target_type="entity",
                    target_id=entity.id, field_path="status", operation="restore", value=entity.status,
                    scope="space", reason_code="entity_split", reason_note=payload.reason_note,
                    batch_id=batch.id,
                )
            for fact in db.scalars(select(Fact).where(
                Fact.space_id == payload.space_id,
                _active(Fact),
                (Fact.subject_entity_id.in_([left.id, right.id]))
                | (Fact.object_entity_id.in_([left.id, right.id])),
            )):
                if fact.subject_entity_id in {left.id, right.id}:
                    create_decision(
                        db, user=user, space_id=payload.space_id, target_type="fact",
                        target_id=fact.id, field_path="subject_entity_id", operation="restore",
                        value=fact.subject_entity_id, scope="space", reason_code="entity_split",
                        reason_note=payload.reason_note, batch_id=batch.id,
                    )
                if fact.object_entity_id in {left.id, right.id}:
                    create_decision(
                        db, user=user, space_id=payload.space_id, target_type="fact",
                        target_id=fact.id, field_path="object_entity_id", operation="restore",
                        value=fact.object_entity_id, scope="space", reason_code="entity_split",
                        reason_note=payload.reason_note, batch_id=batch.id,
                    )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    job = _queue_curation_projection(db, user=user, batch=batch)
    audit(db, user.tenant_id, user.id, "curation.entity_pair", "curation_decision", pair_decision.id, {"target_id": pair_id, "operation": payload.operation, "winner_id": winner.id if winner else None})
    db.commit()
    _dispatch_curation_job(db, job, batch)
    return {"decision": serialize_row(pair_decision), "batch": serialize_row(batch), "job": serialize_row(job)}


# ---- M8 ontology and graph CRUD ------------------------------------------------------

@router.get("/ontologies")
def list_ontologies(space_id: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(Ontology).where(Ontology.tenant_id == user.tenant_id, _active(Ontology)).order_by(Ontology.name)
    if space_id:
        require_space_permission(db, user, space_id, "read"); query = query.where(Ontology.space_id.in_([None, space_id]))
    return [serialize_row(row) for row in db.scalars(query)]


@router.post("/ontologies")
def create_ontology(payload: OntologyCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if payload.space_id: require_space_permission(db, admin, payload.space_id, "manage")
    row = Ontology(tenant_id=admin.tenant_id, **payload.model_dump()); db.add(row); _commit(db); return serialize_row(row)


@router.put("/ontologies/{row_id}")
def update_ontology(row_id: str, payload: OntologyUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, Ontology, row_id, "本体")
    if row.tenant_id != admin.tenant_id: raise HTTPException(404, "本体不存在")
    apply_patch(row, payload.model_dump(exclude_unset=True, exclude_none=True), {"space_id", "code", "name", "namespace", "description", "config", "enabled"}); row.version += 1; _commit(db); return serialize_row(row)


@router.delete("/ontologies/{row_id}")
def delete_ontology(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, Ontology, row_id, "本体")
    if row.tenant_id != admin.tenant_id: raise HTTPException(404, "本体不存在")
    for term in db.scalars(select(OntologyTerm).where(OntologyTerm.ontology_id == row.id, _active(OntologyTerm))): _soft_delete(db, term)
    _soft_delete(db, row); db.commit(); return {"ok": True}


@router.get("/ontologies/{ontology_id}/terms")
def list_ontology_terms(ontology_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ontology = _must(db, Ontology, ontology_id, "本体")
    if ontology.tenant_id != user.tenant_id: raise HTTPException(404, "本体不存在")
    if ontology.space_id: require_space_permission(db, user, ontology.space_id, "read")
    return [serialize_row(row) for row in db.scalars(select(OntologyTerm).where(OntologyTerm.ontology_id == ontology.id, _active(OntologyTerm)).order_by(OntologyTerm.code))]


@router.post("/ontologies/{ontology_id}/terms")
def create_ontology_term(ontology_id: str, payload: OntologyTermCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    ontology = _must(db, Ontology, ontology_id, "本体")
    if ontology.tenant_id != admin.tenant_id: raise HTTPException(404, "本体不存在")
    row = OntologyTerm(ontology_id=ontology.id, **payload.model_dump()); db.add(row); ontology.version += 1; _commit(db); return serialize_row(row)


@router.put("/ontology-terms/{row_id}")
def update_ontology_term(row_id: str, payload: OntologyTermUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, OntologyTerm, row_id, "词条"); ontology = _must(db, Ontology, row.ontology_id, "本体")
    if ontology.tenant_id != admin.tenant_id: raise HTTPException(404, "词条不存在")
    apply_patch(row, payload.model_dump(exclude_unset=True, exclude_none=True), {"code", "label", "term_type", "parent_code", "aliases", "definition", "constraints", "enabled"}); ontology.version += 1; _commit(db); return serialize_row(row)


@router.delete("/ontology-terms/{row_id}")
def delete_ontology_term(row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _must(db, OntologyTerm, row_id, "词条"); ontology = _must(db, Ontology, row.ontology_id, "本体")
    if ontology.tenant_id != admin.tenant_id: raise HTTPException(404, "词条不存在")
    _soft_delete(db, row); ontology.version += 1; db.commit(); return {"ok": True}


@router.get("/knowledge/entities")
def list_entities(space_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, space_id, "read")
    query = select(CanonicalEntity).where(CanonicalEntity.space_id == space_id, _active(CanonicalEntity)).order_by(CanonicalEntity.canonical_name)
    items = []
    for row in db.scalars(query):
        effective = effective_entity(db, row)
        if effective.get("status") not in {"published", "active"}:
            continue
        items.append({**serialize_row(row), **effective})
    return {"total": len(items), "items": items[offset:offset + limit]}


@router.post("/knowledge/entities")
def create_knowledge_entity(payload: KnowledgeEntityCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, payload.space_id, "write")
    name = payload.canonical_name.strip()
    row = CanonicalEntity(
        tenant_id=user.tenant_id,
        space_id=payload.space_id,
        canonical_name=name,
        normalized_name=_normalized_entity_name(name),
        entity_type=payload.entity_type.strip(),
        aliases=sorted({item.strip() for item in payload.aliases if item.strip() and item.strip() != name}),
        properties={**payload.properties, "curation_origin": "manual"},
        confidence=payload.confidence,
        source_count=0,
        scope_tokens=[],
        status=payload.status,
    )
    db.add(row)
    _flush_or_conflict(db, "该知识空间中已存在同名同类型节点")
    audit(db, user.tenant_id, user.id, "knowledge.entity.create", "canonical_entity", row.id, {"space_id": row.space_id})
    release = _publish_graph_snapshot(db, user.tenant_id, row.space_id)
    _commit(db)
    data = serialize_row(row); data["graph_release"] = release.release_number
    return data


@router.put("/knowledge/entities/{row_id}")
def update_knowledge_entity(row_id: str, payload: KnowledgeEntityUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must(db, CanonicalEntity, row_id, "知识节点")
    if row.tenant_id != user.tenant_id: raise HTTPException(404, "知识节点不存在")
    require_space_permission(db, user, row.space_id, "write")
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    reason_note = str(values.pop("reason_note", "") or "").strip()
    if "canonical_name" in values:
        values["canonical_name"] = values["canonical_name"].strip()
    if "entity_type" in values: values["entity_type"] = values["entity_type"].strip()
    if "aliases" in values:
        values["aliases"] = sorted({item.strip() for item in values["aliases"] if item.strip() and item.strip() != values.get("canonical_name", row.canonical_name)})
    if not values:
        raise HTTPException(400, "没有需要保存的字段")
    batch = CurationBatch(tenant_id=user.tenant_id, space_id=row.space_id, name="实体人工修正", created_by=user.id)
    db.add(batch); db.flush()
    try:
        decisions = [
            create_decision(
                db, user=user, space_id=row.space_id, target_type="entity", target_id=row.id,
                field_path=field, operation="override", value=value, scope="space",
                reason_code="manual_graph_edit", reason_note=reason_note, batch_id=batch.id,
            )[0]
            for field, value in values.items()
        ]
    except ValueError as exc:
        db.rollback(); raise HTTPException(409, str(exc)) from exc
    job = _queue_curation_projection(db, user=user, batch=batch)
    audit(db, user.tenant_id, user.id, "knowledge.entity.update", "canonical_entity", row.id, {"decision_ids": [item.id for item in decisions]})
    db.commit(); _dispatch_curation_job(db, job, batch)
    return {**serialize_row(row), **effective_entity(db, row), "curation_batch_id": batch.id, "job_id": job.id}


@router.delete("/knowledge/entities/{row_id}")
def delete_knowledge_entity(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must(db, CanonicalEntity, row_id, "知识节点")
    if row.tenant_id != user.tenant_id: raise HTTPException(404, "知识节点不存在")
    require_space_permission(db, user, row.space_id, "write")
    related = [
        fact
        for fact in db.scalars(
            select(Fact).where(
                Fact.space_id == row.space_id,
                (Fact.subject_entity_id == row.id) | (Fact.object_entity_id == row.id),
                _active(Fact),
            )
        )
        if effective_fact(db, fact).get("status") == "published"
    ]
    batch = CurationBatch(tenant_id=user.tenant_id, space_id=row.space_id, name="实体屏蔽", created_by=user.id)
    db.add(batch); db.flush()
    try:
        create_decision(db, user=user, space_id=row.space_id, target_type="entity", target_id=row.id, field_path="status", operation="reject", value="suppressed", scope="space", reason_code="manual_graph_delete", batch_id=batch.id)
    except ValueError as exc:
        db.rollback(); raise HTTPException(409, str(exc)) from exc
    job = _queue_curation_projection(db, user=user, batch=batch)
    audit(db, user.tenant_id, user.id, "knowledge.entity.delete", "canonical_entity", row.id, {"affected_facts": len(related), "soft_suppressed": True})
    db.commit(); _dispatch_curation_job(db, job, batch)
    return {
        "ok": True,
        "affected_facts": len(related),
        # Compatibility alias for existing graph clients.  Facts are hidden
        # from the effective projection, not physically deleted.
        "removed_facts": len(related),
        "curation_batch_id": batch.id,
        "job_id": job.id,
    }


@router.get("/knowledge/facts")
def list_facts(space_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), include_inferred: bool = True, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, space_id, "read")
    query = select(Fact).where(Fact.space_id == space_id, _active(Fact)).order_by(Fact.created_at.desc())
    asserted_total = db.scalar(
        select(func.count()).select_from(Fact).where(Fact.space_id == space_id, _active(Fact))
    ) or 0
    inferred_total = 0
    items = []
    asserted_total = 0
    for row in db.scalars(query):
        effective = effective_fact(db, row)
        if effective.get("status") != "published":
            continue
        subject = db.get(CanonicalEntity, effective["subject_entity_id"])
        obj = db.get(CanonicalEntity, effective["object_entity_id"]) if effective.get("object_entity_id") else None
        subject_effective = effective_entity(db, subject) if subject else None
        object_effective = effective_entity(db, obj) if obj else None
        if not subject_effective or subject_effective.get("status") != "published":
            continue
        if obj and (not object_effective or object_effective.get("status") != "published"):
            continue
        data = {**serialize_row(row), **effective}
        data.update({"subject_name": subject_effective["canonical_name"] if subject_effective else "—", "object_name": object_effective["canonical_name"] if object_effective else effective.get("object_value") or "—", "origin_type": "asserted", "editable": True}); items.append(data)
        asserted_total += 1
    if include_inferred:
        inferred_rows = db.scalars(
            select(InferredFact).where(
                InferredFact.space_id == space_id,
                InferredFact.status == "published",
                _active(InferredFact),
            ).order_by(InferredFact.created_at.desc()).limit(offset + limit)
        )
        for row in inferred_rows:
            subject = db.get(CanonicalEntity, row.subject_entity_id)
            obj = db.get(CanonicalEntity, row.object_entity_id) if row.object_entity_id else None
            subject_effective = effective_entity(db, subject) if subject else None
            object_effective = effective_entity(db, obj) if obj else None
            if not subject_effective or subject_effective.get("status") != "published":
                continue
            if obj and (not object_effective or object_effective.get("status") != "published"):
                continue
            data = serialize_row(row)
            data.update({
                "subject_name": subject_effective["canonical_name"],
                "object_name": object_effective["canonical_name"] if object_effective else row.object_value or "—",
                "source_chunk_id": None,
                "origin_type": "inferred",
                "editable": False,
            })
            items.append(data)
            inferred_total += 1
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    total = asserted_total + inferred_total
    return {"total": total, "items": items[offset:offset + limit]}


def _knowledge_fact_entities(db: Session, tenant_id: str, space_id: str, subject_id: str, object_id: str | None) -> tuple[CanonicalEntity, CanonicalEntity | None]:
    subject = _must(db, CanonicalEntity, subject_id, "主体节点")
    obj = _must(db, CanonicalEntity, object_id, "客体节点") if object_id else None
    if subject.tenant_id != tenant_id or subject.space_id != space_id or (obj and (obj.tenant_id != tenant_id or obj.space_id != space_id)):
        raise HTTPException(400, "关系两端必须属于当前知识空间")
    if obj and obj.id == subject.id: raise HTTPException(400, "暂不支持节点自关联")
    return subject, obj


@router.post("/knowledge/facts")
def create_knowledge_fact(payload: KnowledgeFactCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, payload.space_id, "write")
    _knowledge_fact_entities(db, user.tenant_id, payload.space_id, payload.subject_entity_id, payload.object_entity_id)
    duplicate = db.scalar(select(Fact).where(
        Fact.space_id == payload.space_id,
        Fact.subject_entity_id == payload.subject_entity_id,
        Fact.predicate == payload.predicate.strip(),
        Fact.object_entity_id == payload.object_entity_id,
        Fact.object_value == payload.object_value,
        _active(Fact),
    ))
    if duplicate: raise HTTPException(409, "相同的知识关系已存在")
    row = Fact(
        tenant_id=user.tenant_id,
        space_id=payload.space_id,
        subject_entity_id=payload.subject_entity_id,
        predicate=payload.predicate.strip(),
        object_entity_id=payload.object_entity_id,
        object_value=payload.object_value,
        source_chunk_id=None,
        confidence=payload.confidence,
        scope_tokens=[],
        status=payload.status,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
    )
    db.add(row); _flush_or_conflict(db, "相同的知识关系已存在")
    audit(db, user.tenant_id, user.id, "knowledge.fact.create", "fact", row.id, {"space_id": row.space_id})
    release = _publish_graph_snapshot(db, user.tenant_id, row.space_id)
    _commit(db)
    data = serialize_row(row); data["graph_release"] = release.release_number
    return data


@router.put("/knowledge/facts/{row_id}")
def update_knowledge_fact(row_id: str, payload: KnowledgeFactUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must(db, Fact, row_id, "知识关系")
    if row.tenant_id != user.tenant_id: raise HTTPException(404, "知识关系不存在")
    require_space_permission(db, user, row.space_id, "write")
    current = effective_fact(db, row)
    values = payload.model_dump(exclude_unset=True)
    reason_note = str(values.pop("reason_note", "") or "").strip()
    subject_id = values.get("subject_entity_id") or current["subject_entity_id"]
    object_id = values.get("object_entity_id", current["object_entity_id"])
    object_value = values.get("object_value", current["object_value"])
    if object_id and object_value:
        if "object_entity_id" in values and "object_value" not in values:
            object_value = None
            values["object_value"] = None
        elif "object_value" in values and "object_entity_id" not in values:
            object_id = None
            values["object_entity_id"] = None
        else:
            raise HTTPException(400, "关系必须且只能选择一个客体节点或客体值")
    if not object_id and not (object_value or "").strip(): raise HTTPException(400, "关系必须选择客体节点或填写客体值")
    _knowledge_fact_entities(db, user.tenant_id, row.space_id, subject_id, object_id)
    if "object_value" in values:
        values["object_value"] = object_value.strip() if object_value else None
    if "predicate" in values: values["predicate"] = values["predicate"].strip()
    if not values:
        raise HTTPException(400, "没有需要保存的字段")
    batch = CurationBatch(tenant_id=user.tenant_id, space_id=row.space_id, name="关系人工修正", created_by=user.id)
    db.add(batch); db.flush()
    try:
        decisions = [
            create_decision(
                db, user=user, space_id=row.space_id, target_type="fact", target_id=row.id,
                field_path=field, operation="override", value=value, scope="space",
                reason_code="manual_graph_edit", reason_note=reason_note, batch_id=batch.id,
            )[0]
            for field, value in values.items()
        ]
    except ValueError as exc:
        db.rollback(); raise HTTPException(409, str(exc)) from exc
    job = _queue_curation_projection(db, user=user, batch=batch)
    audit(db, user.tenant_id, user.id, "knowledge.fact.update", "fact", row.id, {"decision_ids": [item.id for item in decisions]})
    db.commit(); _dispatch_curation_job(db, job, batch)
    return {**serialize_row(row), **effective_fact(db, row), "curation_batch_id": batch.id, "job_id": job.id}


@router.delete("/knowledge/facts/{row_id}")
def delete_knowledge_fact(row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must(db, Fact, row_id, "知识关系")
    if row.tenant_id != user.tenant_id: raise HTTPException(404, "知识关系不存在")
    require_space_permission(db, user, row.space_id, "write")
    batch = CurationBatch(tenant_id=user.tenant_id, space_id=row.space_id, name="关系屏蔽", created_by=user.id)
    db.add(batch); db.flush()
    try:
        create_decision(db, user=user, space_id=row.space_id, target_type="fact", target_id=row.id, field_path="status", operation="reject", value="suppressed", scope="space", reason_code="manual_graph_delete", batch_id=batch.id)
    except ValueError as exc:
        db.rollback(); raise HTTPException(409, str(exc)) from exc
    job = _queue_curation_projection(db, user=user, batch=batch)
    audit(db, user.tenant_id, user.id, "knowledge.fact.delete", "fact", row.id, {"soft_suppressed": True})
    db.commit(); _dispatch_curation_job(db, job, batch)
    return {"ok": True, "curation_batch_id": batch.id, "job_id": job.id}


@router.get("/knowledge/conflicts")
def list_conflicts(space_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, space_id, "read")
    valid_chunk_ids = set(
        db.scalars(
            select(Chunk.id).where(
                Chunk.space_id == space_id,
                _active(Chunk),
            )
        )
    )
    rows = db.scalars(
        select(ConflictCase)
        .where(ConflictCase.space_id == space_id, _active(ConflictCase))
        .order_by(ConflictCase.created_at.desc())
        .limit(300)
    )
    return [
        serialize_row(row)
        for row in rows
        if set(row.source_chunk_ids or []).issubset(valid_chunk_ids)
    ]


@router.get("/knowledge/releases")
def list_releases(space_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, space_id, "read")
    return {
        "graphs": [serialize_row(row) for row in db.scalars(select(GraphRelease).where(GraphRelease.space_id == space_id, _active(GraphRelease)).order_by(GraphRelease.release_number.desc()))],
        "indexes": [serialize_row(row) for row in db.scalars(select(IndexRelease).where(IndexRelease.space_id == space_id, _active(IndexRelease)).order_by(IndexRelease.release_number.desc()))],
        "knowledge": [serialize_row(row) for row in db.scalars(select(KnowledgeRelease).where(KnowledgeRelease.space_id == space_id, _active(KnowledgeRelease)).order_by(KnowledgeRelease.release_number.desc()))],
    }


# ---- M5 processing and M10 authorized hybrid retrieval -------------------------------

@router.get("/versions/{version_id}/chunks")
def list_chunks(version_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    version = _must(db, DocumentVersion, version_id, "版本"); document = _must(db, Document, version.document_id, "文档"); require_space_permission(db, user, document.space_id, "read")
    items = []
    for row in db.scalars(select(Chunk).where(Chunk.version_id == version_id, _active(Chunk)).order_by(Chunk.ordinal)):
        try:
            text, metadata = effective_chunk_text(db, row)
        except ValueError:
            continue
        items.append({
            **serialize_row(row),
            "text": text,
            "automatic_text": row.text,
            "automatic_boost": 1.0,
            "field_origin": "manual" if metadata.get("curation_decision_id") else "automatic",
            **metadata,
        })
    return {"total": len(items), "items": items[offset:offset + limit]}


@router.post("/documents/{row_id}/process")
def process_document(row_id: str, force: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    document = _must(db, Document, row_id, "文档"); require_space_permission(db, user, document.space_id, "write")
    if not document.current_version_id: raise HTTPException(409, "文档没有可加工版本")
    active = list(db.scalars(select(Job).where(Job.tenant_id == user.tenant_id, Job.job_type == "process_knowledge", Job.status.in_(["queued", "running"]), _active(Job))))
    if any((row.input or {}).get("version_id") == document.current_version_id for row in active): raise HTTPException(409, "该文档已有知识加工任务")
    job = Job(tenant_id=user.tenant_id, job_type="process_knowledge", idempotency_key=f"knowledge:{document.current_version_id}:{datetime.now(timezone.utc).isoformat()}" if force else f"knowledge-manual:{document.current_version_id}:{datetime.now(timezone.utc).isoformat()}", input={"version_id": document.current_version_id, "force": force})
    db.add(job); db.commit()
    warning = _dispatch_job(db, job, process_version_task)
    response = serialize_row(job)
    if warning:
        response["warning"] = warning
    return response


@router.get("/fragments/{chunk_id}")
def get_fragment(chunk_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _must(db, Chunk, chunk_id, "知识片段")
    if row.tenant_id != user.tenant_id:
        raise HTTPException(404, "知识片段不存在")
    require_space_permission(db, user, row.space_id, "read")
    document = db.get(Document, row.document_id)
    version = db.get(DocumentVersion, row.version_id)
    if document is None or document.tenant_id != user.tenant_id or version is None:
        raise HTTPException(404, "知识片段来源不存在")
    try:
        text, curation = effective_chunk_text(db, row, include_superseded=True)
    except ValueError as exc:
        raise HTTPException(404, "知识片段已被人工屏蔽") from exc
    return {
        **serialize_row(row),
        "text": text,
        "curation": curation,
        "document_title": document.title,
        "document_tags": document.tags,
        "document_version": version.version_number,
        "source_filename": version.filename,
        "start_seconds": (row.source_span or {}).get("time_start"),
        "end_seconds": (row.source_span or {}).get("time_end"),
        "media_type": media_type_for(version.filename, version.content_type),
        "media_url": f"/api/v1/documents/{document.id}/media-content" if media_type_for(version.filename, version.content_type) in {"audio", "video"} else None,
        "document_deleted": document.deleted_at is not None,
        "version_deleted": version.deleted_at is not None,
        "has_access": True,
    }


@router.post("/search")
def search_knowledge(payload: SearchRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    all_spaces = list(db.scalars(select(KnowledgeSpace).where(KnowledgeSpace.tenant_id == user.tenant_id, KnowledgeSpace.enabled.is_(True), _active(KnowledgeSpace))))
    requested = set(payload.space_ids or [row.id for row in all_spaces])
    allowed = [row.id for row in all_spaces if row.id in requested and has_space_permission(db, user, row.id, "read")]
    denied = sorted(requested - set(allowed))
    if denied: raise HTTPException(403, "查询包含无权访问的知识空间")
    if not allowed:
        return {
            "query_id": None,
            "normalized_query": payload.query.strip(),
            "items": [],
            "channel_counts": {"keyword": 0, "vector": 0, "graph": 0},
            "channels": {"keyword": 0, "vector": 0, "graph": 0},
            "warnings": [],
            "trace_summary": {"evidence_insufficient": True},
        }
    result = execute_hybrid_search(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        query=payload.query,
        space_ids=allowed,
        top_k=payload.top_k,
        use_keyword=payload.use_keyword,
        use_vector=payload.use_vector,
        use_graph=payload.use_graph,
        use_reranker=payload.use_reranker,
        filters=payload.filters,
    )
    db.commit()
    return result
