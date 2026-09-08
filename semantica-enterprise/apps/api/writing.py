from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import uuid
from typing import Any

import semantica

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import get_current_user, has_space_permission, require_permission
from apps.api.utils import apply_patch, attachment_content_disposition, serialize_row
from apps.api.writing_schemas import (
    AlternativePlanGenerate,
    AlternativePlanSelect,
    ComputationRequest,
    DecisionRecordCreate,
    FactConfirmation,
    ProjectFactCreate,
    ScenarioPackageCreate,
    ScenarioPackageUpdate,
    ScenarioPackageVersionCreate,
    WritingBlockBindingUpsert,
    WritingDocumentCreate,
    WritingDocumentUpdate,
    WritingDocumentValidate,
    WritingDocumentVersionCreate,
    WritingCommentCreate,
    WritingCommentResolve,
    WritingCommentUpdate,
    WritingMemberCreate,
    WritingKnowledgeSearch,
    WritingAgentMessageCreate,
    WritingAgentSessionCreate,
    WritingProjectCreate,
    WritingProjectReleaseRebase,
    WritingProjectUpdate,
    WritingRecomputeRequest,
    WritingReasoningRequest,
    WritingExportCreate,
)
from packages.platform.audit import audit
from packages.platform.curation import effective_chunk_text
from packages.platform.database import get_db
from packages.platform.knowledge_search import execute_hybrid_search
from packages.platform.models import (
    AgentEventProjection,
    AlternativePlan,
    Application,
    ComputationDefinition,
    ComputationDefinitionVersion,
    ComputationRun,
    Conversation,
    ConversationMessage,
    Chunk,
    DecisionGate,
    DecisionRecord,
    KnowledgeProductRelease,
    KnowledgeProductReleaseItem,
    Document,
    DocumentVersion,
    ExportJob,
    ExportTemplateVersion,
    QueryRun,
    ProjectFact,
    ScenarioPackage,
    ScenarioPackageVersion,
    User,
    WritingBlockBinding,
    WritingDocument,
    WritingDocumentVersion,
    WritingComment,
    WritingProject,
    WritingProjectMember,
    WritingAgentSession,
    WritingEventProjection,
    WritingReasoningRun,
)
from packages.platform.writing import (
    BUILTIN_FORMULAS,
    affected_dependency_ids,
    content_hash,
    execute_formula,
    evaluate_earthquake_criteria,
    earthquake_reasoning_payload,
    generate_alternative_plans,
    validate_plate_content,
    validate_scenario_contract,
    walk_plate_nodes,
)
from packages.platform.writing_export import CONTENT_TYPES, build_export_artifact
from packages.platform.storage import object_storage
from packages.platform.config import get_settings
from packages.platform.security import create_collaboration_access_token
from packages.semantica_adapter.analyze import run_graph_inference
from apps.api.conversations import (
    _cancel_runtime,
    _conversation_payload,
    _create_retry_assistant,
    _create_turn_messages,
    _project_event,
    _stream_turn,
    _turn_retrieval_settings,
)


router = APIRouter(prefix="/writing", tags=["miaobi-writing"])
require_writing_admin = require_permission("writing.manage")
settings = get_settings()

ROLE_RANK = {"viewer": 1, "commenter": 2, "editor": 3, "reviewer": 4, "publisher": 5, "owner": 6}
def _active(model: type) -> Any:
    return model.deleted_at.is_(None)


def _tenant_row(db: Session, model: type, row_id: str, tenant_id: str, label: str):
    row = db.get(model, row_id)
    if row is None or row.deleted_at is not None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    return row


def _commit(db: Session, message: str = "记录已存在") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=message) from exc


def _project_role(db: Session, project: WritingProject, user: User) -> str | None:
    if user.is_admin or project.owner_id == user.id:
        return "owner"
    member = db.scalar(
        select(WritingProjectMember).where(
            WritingProjectMember.project_id == project.id,
            WritingProjectMember.user_id == user.id,
            _active(WritingProjectMember),
        )
    )
    return member.role if member else None


def _project(db: Session, project_id: str, user: User, minimum_role: str = "viewer") -> WritingProject:
    project = _tenant_row(db, WritingProject, project_id, user.tenant_id, "方案任务")
    role = _project_role(db, project, user)
    if role is None or ROLE_RANK.get(role, 0) < ROLE_RANK[minimum_role]:
        raise HTTPException(status_code=403, detail="无权访问该方案任务")
    return project


def _document(db: Session, document_id: str, user: User, minimum_role: str = "viewer") -> tuple[WritingDocument, WritingProject]:
    document = _tenant_row(db, WritingDocument, document_id, user.tenant_id, "文稿")
    return document, _project(db, document.project_id, user, minimum_role)


def _writing_session(
    db: Session,
    session_id: str,
    user: User,
    minimum_role: str = "viewer",
) -> tuple[WritingAgentSession, WritingProject, Conversation]:
    session = _tenant_row(db, WritingAgentSession, session_id, user.tenant_id, "妙笔助手会话")
    if session.user_id != user.id or session.status != "active":
        raise HTTPException(status_code=404, detail="妙笔助手会话不存在")
    project = _project(db, session.project_id, user, minimum_role)
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.harness_session_id == session.harness_session_id,
            Conversation.status != "deleted",
        )
    )
    if conversation is None or conversation.user_id != user.id or conversation.tenant_id != user.tenant_id:
        raise HTTPException(status_code=409, detail="妙笔助手会话映射不可用")
    return session, project, conversation


def _release_for_user(db: Session, release_id: str, user: User) -> KnowledgeProductRelease:
    release = _tenant_row(db, KnowledgeProductRelease, release_id, user.tenant_id, "知识产品版本")
    if release.status != "published":
        raise HTTPException(status_code=409, detail="方案任务只能绑定已发布的知识产品版本")
    items = list(
        db.scalars(
            select(KnowledgeProductReleaseItem).where(
                KnowledgeProductReleaseItem.product_release_id == release.id,
                _active(KnowledgeProductReleaseItem),
            )
        )
    )
    if not items:
        raise HTTPException(status_code=409, detail="知识产品版本没有可用知识空间")
    if any(not has_space_permission(db, user, item.space_id, "read") for item in items):
        raise HTTPException(status_code=403, detail="无权读取知识产品版本包含的全部知识空间")
    return release


def _release_scope(
    db: Session,
    project: WritingProject,
    user: User,
) -> tuple[list[str], dict[str, str]]:
    release = _release_for_user(db, project.knowledge_product_release_id, user)
    items = list(
        db.scalars(
            select(KnowledgeProductReleaseItem).where(
                KnowledgeProductReleaseItem.product_release_id == release.id,
                _active(KnowledgeProductReleaseItem),
            )
        )
    )
    return [item.space_id for item in items], {
        item.space_id: item.knowledge_release_id for item in items
    }


def _scenario_contract(version: ScenarioPackageVersion) -> dict[str, Any]:
    return {
        "input_schema": version.input_schema,
        "ontology_mapping": version.ontology_mapping,
        "rule_set_ids": version.rule_set_ids,
        "formula_ids": version.formula_ids,
        "tool_ids": version.tool_ids,
        "chapter_template": version.chapter_template,
        "output_schema": version.output_schema,
        "review_rules": version.review_rules,
        "decision_gates": version.decision_gates,
        "comparison_dimensions": version.comparison_dimensions,
        "config": version.config,
    }


def _ensure_formula_version(db: Session, user: User, operation: str) -> ComputationDefinitionVersion:
    definition = db.scalar(
        select(ComputationDefinition).where(
            ComputationDefinition.tenant_id == user.tenant_id,
            ComputationDefinition.code == operation,
            _active(ComputationDefinition),
        )
    )
    if definition is None:
        spec = BUILTIN_FORMULAS[operation]
        definition = ComputationDefinition(
            tenant_id=user.tenant_id,
            code=operation,
            name=spec["name"],
            description="妙笔内置确定性公式；只执行受控操作，不解释任意表达式。",
            enabled=True,
        )
        db.add(definition)
        db.flush()
    version = db.scalar(
        select(ComputationDefinitionVersion).where(
            ComputationDefinitionVersion.definition_id == definition.id,
            ComputationDefinitionVersion.status == "active",
            _active(ComputationDefinitionVersion),
        ).order_by(ComputationDefinitionVersion.version.desc())
    )
    if version is None:
        spec = BUILTIN_FORMULAS[operation]
        manifest = {
            "operation": operation,
            "expression": spec["expression"],
            "rounding": {"mode": "half_up", "digits": 0},
        }
        version = ComputationDefinitionVersion(
            tenant_id=user.tenant_id,
            definition_id=definition.id,
            version=1,
            operation=operation,
            expression=spec["expression"],
            input_schema={"type": "object"},
            output_schema={"type": "number"},
            unit=spec["unit"],
            rounding=manifest["rounding"],
            default_parameters={},
            tests=[],
            checksum=content_hash(manifest),
            status="active",
            created_by=user.id,
        )
        db.add(version)
        db.flush()
        definition.current_version_id = version.id
    return version


def _fact_number(row: ProjectFact) -> float:
    value = row.value or {}
    number = value.get("number", value.get("value"))
    if number is None or isinstance(number, bool):
        raise HTTPException(status_code=409, detail=f"事实“{row.label}”不是可计算数值")
    try:
        return float(number)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"事实“{row.label}”不是可计算数值") from exc


def _execute_computation_run(
    db: Session,
    *,
    project: WritingProject,
    user: User,
    definition_version: ComputationDefinitionVersion,
    inputs: dict[str, Any],
    parameters: dict[str, Any],
    rounding: dict[str, Any],
    input_facts: dict[str, ProjectFact],
    output_fact: dict[str, Any] | None = None,
) -> tuple[ComputationRun, ProjectFact | None]:
    try:
        calculated = execute_formula(
            definition_version.operation,
            inputs,
            parameters={**(definition_version.default_parameters or {}), **parameters},
            rounding=rounding or definition_version.rounding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dependencies = {name: row.fact_key for name, row in input_facts.items()}
    result = {
        **calculated,
        "dependencies": dependencies,
        "output_fact": output_fact or {},
    }
    now = datetime.now(timezone.utc)
    run = ComputationRun(
        tenant_id=user.tenant_id,
        project_id=project.id,
        definition_version_id=definition_version.id,
        status="succeeded",
        inputs=inputs,
        result=result,
        input_fact_ids=[row.id for row in input_facts.values()],
        checksum=content_hash(
            {
                "definition": definition_version.checksum,
                "inputs": inputs,
                "parameters": parameters,
                "dependencies": dependencies,
            }
        ),
        started_at=now,
        finished_at=now,
        created_by=user.id,
    )
    db.add(run)
    db.flush()
    generated = None
    if output_fact:
        generated = _upsert_generated_fact(
            db,
            project=project,
            user=user,
            fact_key=str(output_fact["fact_key"]),
            label=str(output_fact["label"]),
            fact_type="deterministic_computation",
            value={"number": calculated["value"]},
            unit=output_fact.get("unit") or definition_version.unit,
            source_type="computation",
            source_id=run.id,
            source_locator={
                "formula_version_id": definition_version.id,
                "expression": definition_version.expression,
                "input_fact_ids": run.input_fact_ids,
                "dependencies": dependencies,
            },
            verification_status="verified",
        )
    return run, generated


def _upsert_generated_fact(
    db: Session,
    *,
    project: WritingProject,
    user: User,
    fact_key: str,
    label: str,
    fact_type: str,
    value: dict[str, Any],
    source_type: str,
    source_id: str,
    source_locator: dict[str, Any],
    verification_status: str,
    unit: str | None = None,
) -> ProjectFact:
    current = db.scalar(
        select(ProjectFact).where(
            ProjectFact.project_id == project.id,
            ProjectFact.fact_key == fact_key,
            ProjectFact.active.is_(True),
            _active(ProjectFact),
        ).order_by(ProjectFact.version.desc())
    )
    if current is not None and current.value == value and current.source_locator == source_locator:
        return current
    version = current.version + 1 if current else 1
    if current is not None:
        current.active = False
        current.freshness_status = "superseded"
        db.query(WritingBlockBinding).filter(
            WritingBlockBinding.fact_id == current.id,
            _active(WritingBlockBinding),
        ).update({"freshness_status": "stale"})
    row = ProjectFact(
        tenant_id=user.tenant_id,
        project_id=project.id,
        fact_key=fact_key,
        label=label,
        fact_type=fact_type,
        value=value,
        unit=unit,
        source_type=source_type,
        source_id=source_id,
        source_locator=source_locator,
        confidence=1.0,
        verification_status=verification_status,
        freshness_status="current",
        version=version,
        active=True,
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    return row


# Scenario packages are administrator-managed executable contracts.


@router.get("/scenario-packages")
def list_scenario_packages(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ScenarioPackage).where(
            ScenarioPackage.tenant_id == user.tenant_id,
            _active(ScenarioPackage),
        ).order_by(ScenarioPackage.name)
    )
    return [serialize_row(row) for row in rows]


@router.post("/scenario-packages")
def create_scenario_package(
    payload: ScenarioPackageCreate,
    admin: User = Depends(require_writing_admin),
    db: Session = Depends(get_db),
):
    row = ScenarioPackage(tenant_id=admin.tenant_id, status="draft", **payload.model_dump())
    db.add(row)
    audit(db, admin.tenant_id, admin.id, "writing.scenario_package.create", "scenario_package", row.id)
    _commit(db, "场景编码已存在")
    db.refresh(row)
    return serialize_row(row)


@router.get("/scenario-packages/{package_id}")
def get_scenario_package(package_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _tenant_row(db, ScenarioPackage, package_id, user.tenant_id, "场景包")
    data = serialize_row(row)
    data["versions"] = [
        serialize_row(version)
        for version in db.scalars(
            select(ScenarioPackageVersion).where(
                ScenarioPackageVersion.scenario_package_id == row.id,
                _active(ScenarioPackageVersion),
            ).order_by(ScenarioPackageVersion.version.desc())
        )
    ]
    return data


@router.put("/scenario-packages/{package_id}")
def update_scenario_package(
    package_id: str,
    payload: ScenarioPackageUpdate,
    admin: User = Depends(require_writing_admin),
    db: Session = Depends(get_db),
):
    row = _tenant_row(db, ScenarioPackage, package_id, admin.tenant_id, "场景包")
    apply_patch(row, payload.model_dump(exclude_none=True), {"name", "description", "status", "enabled"})
    audit(db, admin.tenant_id, admin.id, "writing.scenario_package.update", "scenario_package", row.id)
    db.commit()
    return serialize_row(row)


@router.delete("/scenario-packages/{package_id}")
def delete_scenario_package(
    package_id: str,
    admin: User = Depends(require_writing_admin),
    db: Session = Depends(get_db),
):
    row = _tenant_row(db, ScenarioPackage, package_id, admin.tenant_id, "场景包")
    referenced = db.scalar(
        select(func.count()).select_from(WritingProject).join(
            ScenarioPackageVersion,
            ScenarioPackageVersion.id == WritingProject.scenario_package_version_id,
        ).where(ScenarioPackageVersion.scenario_package_id == row.id, _active(WritingProject))
    )
    if referenced:
        raise HTTPException(status_code=409, detail="场景包已被方案任务使用，请先停用，不能删除历史依据")
    row.deleted_at = datetime.now(timezone.utc)
    audit(db, admin.tenant_id, admin.id, "writing.scenario_package.delete", "scenario_package", row.id)
    db.commit()
    return {"deleted": True}


@router.post("/scenario-packages/{package_id}/versions")
def create_scenario_package_version(
    package_id: str,
    payload: ScenarioPackageVersionCreate,
    admin: User = Depends(require_writing_admin),
    db: Session = Depends(get_db),
):
    package = _tenant_row(db, ScenarioPackage, package_id, admin.tenant_id, "场景包")
    contract = payload.model_dump(exclude={"activate"})
    try:
        validate_scenario_contract(contract)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    number = int(
        db.scalar(
            select(func.max(ScenarioPackageVersion.version)).where(
                ScenarioPackageVersion.scenario_package_id == package.id
            )
        )
        or 0
    ) + 1
    row = ScenarioPackageVersion(
        tenant_id=admin.tenant_id,
        scenario_package_id=package.id,
        version=number,
        checksum=content_hash(contract),
        status="active" if payload.activate else "draft",
        created_by=admin.id,
        **contract,
    )
    db.add(row)
    db.flush()
    if payload.activate:
        db.query(ScenarioPackageVersion).filter(
            ScenarioPackageVersion.scenario_package_id == package.id,
            ScenarioPackageVersion.id != row.id,
            ScenarioPackageVersion.status == "active",
        ).update({"status": "retired"})
        package.current_version_id = row.id
        package.status = "active"
    audit(
        db,
        admin.tenant_id,
        admin.id,
        "writing.scenario_package.version.create",
        "scenario_package_version",
        row.id,
        {"version": number, "activated": payload.activate, "checksum": row.checksum},
    )
    db.commit()
    return serialize_row(row)


@router.post("/scenario-packages/{package_id}/versions/{version_id}/activate")
def activate_scenario_package_version(
    package_id: str,
    version_id: str,
    admin: User = Depends(require_writing_admin),
    db: Session = Depends(get_db),
):
    package = _tenant_row(db, ScenarioPackage, package_id, admin.tenant_id, "场景包")
    version = _tenant_row(db, ScenarioPackageVersion, version_id, admin.tenant_id, "场景包版本")
    if version.scenario_package_id != package.id:
        raise HTTPException(status_code=404, detail="场景包版本不存在")
    validate_scenario_contract(_scenario_contract(version))
    db.query(ScenarioPackageVersion).filter(
        ScenarioPackageVersion.scenario_package_id == package.id,
        ScenarioPackageVersion.status == "active",
    ).update({"status": "retired"})
    version.status = "active"
    package.current_version_id = version.id
    package.status = "active"
    audit(db, admin.tenant_id, admin.id, "writing.scenario_package.version.activate", "scenario_package_version", version.id)
    db.commit()
    return serialize_row(version)


# Projects, facts, gates and deterministic plans.


@router.get("/projects")
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(WritingProject).where(WritingProject.tenant_id == user.tenant_id, _active(WritingProject))
    if not user.is_admin:
        memberships = select(WritingProjectMember.project_id).where(
            WritingProjectMember.user_id == user.id,
            _active(WritingProjectMember),
        )
        query = query.where(or_(WritingProject.owner_id == user.id, WritingProject.id.in_(memberships)))
    return [serialize_row(row) for row in db.scalars(query.order_by(WritingProject.updated_at.desc()))]


@router.post("/projects")
def create_project(
    payload: WritingProjectCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    version = _tenant_row(
        db, ScenarioPackageVersion, payload.scenario_package_version_id, user.tenant_id, "场景包版本"
    )
    if version.status != "active":
        raise HTTPException(status_code=409, detail="方案任务只能使用已激活的场景包版本")
    _release_for_user(db, payload.knowledge_product_release_id, user)
    if payload.application_id:
        application = _tenant_row(db, Application, payload.application_id, user.tenant_id, "应用")
        if not user.is_admin and application.owner_id != user.id:
            raise HTTPException(status_code=403, detail="无权将任务绑定到该应用")
    row = WritingProject(tenant_id=user.tenant_id, owner_id=user.id, status="draft", **payload.model_dump())
    db.add(row)
    db.flush()
    db.add(
        WritingProjectMember(
            tenant_id=user.tenant_id,
            project_id=row.id,
            user_id=user.id,
            role="owner",
            created_by=user.id,
        )
    )
    for item in version.decision_gates or []:
        db.add(
            DecisionGate(
                tenant_id=user.tenant_id,
                project_id=row.id,
                gate_key=str(item["key"]),
                name=str(item.get("name") or item["key"]),
                required=bool(item.get("required", True)),
                status="pending",
            )
        )
    audit(db, user.tenant_id, user.id, "writing.project.create", "writing_project", row.id)
    _commit(db, "方案任务编码已存在")
    db.refresh(row)
    return serialize_row(row)


@router.get("/projects/{project_id}")
def get_project(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _project(db, project_id, user)
    data = serialize_row(row)
    data["role"] = _project_role(db, row, user)
    data["facts"] = int(
        db.scalar(select(func.count()).select_from(ProjectFact).where(ProjectFact.project_id == row.id, ProjectFact.active.is_(True), _active(ProjectFact))) or 0
    )
    data["pending_gates"] = int(
        db.scalar(select(func.count()).select_from(DecisionGate).where(DecisionGate.project_id == row.id, DecisionGate.required.is_(True), DecisionGate.status != "confirmed", _active(DecisionGate))) or 0
    )
    return data


@router.post("/projects/{project_id}/knowledge/search")
def search_project_knowledge(
    project_id: str,
    payload: WritingKnowledgeSearch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search the immutable Knowledge Product release bound to this project."""
    project = _project(db, project_id, user)
    space_ids, knowledge_release_ids = _release_scope(db, project, user)
    try:
        result = execute_hybrid_search(
            db,
            tenant_id=user.tenant_id,
            user_id=user.id,
            query=payload.query,
            space_ids=space_ids,
            top_k=payload.top_k,
            use_keyword=payload.use_keyword,
            use_vector=payload.use_vector,
            use_graph=payload.use_graph,
            use_reranker=payload.use_reranker,
            filters=payload.filters,
            audit_action="writing.knowledge.search",
            knowledge_release_ids=knowledge_release_ids,
            retrieval_context={
                "writing_project_id": project.id,
                "knowledge_product_release_id": project.knowledge_product_release_id,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    result["knowledge_product_release_id"] = project.knowledge_product_release_id
    result["snapshot_locked"] = True
    return result


@router.get("/projects/{project_id}/knowledge/fragments/{chunk_id}")
def get_project_fragment(
    project_id: str,
    chunk_id: str,
    query_run_id: str = Query(min_length=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read only a fragment attested by this project's release-scoped QueryRun."""
    project = _project(db, project_id, user)
    run = _tenant_row(db, QueryRun, query_run_id, user.tenant_id, "检索记录")
    policy = dict(run.retrieval_policy or {})
    if (
        run.user_id != user.id
        or policy.get("writing_project_id") != project.id
        or policy.get("knowledge_product_release_id") != project.knowledge_product_release_id
    ):
        raise HTTPException(status_code=403, detail="该检索记录不属于当前方案任务")
    item = next(
        (
            row
            for row in (run.results or [])
            if str(row.get("chunk_id") or "") == chunk_id
        ),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="该片段不在本次检索结果中")
    chunk = _tenant_row(db, Chunk, chunk_id, user.tenant_id, "知识片段")
    space_ids, _ = _release_scope(db, project, user)
    if chunk.space_id not in space_ids:
        raise HTTPException(status_code=403, detail="知识片段超出项目知识产品范围")
    document = db.get(Document, chunk.document_id)
    version = db.get(DocumentVersion, chunk.version_id)
    if document is None or version is None or document.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="来源文档不存在")
    try:
        text, curation = effective_chunk_text(db, chunk, include_superseded=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="知识片段已被人工屏蔽") from exc
    return {
        "query_run_id": run.id,
        "knowledge_product_release_id": project.knowledge_product_release_id,
        "chunk_id": chunk.id,
        "document_id": document.id,
        "version_id": version.id,
        "document_title": document.title,
        "text": text,
        "page_number": chunk.page_number,
        "structural_path": chunk.structural_path,
        "source_span": chunk.source_span or {},
        "document_version": version.version_number,
        "filename": version.filename,
        "content_type": version.content_type,
        "historical_snapshot": document.current_version_id != version.id,
        "curation": curation,
        "has_access": True,
    }


# DSH remains the authoritative Agent event log.  A hidden business
# conversation supplies the existing short-lived credential and SSE bridge;
# its events are mirrored into WritingEventProjection by conversations.py.


@router.post("/projects/{project_id}/agent-sessions")
def create_writing_agent_session(
    project_id: str,
    payload: WritingAgentSessionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _project(db, project_id, user, "editor")
    if payload.document_id:
        document, _ = _document(db, payload.document_id, user, "editor")
        if document.project_id != project.id:
            raise HTTPException(status_code=404, detail="文稿不属于当前方案任务")
    existing = db.scalar(
        select(WritingAgentSession)
        .where(
            WritingAgentSession.project_id == project.id,
            WritingAgentSession.document_id == payload.document_id,
            WritingAgentSession.user_id == user.id,
            WritingAgentSession.status == "active",
            _active(WritingAgentSession),
        )
        .order_by(WritingAgentSession.created_at.desc())
    )
    if existing is not None and not payload.start_new:
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.harness_session_id == existing.harness_session_id,
                Conversation.status != "deleted",
            )
        )
        if conversation is not None:
            return {
                **serialize_row(existing),
                "conversation_id": conversation.id,
                "conversation": _conversation_payload(db, conversation, detail=True),
            }
        existing.status = "orphaned"
    elif existing is not None:
        existing.status = "archived"

    space_ids, knowledge_release_ids = _release_scope(db, project, user)
    harness_session_id = f"miaobi-{uuid.uuid4().hex}"
    session = WritingAgentSession(
        tenant_id=user.tenant_id,
        project_id=project.id,
        document_id=payload.document_id,
        user_id=user.id,
        harness_session_id=harness_session_id,
        status="active",
    )
    db.add(session)
    db.flush()
    conversation = Conversation(
        harness_session_id=harness_session_id,
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=f"妙笔 · {project.name}",
        settings={
            "kind": "writing",
            "writing_session_id": session.id,
            "writing_project_id": project.id,
            "writing_document_id": payload.document_id,
            "knowledge_product_release_id": project.knowledge_product_release_id,
            "knowledge_release_ids": knowledge_release_ids,
            "space_ids": space_ids,
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": False,
            "top_k": 8,
        },
    )
    db.add(conversation)
    audit(
        db,
        user.tenant_id,
        user.id,
        "writing.agent_session.create",
        "writing_agent_session",
        session.id,
        {"project_id": project.id, "document_id": payload.document_id},
    )
    db.commit()
    db.refresh(session)
    return {
        **serialize_row(session),
        "conversation_id": conversation.id,
        "conversation": _conversation_payload(db, conversation, detail=True),
    }


@router.get("/projects/{project_id}/agent-sessions")
def list_writing_agent_sessions(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _project(db, project_id, user)
    sessions = list(
        db.scalars(
            select(WritingAgentSession).where(
                WritingAgentSession.project_id == project_id,
                WritingAgentSession.user_id == user.id,
                _active(WritingAgentSession),
            ).order_by(WritingAgentSession.updated_at.desc())
        )
    )
    conversations = {
        row.harness_session_id: row
        for row in db.scalars(
            select(Conversation).where(
                Conversation.harness_session_id.in_([item.harness_session_id for item in sessions])
            )
        )
    } if sessions else {}
    return [
        {
            **serialize_row(item),
            "conversation_id": conversations[item.harness_session_id].id
            if item.harness_session_id in conversations else None,
        }
        for item in sessions
    ]


@router.get("/agent-sessions/{session_id}")
def get_writing_agent_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session, _, conversation = _writing_session(db, session_id, user)
    return {
        **serialize_row(session),
        "conversation_id": conversation.id,
        "conversation": _conversation_payload(db, conversation, detail=True),
    }


@router.post("/agent-sessions/{session_id}/messages")
def send_writing_agent_message(
    session_id: str,
    payload: WritingAgentMessageCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session, project, conversation = _writing_session(db, session_id, user, "editor")
    if conversation.status == "generating":
        raise HTTPException(status_code=409, detail="妙笔助手正在生成")
    content = payload.content.strip()
    _, assistant = _create_turn_messages(db, conversation, user, content)
    agent_content = (
        "[妙笔写作任务] 请使用 writing_get_project_context 获取当前方案任务，"
        "并将权威事实、计算和规则结论与普通叙述严格区分。"
        "执行阶段仅写入 Session Event；最终答复不得输出工具名、检索尝试、自我对话或工作草稿，"
        "应直接给出面向业务人员的修订建议、待确认事项和真实引用。用户请求：\n" + content
    )
    audit(
        db,
        user.tenant_id,
        user.id,
        "writing.agent.turn.start",
        "writing_agent_session",
        session.id,
        {"project_id": project.id, "document_id": session.document_id},
    )
    db.commit()
    return StreamingResponse(
        _stream_turn(
            request,
            conversation.id,
            conversation.harness_session_id,
            assistant.id,
            agent_content,
            _turn_retrieval_settings(conversation),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/agent-sessions/{session_id}/events")
def list_writing_agent_events(
    session_id: str,
    after_sequence: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session, _, _ = _writing_session(db, session_id, user)
    return {
        "items": [
            serialize_row(row)
            for row in db.scalars(
                select(WritingEventProjection).where(
                    WritingEventProjection.session_id == session.id,
                    WritingEventProjection.sequence > after_sequence,
                ).order_by(WritingEventProjection.sequence)
            )
        ]
    }


@router.post("/agent-sessions/{session_id}/cancel")
def cancel_writing_agent(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session, _, conversation = _writing_session(db, session_id, user, "editor")
    assistant = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.role == "assistant",
            ConversationMessage.status == "generating",
            _active(ConversationMessage),
        ).order_by(ConversationMessage.sequence.desc()).limit(1)
    )
    if assistant is not None:
        _project_event(db, conversation.id, assistant.id, "turn_cancelled", {"reason": "user_cancelled"})
    conversation.status = "active"
    audit(db, user.tenant_id, user.id, "writing.agent.cancel", "writing_agent_session", session.id)
    db.commit()
    _cancel_runtime(conversation.harness_session_id)
    return {"ok": True}


@router.post("/agent-sessions/{session_id}/messages/{message_id}/retry")
def retry_writing_agent_message(
    session_id: str,
    message_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, _, conversation = _writing_session(db, session_id, user, "editor")
    failed = db.get(ConversationMessage, message_id)
    if (
        failed is None or failed.conversation_id != conversation.id
        or failed.role != "assistant" or failed.status not in {"failed", "cancelled"}
    ):
        raise HTTPException(status_code=409, detail="只能重试失败或已取消的生成")
    parent = db.get(ConversationMessage, failed.parent_message_id) if failed.parent_message_id else None
    if parent is None:
        raise HTTPException(status_code=409, detail="找不到原始写作请求")
    assistant = _create_retry_assistant(db, conversation, user, parent, failed)
    return StreamingResponse(
        _stream_turn(
            request,
            conversation.id,
            conversation.harness_session_id,
            assistant.id,
            "[妙笔写作任务] " + parent.content,
            _turn_retrieval_settings(conversation),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.put("/projects/{project_id}")
def update_project(
    project_id: str,
    payload: WritingProjectUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _project(db, project_id, user, "editor")
    apply_patch(row, payload.model_dump(exclude_none=True), {"name", "status", "config"})
    audit(db, user.tenant_id, user.id, "writing.project.update", "writing_project", row.id)
    db.commit()
    return serialize_row(row)


@router.post("/projects/{project_id}/knowledge-release")
def rebase_project_knowledge_release(
    project_id: str,
    payload: WritingProjectReleaseRebase,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Explicitly rebase a non-published writing project onto an immutable knowledge release."""
    row = _project(db, project_id, user, "owner")
    if row.status == "published":
        raise HTTPException(status_code=409, detail="已发布方案任务不能变更知识基线，请创建后续任务版本")
    release = _release_for_user(db, payload.knowledge_product_release_id, user)
    old_release_id = row.knowledge_product_release_id
    if old_release_id == release.id:
        return {**serialize_row(row), "unchanged": True}
    row.knowledge_product_release_id = release.id
    stale_bindings = list(
        db.scalars(
            select(WritingBlockBinding).where(
                WritingBlockBinding.project_id == row.id,
                WritingBlockBinding.freshness_status == "current",
                _active(WritingBlockBinding),
            )
        )
    )
    for binding in stale_bindings:
        binding.freshness_status = "stale"
        binding.metadata_json = {
            **(binding.metadata_json or {}),
            "stale_reason": "knowledge_product_release_rebased",
            "previous_knowledge_product_release_id": old_release_id,
        }
    audit(
        db,
        user.tenant_id,
        user.id,
        "writing.project.knowledge_release.rebase",
        "writing_project",
        row.id,
        {
            "previous_release_id": old_release_id,
            "knowledge_product_release_id": release.id,
            "stale_bindings": len(stale_bindings),
            "reason": payload.reason,
        },
    )
    db.commit()
    return {**serialize_row(row), "unchanged": False, "stale_bindings": len(stale_bindings)}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _project(db, project_id, user, "owner")
    if row.status == "published":
        raise HTTPException(status_code=409, detail="已发布任务只能归档，不能删除")
    row.deleted_at = datetime.now(timezone.utc)
    audit(db, user.tenant_id, user.id, "writing.project.delete", "writing_project", row.id)
    db.commit()
    return {"deleted": True}


@router.get("/projects/{project_id}/members")
def list_members(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _project(db, project_id, user)
    return [serialize_row(row) for row in db.scalars(select(WritingProjectMember).where(WritingProjectMember.project_id == project_id, _active(WritingProjectMember)).order_by(WritingProjectMember.created_at))]


@router.post("/projects/{project_id}/members")
def add_member(
    project_id: str,
    payload: WritingMemberCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _project(db, project_id, user, "owner")
    member_user = _tenant_row(db, User, payload.user_id, user.tenant_id, "用户")
    if not member_user.enabled:
        raise HTTPException(status_code=409, detail="用户已停用")
    if payload.role == "owner" and member_user.id != project.owner_id:
        raise HTTPException(status_code=422, detail="项目只能有一个负责人")
    existing = db.scalar(select(WritingProjectMember).where(WritingProjectMember.project_id == project.id, WritingProjectMember.user_id == member_user.id))
    if existing:
        existing.deleted_at = None
        existing.role = payload.role
        row = existing
    else:
        row = WritingProjectMember(tenant_id=user.tenant_id, project_id=project.id, user_id=member_user.id, role=payload.role, created_by=user.id)
        db.add(row)
    audit(db, user.tenant_id, user.id, "writing.project.member.upsert", "writing_project", project.id, {"member_id": member_user.id, "role": payload.role})
    _commit(db)
    return serialize_row(row)


@router.get("/projects/{project_id}/facts")
def list_facts(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _project(db, project_id, user)
    return [serialize_row(row) for row in db.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id, ProjectFact.active.is_(True), _active(ProjectFact)).order_by(ProjectFact.fact_key))]


@router.post("/projects/{project_id}/facts")
def create_fact(
    project_id: str,
    payload: ProjectFactCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _project(db, project_id, user, "editor")
    current = db.scalar(select(ProjectFact).where(ProjectFact.project_id == project.id, ProjectFact.fact_key == payload.fact_key, ProjectFact.active.is_(True), _active(ProjectFact)).order_by(ProjectFact.version.desc()))
    version = (current.version + 1) if current else 1
    if current:
        current.active = False
        current.freshness_status = "superseded"
        db.query(WritingBlockBinding).filter(WritingBlockBinding.fact_id == current.id, _active(WritingBlockBinding)).update({"freshness_status": "stale"})
        runs = [
            serialize_row(item)
            for item in db.scalars(
                select(ComputationRun).where(
                    ComputationRun.project_id == project.id,
                    _active(ComputationRun),
                )
            )
        ]
        bindings = [
            serialize_row(item)
            for item in db.scalars(
                select(WritingBlockBinding).where(
                    WritingBlockBinding.project_id == project.id,
                    _active(WritingBlockBinding),
                )
            )
        ]
        impact = affected_dependency_ids({current.id}, runs, bindings)
        if impact["block_ids"]:
            db.query(WritingBlockBinding).filter(
                WritingBlockBinding.project_id == project.id,
                WritingBlockBinding.block_id.in_(impact["block_ids"]),
                _active(WritingBlockBinding),
            ).update({"freshness_status": "stale"}, synchronize_session=False)
    row = ProjectFact(tenant_id=user.tenant_id, project_id=project.id, version=version, active=True, freshness_status="current", created_by=user.id, **payload.model_dump())
    db.add(row)
    audit(db, user.tenant_id, user.id, "writing.fact.create", "writing_project_fact", row.id, {"fact_key": row.fact_key, "version": version, "supersedes": current.id if current else None})
    _commit(db)
    db.refresh(row)
    return serialize_row(row)


@router.post("/projects/{project_id}/facts/{fact_id}/confirm")
def confirm_fact(
    project_id: str,
    fact_id: str,
    payload: FactConfirmation,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _project(db, project_id, user, "reviewer")
    row = _tenant_row(db, ProjectFact, fact_id, user.tenant_id, "事实")
    if row.project_id != project_id or not row.active:
        raise HTTPException(status_code=404, detail="事实不存在")
    if payload.decision == "override":
        previous = row
        previous.active = False
        previous.freshness_status = "superseded"
        db.query(WritingBlockBinding).filter(
            WritingBlockBinding.fact_id == previous.id,
            _active(WritingBlockBinding),
        ).update({"freshness_status": "stale"})
        dependent_run_ids = [
            item.id
            for item in db.scalars(
                select(ComputationRun).where(
                    ComputationRun.project_id == project_id,
                    _active(ComputationRun),
                )
            )
            if previous.id in (item.input_fact_ids or [])
        ]
        if dependent_run_ids:
            db.query(WritingBlockBinding).filter(
                WritingBlockBinding.project_id == project_id,
                WritingBlockBinding.computation_run_id.in_(dependent_run_ids),
                _active(WritingBlockBinding),
            ).update({"freshness_status": "stale"}, synchronize_session=False)
        row = ProjectFact(
            tenant_id=previous.tenant_id,
            project_id=previous.project_id,
            fact_key=previous.fact_key,
            label=previous.label,
            fact_type="manual_override",
            value=payload.new_value,
            unit=previous.unit,
            source_type="manual_override",
            source_id=previous.id,
            source_version=str(previous.version),
            source_locator={"supersedes_fact_id": previous.id, "reason": payload.reason},
            confidence=1.0,
            verification_status="verified",
            freshness_status="manual_override",
            version=previous.version + 1,
            active=True,
            created_by=user.id,
            confirmed_by=user.id,
            confirmed_at=datetime.now(timezone.utc),
        )
        db.add(row)
    else:
        row.verification_status = "verified" if payload.decision == "confirm" else "rejected"
        row.confirmed_by = user.id
        row.confirmed_at = datetime.now(timezone.utc)
    audit(db, user.tenant_id, user.id, f"writing.fact.{payload.decision}", "writing_project_fact", row.id, {"reason": payload.reason, "version": row.version})
    db.commit()
    db.refresh(row)
    return serialize_row(row)


@router.post("/projects/{project_id}/criteria/evaluate")
def evaluate_project_criteria(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _project(db, project_id, user, "editor")
    scenario = _tenant_row(
        db,
        ScenarioPackageVersion,
        project.scenario_package_version_id,
        user.tenant_id,
        "场景包版本",
    )
    package = _tenant_row(db, ScenarioPackage, scenario.scenario_package_id, user.tenant_id, "场景包")
    if package.disaster_type != "earthquake":
        raise HTTPException(status_code=409, detail="当前仅地震场景具备已验收的确定性等级判据")
    rows = list(
        db.scalars(
            select(ProjectFact).where(
                ProjectFact.project_id == project.id,
                ProjectFact.active.is_(True),
                ProjectFact.verification_status == "verified",
                _active(ProjectFact),
            )
        )
    )
    values = {row.fact_key: row.value for row in rows}
    try:
        criteria = evaluate_earthquake_criteria(values)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"无法执行判据：{exc}") from exc
    created = [
        _upsert_generated_fact(
            db,
            project=project,
            user=user,
            fact_key=item["fact_key"],
            label=item["label"],
            fact_type="deterministic_computation",
            value=item["value"],
            unit=item["unit"],
            source_type="computation",
            source_id=f"criteria:{package.code}:v{scenario.version}",
            source_locator={"formula": item["formula"], "input_fact_keys": item["inputs"]},
            verification_status="verified",
        )
        for item in criteria
    ]
    audit(db, user.tenant_id, user.id, "writing.criteria.evaluate", "writing_project", project.id, {"scenario": package.code, "criteria": len(created)})
    db.commit()
    return {"items": [serialize_row(row) for row in created], "engine": "deterministic-criteria", "scenario_version": scenario.version}


@router.post("/projects/{project_id}/reason")
def reason_project(
    project_id: str,
    payload: WritingReasoningRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _project(db, project_id, user, "editor")
    scenario = _tenant_row(db, ScenarioPackageVersion, project.scenario_package_version_id, user.tenant_id, "场景包版本")
    package = _tenant_row(db, ScenarioPackage, scenario.scenario_package_id, user.tenant_id, "场景包")
    if package.disaster_type != "earthquake":
        raise HTTPException(status_code=409, detail="该灾种规则包尚待业务确认，不能生成正式推演结论")
    current = list(db.scalars(select(ProjectFact).where(ProjectFact.project_id == project.id, ProjectFact.active.is_(True), _active(ProjectFact))))
    by_key = {row.fact_key: row for row in current}
    required = {"criterion_major_magnitude", "criterion_high_population_density"}
    if any(key not in by_key or by_key[key].verification_status != "verified" for key in required):
        raise HTTPException(status_code=409, detail="请先完成地震等级确定性判据计算")
    event_fact = by_key.get("event_name")
    event_name = str(((event_fact.value if event_fact else {}) or {}).get("text") or project.name)
    criteria = [serialize_row(by_key[key]) for key in sorted(required)]
    facts, rules = earthquake_reasoning_payload(project_id=project.id, event_name=event_name, criteria=criteria)
    started = datetime.now(timezone.utc)
    checksum = content_hash({"facts": facts, "rules": rules, "scenario": scenario.checksum})
    run = WritingReasoningRun(
        tenant_id=user.tenant_id,
        project_id=project.id,
        status="running",
        mode=payload.mode,
        engine="semantica-datalog",
        engine_version=getattr(semantica, "__version__", "unknown"),
        input_fact_ids=[by_key[key].id for key in sorted(required)],
        rule_manifest=rules,
        result={},
        proof={},
        checksum=checksum,
        started_at=started,
        created_by=user.id,
    )
    db.add(run)
    db.flush()
    try:
        inference = run_graph_inference(facts=facts, rules=rules, max_results=20)
    except Exception as exc:
        run.status = "failed"
        run.result = {"error_type": type(exc).__name__}
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=422, detail=f"规则推演失败：{type(exc).__name__}") from exc
    run.status = "succeeded"
    run.result = {"items": inference["items"], "metrics": inference["metrics"]}
    run.proof = {"engine": "Semantica DatalogReasoner", "items": [item.get("proof") or {} for item in inference["items"]]}
    run.finished_at = datetime.now(timezone.utc)
    conclusions = []
    for item in inference["items"]:
        row = _upsert_generated_fact(
            db,
            project=project,
            user=user,
            fact_key="disaster_grade",
            label="灾害等级",
            fact_type="semantica_inference",
            value={"text": item.get("object_value")},
            source_type="semantica_inference",
            source_id=run.id,
            source_locator={"rule_id": item.get("rule_id"), "proof": item.get("proof"), "evidence": item.get("evidence")},
            verification_status="unverified" if payload.mode == "preview" else "verified",
        )
        conclusions.append(row)
    audit(db, user.tenant_id, user.id, "writing.reason.execute", "writing_reasoning_run", run.id, {"mode": payload.mode, "results": len(conclusions), "engine_version": run.engine_version})
    db.commit()
    return {"run": serialize_row(run), "conclusions": [serialize_row(row) for row in conclusions], "requires_human_confirmation": payload.mode == "preview"}


@router.get("/projects/{project_id}/reasoning-runs")
def list_project_reasoning_runs(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _project(db, project_id, user)
    return [serialize_row(row) for row in db.scalars(select(WritingReasoningRun).where(WritingReasoningRun.project_id == project_id, _active(WritingReasoningRun)).order_by(WritingReasoningRun.created_at.desc()))]


@router.post("/projects/{project_id}/compute")
def compute(
    project_id: str,
    payload: ComputationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _project(db, project_id, user, "editor")
    if payload.definition_version_id:
        definition_version = _tenant_row(db, ComputationDefinitionVersion, payload.definition_version_id, user.tenant_id, "公式版本")
        if definition_version.status != "active":
            raise HTTPException(status_code=409, detail="公式版本未激活")
    else:
        definition_version = _ensure_formula_version(db, user, str(payload.operation))
    fact_ids = set(payload.input_fact_ids)
    facts = list(
        db.scalars(
            select(ProjectFact).where(
                ProjectFact.id.in_(fact_ids),
                ProjectFact.project_id == project.id,
                ProjectFact.active.is_(True),
                _active(ProjectFact),
            )
        )
    ) if fact_ids else []
    if len(facts) != len(fact_ids):
        raise HTTPException(status_code=422, detail="计算输入事实不存在或不属于当前方案任务")
    by_id = {row.id: row for row in facts}
    input_facts = {name: by_id[fact_id] for name, fact_id in payload.input_fact_map.items()}
    for name, row in input_facts.items():
        if row.verification_status != "verified":
            raise HTTPException(status_code=409, detail=f"事实“{row.label}”尚未核验，不能形成正式测算")
        if name not in payload.inputs or float(payload.inputs[name]) != _fact_number(row):
            raise HTTPException(status_code=409, detail=f"计算输入 {name} 与已核验事实不一致")
    if payload.output_fact_key and not input_facts:
        raise HTTPException(status_code=409, detail="生成权威测算事实必须绑定已核验输入事实")
    output_fact = (
        {"fact_key": payload.output_fact_key, "label": payload.output_label, "unit": payload.output_unit}
        if payload.output_fact_key else None
    )
    run, generated = _execute_computation_run(
        db,
        project=project,
        user=user,
        definition_version=definition_version,
        inputs=payload.inputs,
        parameters=payload.parameters,
        rounding=payload.rounding,
        input_facts=input_facts,
        output_fact=output_fact,
    )
    audit(db, user.tenant_id, user.id, "writing.computation.execute", "computation_run", run.id, {"operation": definition_version.operation, "checksum": run.checksum})
    db.commit()
    db.refresh(run)
    return {**serialize_row(run), "generated_fact": serialize_row(generated) if generated else None}


@router.post("/projects/{project_id}/computations/run-baseline")
def run_project_baseline_computations(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run the accepted earthquake resource formulas from verified project facts."""
    project = _project(db, project_id, user, "editor")
    scenario = _tenant_row(db, ScenarioPackageVersion, project.scenario_package_version_id, user.tenant_id, "场景包版本")
    package = _tenant_row(db, ScenarioPackage, scenario.scenario_package_id, user.tenant_id, "场景包")
    if package.disaster_type != "earthquake":
        raise HTTPException(status_code=409, detail="该灾种公式包尚待业务确认，不能生成正式测算")
    rows = list(
        db.scalars(
            select(ProjectFact).where(
                ProjectFact.project_id == project.id,
                ProjectFact.active.is_(True),
                ProjectFact.verification_status == "verified",
                _active(ProjectFact),
            )
        )
    )
    by_key = {row.fact_key: row for row in rows}
    specifications = [
        ("rescue_gap", "搜救人员缺口", "人", "rescue_required", "rescue_available"),
        ("county_bed_gap", "县域创伤床位缺口", "张", "trauma_beds_required", "county_trauma_beds"),
        ("all_area_bed_gap", "全域创伤床位缺口", "张", "trauma_beds_required", "callable_trauma_beds"),
        ("tents_gap", "帐篷缺口", "顶", "tents_required", "tents_available"),
    ]
    missing = sorted(
        {key for _, _, _, required_key, available_key in specifications for key in (required_key, available_key)}
        - set(by_key)
    )
    if missing:
        raise HTTPException(status_code=409, detail=f"缺少已核验计算事实：{', '.join(missing)}")
    definition = _ensure_formula_version(db, user, "resource_gap")
    output = []
    for fact_key, label, unit, required_key, available_key in specifications:
        input_facts = {"required": by_key[required_key], "available": by_key[available_key]}
        run, fact = _execute_computation_run(
            db,
            project=project,
            user=user,
            definition_version=definition,
            inputs={"required": _fact_number(input_facts["required"]), "available": _fact_number(input_facts["available"])},
            parameters={},
            rounding={"mode": "half_up", "digits": 0},
            input_facts=input_facts,
            output_fact={"fact_key": fact_key, "label": label, "unit": unit},
        )
        output.append({"run": serialize_row(run), "fact": serialize_row(fact)})
    audit(db, user.tenant_id, user.id, "writing.computations.baseline", "writing_project", project.id, {"count": len(output), "scenario_version": scenario.version})
    db.commit()
    return {"items": output, "scenario_version": scenario.version, "engine": "deterministic-formula-library"}


@router.get("/projects/{project_id}/computations")
def list_computations(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _project(db, project_id, user)
    return [serialize_row(row) for row in db.scalars(select(ComputationRun).where(ComputationRun.project_id == project_id, _active(ComputationRun)).order_by(ComputationRun.created_at.desc()))]


@router.post("/projects/{project_id}/plans/generate")
def generate_plans(
    project_id: str,
    payload: AlternativePlanGenerate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _project(db, project_id, user, "editor")
    configured = dict((project.config or {}).get("plan_inputs") or {})
    inputs = {**configured, **payload.inputs}
    if not inputs.get("resources"):
        facts = {
            row.fact_key: row
            for row in db.scalars(
                select(ProjectFact).where(
                    ProjectFact.project_id == project.id,
                    ProjectFact.active.is_(True),
                    ProjectFact.verification_status == "verified",
                    _active(ProjectFact),
                )
            )
        }
        resources = []
        for label, unit, required_key, available_key in (
            ("搜救人员", "人", "rescue_required", "rescue_available"),
            ("创伤床位", "张", "trauma_beds_required", "callable_trauma_beds"),
            ("帐篷", "顶", "tents_required", "tents_available"),
        ):
            if required_key in facts and available_key in facts:
                resources.append({"name": label, "unit": unit, "required": _fact_number(facts[required_key]), "available": _fact_number(facts[available_key])})
        if resources:
            inputs["resources"] = resources
    try:
        generated = generate_alternative_plans(inputs, payload.count)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = []
    for result in generated:
        version = int(db.scalar(select(func.max(AlternativePlan.version)).where(AlternativePlan.project_id == project.id, AlternativePlan.plan_key == result["plan_key"])) or 0) + 1
        row = AlternativePlan(
            tenant_id=user.tenant_id,
            project_id=project.id,
            plan_key=result["plan_key"],
            name=result["name"],
            version=version,
            objective=result["objective"],
            weights=result["weights"],
            inputs=inputs,
            constraints=inputs.get("constraints") or [],
            result={"route": result["route"], "resource_allocation": result["resource_allocation"], "input_fingerprint": result["input_fingerprint"]},
            algorithm=result["algorithm"],
            unresolved_gaps=result["unresolved_gaps"],
            risks=inputs.get("risks") or [],
            status="candidate",
        )
        db.add(row)
        rows.append(row)
    audit(db, user.tenant_id, user.id, "writing.plans.generate", "writing_project", project.id, {"count": len(rows), "input_fingerprint": content_hash(inputs)})
    db.commit()
    return [serialize_row(row) for row in rows]


@router.get("/projects/{project_id}/plans")
def list_plans(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _project(db, project_id, user)
    return [serialize_row(row) for row in db.scalars(select(AlternativePlan).where(AlternativePlan.project_id == project_id, _active(AlternativePlan)).order_by(AlternativePlan.version.desc(), AlternativePlan.plan_key))]


@router.post("/projects/{project_id}/plans/{plan_id}/select")
def select_plan(
    project_id: str,
    plan_id: str,
    payload: AlternativePlanSelect,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _project(db, project_id, user, "reviewer")
    row = _tenant_row(db, AlternativePlan, plan_id, user.tenant_id, "备选方案")
    if row.project_id != project_id:
        raise HTTPException(status_code=404, detail="备选方案不存在")
    db.query(AlternativePlan).filter(AlternativePlan.project_id == project_id, AlternativePlan.status == "selected").update({"status": "candidate", "selected_by": None, "selected_at": None})
    row.status = "selected"
    row.selected_by = user.id
    row.selected_at = datetime.now(timezone.utc)
    audit(db, user.tenant_id, user.id, "writing.plan.select", "alternative_plan", row.id, {"reason": payload.reason})
    db.commit()
    return serialize_row(row)


@router.get("/projects/{project_id}/decision-gates")
def list_decision_gates(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _project(db, project_id, user)
    return [serialize_row(row) for row in db.scalars(select(DecisionGate).where(DecisionGate.project_id == project_id, _active(DecisionGate)).order_by(DecisionGate.created_at))]


@router.post("/projects/{project_id}/decision-gates/{gate_id}/records")
def decide_gate(
    project_id: str,
    gate_id: str,
    payload: DecisionRecordCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _project(db, project_id, user, "reviewer")
    gate = _tenant_row(db, DecisionGate, gate_id, user.tenant_id, "确认节点")
    if gate.project_id != project_id:
        raise HTTPException(status_code=404, detail="确认节点不存在")
    record = DecisionRecord(tenant_id=user.tenant_id, project_id=project_id, gate_id=gate.id, decided_by=user.id, **payload.model_dump())
    db.add(record)
    db.flush()
    gate.current_record_id = record.id
    gate.status = "confirmed" if payload.decision in {"confirm", "override"} else "rejected"
    db.flush()
    project = db.get(WritingProject, project_id)
    pending_required = int(
        db.scalar(
            select(func.count()).select_from(DecisionGate).where(
                DecisionGate.project_id == project_id,
                DecisionGate.required.is_(True),
                DecisionGate.status != "confirmed",
                _active(DecisionGate),
            )
        )
        or 0
    )
    if project is not None and pending_required == 0 and project.status not in {"published", "archived"}:
        project.status = "ready"
    audit(db, user.tenant_id, user.id, "writing.decision.record", "writing_decision_gate", gate.id, {"decision": payload.decision, "reason": payload.reason})
    db.commit()
    return serialize_row(record)


# Plate documents, immutable versions and evidence bindings.


@router.post("/documents")
def create_document(
    payload: WritingDocumentCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _project(db, payload.project_id, user, "editor")
    row = WritingDocument(tenant_id=user.tenant_id, project_id=project.id, title=payload.title, document_type=payload.document_type, status="draft", created_by=user.id)
    db.add(row)
    db.flush()
    version = WritingDocumentVersion(
        tenant_id=user.tenant_id,
        document_id=row.id,
        version=1,
        content=payload.content,
        content_hash=content_hash(payload.content),
        scenario_package_version_id=project.scenario_package_version_id,
        knowledge_product_release_id=project.knowledge_product_release_id,
        status="draft",
        change_summary="创建文稿",
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    row.current_version_id = version.id
    audit(db, user.tenant_id, user.id, "writing.document.create", "writing_document", row.id)
    _commit(db, "同一方案任务中已存在同名文稿")
    db.refresh(row)
    return {**serialize_row(row), "current_version": serialize_row(version)}


@router.get("/projects/{project_id}/documents")
def list_project_documents(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _project(db, project_id, user)
    return [
        serialize_row(row)
        for row in db.scalars(
            select(WritingDocument).where(
                WritingDocument.project_id == project_id,
                _active(WritingDocument),
            ).order_by(WritingDocument.updated_at.desc())
        )
    ]


@router.get("/documents/{document_id}")
def get_document(document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row, project = _document(db, document_id, user)
    current = db.get(WritingDocumentVersion, row.current_version_id) if row.current_version_id else None
    return {**serialize_row(row), "role": _project_role(db, project, user), "current_version": serialize_row(current) if current else None}


@router.post("/documents/{document_id}/collaboration-token")
def collaboration_token(
    document_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document, project = _document(db, document_id, user, "viewer")
    role = _project_role(db, project, user) or "viewer"
    token, room, expires_at = create_collaboration_access_token(
        document_id=document.id,
        project_id=project.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=role,
    )
    audit(
        db,
        user.tenant_id,
        user.id,
        "writing.collaboration.token.issue",
        "writing_document",
        document.id,
        {"room": room, "role": role, "expires_at": expires_at.isoformat()},
    )
    db.commit()
    public_url = settings.collaboration_public_url.strip()
    if not public_url:
        scheme = "wss" if request.url.scheme == "https" else "ws"
        public_url = f"{scheme}://{request.url.hostname}:8092"
    return {
        "token": token,
        "room": room,
        "url": public_url,
        "expires_at": expires_at,
        "role": role,
        "read_only": ROLE_RANK.get(role, 0) < ROLE_RANK["editor"],
        "user": {"id": user.id, "name": user.display_name},
    }


def _comment_payload(db: Session, row: WritingComment) -> dict[str, Any]:
    creator = db.get(User, row.created_by)
    return {
        **serialize_row(row),
        "author": {
            "id": row.created_by,
            "name": creator.display_name if creator else "已停用用户",
        },
    }


@router.get("/documents/{document_id}/comments")
def list_document_comments(
    document_id: str,
    include_resolved: bool = True,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _document(db, document_id, user, "viewer")
    query = select(WritingComment).where(
        WritingComment.document_id == document_id,
        _active(WritingComment),
    )
    if not include_resolved:
        query = query.where(WritingComment.status == "open")
    rows = list(db.scalars(query.order_by(WritingComment.created_at, WritingComment.id)))
    return [_comment_payload(db, row) for row in rows]


@router.post("/documents/{document_id}/comments")
def create_document_comment(
    document_id: str,
    payload: WritingCommentCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document, project = _document(db, document_id, user, "commenter")
    current = db.get(WritingDocumentVersion, document.current_version_id) if document.current_version_id else None
    if payload.block_id:
        available = {str(node.get("id")) for node in walk_plate_nodes((current.content if current else []) or []) if node.get("id")}
        if payload.block_id not in available:
            raise HTTPException(status_code=422, detail="评论对应的正文块不存在于当前版本")
    parent = None
    if payload.parent_id:
        parent = _tenant_row(db, WritingComment, payload.parent_id, user.tenant_id, "上级评论")
        if parent.document_id != document.id:
            raise HTTPException(status_code=422, detail="不能回复其他文稿的评论")
    row_id = str(uuid.uuid4())
    thread_id = parent.thread_id if parent else (payload.thread_id or row_id)
    if payload.thread_id and not parent:
        root = db.scalar(
            select(WritingComment).where(
                WritingComment.document_id == document.id,
                WritingComment.thread_id == payload.thread_id,
                WritingComment.parent_id.is_(None),
                _active(WritingComment),
            )
        )
        if root is None:
            raise HTTPException(status_code=422, detail="评论线程不存在")
    row = WritingComment(
        id=row_id,
        tenant_id=user.tenant_id,
        project_id=project.id,
        document_id=document.id,
        thread_id=thread_id,
        parent_id=parent.id if parent else None,
        block_id=payload.block_id if not parent else (payload.block_id or parent.block_id),
        content=payload.content.strip(),
        status="open",
        created_by=user.id,
    )
    db.add(row)
    audit(db, user.tenant_id, user.id, "writing.comment.create", "writing_comment", row.id, {"document_id": document.id, "thread_id": thread_id})
    _commit(db)
    return _comment_payload(db, row)


@router.put("/comments/{comment_id}")
def update_document_comment(
    comment_id: str,
    payload: WritingCommentUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _tenant_row(db, WritingComment, comment_id, user.tenant_id, "评论")
    _document(db, row.document_id, user, "commenter")
    if row.created_by != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="只能修改自己创建的评论")
    if row.status != "open":
        raise HTTPException(status_code=409, detail="已解决的评论不能修改")
    row.content = payload.content.strip()
    audit(db, user.tenant_id, user.id, "writing.comment.update", "writing_comment", row.id)
    db.commit()
    return _comment_payload(db, row)


@router.post("/comments/{comment_id}/resolve")
def resolve_document_comment(
    comment_id: str,
    payload: WritingCommentResolve,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _tenant_row(db, WritingComment, comment_id, user.tenant_id, "评论")
    _document(db, row.document_id, user, "reviewer")
    status = "resolved" if payload.resolved else "open"
    now = datetime.now(timezone.utc)
    rows = list(
        db.scalars(
            select(WritingComment).where(
                WritingComment.document_id == row.document_id,
                WritingComment.thread_id == row.thread_id,
                _active(WritingComment),
            )
        )
    )
    for item in rows:
        item.status = status
        item.resolved_by = user.id if payload.resolved else None
        item.resolved_at = now if payload.resolved else None
    audit(db, user.tenant_id, user.id, "writing.comment.resolve" if payload.resolved else "writing.comment.reopen", "writing_comment", row.id, {"thread_id": row.thread_id})
    db.commit()
    return [_comment_payload(db, item) for item in rows]


@router.put("/documents/{document_id}")
def update_document(
    document_id: str,
    payload: WritingDocumentUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row, _ = _document(db, document_id, user, "editor")
    if row.status == "published" and payload.status not in {None, "archived"}:
        raise HTTPException(status_code=409, detail="已发布文稿不可直接恢复为草稿，请创建新版本")
    apply_patch(row, payload.model_dump(exclude_none=True), {"title", "status"})
    audit(db, user.tenant_id, user.id, "writing.document.update", "writing_document", row.id)
    _commit(db, "同一方案任务中已存在同名文稿")
    return serialize_row(row)


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft-delete a draft document while retaining immutable versions and audit history."""
    row, _ = _document(db, document_id, user, "editor")
    if row.status == "published":
        raise HTTPException(status_code=409, detail="已发布文稿不可直接删除，请先创建后续草稿或归档")
    deleted_at = datetime.now(timezone.utc)
    original_title = row.title
    row.title = f"{original_title}（已删除-{row.id[:8]}）"
    row.status = "archived"
    row.deleted_at = deleted_at
    audit(
        db,
        user.tenant_id,
        user.id,
        "writing.document.delete",
        "writing_document",
        row.id,
        {"title": original_title, "deleted_at": deleted_at.isoformat()},
    )
    db.commit()
    return {"ok": True}


@router.get("/documents/{document_id}/versions")
def list_document_versions(document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _document(db, document_id, user)
    return [serialize_row(row) for row in db.scalars(select(WritingDocumentVersion).where(WritingDocumentVersion.document_id == document_id, _active(WritingDocumentVersion)).order_by(WritingDocumentVersion.version.desc()))]


@router.post("/documents/{document_id}/versions")
def create_document_version(
    document_id: str,
    payload: WritingDocumentVersionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document, project = _document(db, document_id, user, "publisher" if payload.publish else "editor")
    bindings = {row.block_id: serialize_row(row) for row in db.scalars(select(WritingBlockBinding).where(WritingBlockBinding.document_id == document.id, _active(WritingBlockBinding)))}
    issues = validate_plate_content(payload.content, bindings)
    if payload.publish:
        pending_gates = int(db.scalar(select(func.count()).select_from(DecisionGate).where(DecisionGate.project_id == project.id, DecisionGate.required.is_(True), DecisionGate.status != "confirmed", _active(DecisionGate))) or 0)
        if pending_gates:
            raise HTTPException(status_code=409, detail=f"仍有 {pending_gates} 个必需确认节点未完成")
        blocking = [
            item
            for item in issues
            if item["code"]
            in {"missing_binding", "stale_binding", "unverified_binding", "trusted_block_modified"}
        ]
        if blocking:
            raise HTTPException(status_code=409, detail={"message": "文稿仍有不可发布的问题", "issues": blocking})
    next_hash = content_hash(payload.content)
    current = db.get(WritingDocumentVersion, document.current_version_id) if document.current_version_id else None
    if (
        current
        and current.content_hash == next_hash
        and current.scenario_package_version_id == project.scenario_package_version_id
        and current.knowledge_product_release_id == project.knowledge_product_release_id
        and not payload.publish
    ):
        return {**serialize_row(current), "issues": issues, "unchanged": True}
    number = int(db.scalar(select(func.max(WritingDocumentVersion.version)).where(WritingDocumentVersion.document_id == document.id)) or 0) + 1
    row = WritingDocumentVersion(
        tenant_id=user.tenant_id,
        document_id=document.id,
        version=number,
        content=payload.content,
        content_hash=next_hash,
        scenario_package_version_id=project.scenario_package_version_id,
        knowledge_product_release_id=project.knowledge_product_release_id,
        status="published" if payload.publish else "draft",
        change_summary=payload.change_summary,
        created_by=user.id,
        published_at=datetime.now(timezone.utc) if payload.publish else None,
    )
    db.add(row)
    db.flush()
    document.current_version_id = row.id
    document.status = "published" if payload.publish else "draft"
    audit(db, user.tenant_id, user.id, "writing.document.version.create", "writing_document_version", row.id, {"version": number, "published": payload.publish, "content_hash": row.content_hash})
    db.commit()
    return {**serialize_row(row), "issues": issues}


@router.get("/documents/{document_id}/bindings")
def list_bindings(document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _document(db, document_id, user)
    return [serialize_row(row) for row in db.scalars(select(WritingBlockBinding).where(WritingBlockBinding.document_id == document_id, _active(WritingBlockBinding)).order_by(WritingBlockBinding.block_id))]


@router.post("/documents/{document_id}/bindings")
def upsert_binding(
    document_id: str,
    payload: WritingBlockBindingUpsert,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document, project = _document(db, document_id, user, "editor")
    if payload.knowledge_product_release_id and payload.knowledge_product_release_id != project.knowledge_product_release_id:
        raise HTTPException(status_code=409, detail="引用必须属于方案任务锁定的知识产品版本")
    if payload.block_type == "knowledge_citation":
        query_run = _tenant_row(db, QueryRun, str(payload.query_run_id), user.tenant_id, "检索记录")
        query_policy = dict(query_run.retrieval_policy or {})
        if (
            query_run.user_id != user.id
            or query_policy.get("writing_project_id") != project.id
            or query_policy.get("knowledge_product_release_id") != project.knowledge_product_release_id
            or not any(
                str(item.get("chunk_id") or "") == str(payload.chunk_id)
                for item in (query_run.results or [])
            )
        ):
            raise HTTPException(status_code=403, detail="知识引用不属于当前方案任务的检索结果")
    if payload.fact_id:
        fact = _tenant_row(db, ProjectFact, payload.fact_id, user.tenant_id, "事实")
        if fact.project_id != project.id:
            raise HTTPException(status_code=403, detail="事实不属于当前方案任务")
        if payload.block_type == "inference_conclusion" and (
            fact.fact_type != "semantica_inference"
            or fact.verification_status != "verified"
            or fact.freshness_status != "current"
        ):
            raise HTTPException(status_code=409, detail="只能插入当前有效且已确认的规则推演结论")
    if payload.computation_run_id:
        run = _tenant_row(db, ComputationRun, payload.computation_run_id, user.tenant_id, "计算运行")
        if run.project_id != project.id:
            raise HTTPException(status_code=403, detail="计算运行不属于当前方案任务")
    row = db.scalar(select(WritingBlockBinding).where(WritingBlockBinding.document_id == document.id, WritingBlockBinding.block_id == payload.block_id))
    values = payload.model_dump(exclude={"metadata", "block_content"})
    if payload.block_content is not None:
        authoritative_hash = content_hash(payload.block_content)
        if payload.content_hash != authoritative_hash:
            raise HTTPException(status_code=422, detail="可信业务块哈希与提交内容不一致")
        values["content_hash"] = authoritative_hash
    if payload.block_type == "knowledge_citation":
        # Knowledge-search QueryRun and structured-SQL QueryRun are separate,
        # intentionally typed audit trails. Keep the public request contract
        # stable while persisting the retrieval FK in its dedicated column.
        values["retrieval_query_run_id"] = values.pop("query_run_id", None)
    values["metadata_json"] = {
        **payload.metadata,
        **({"content_hash_algorithm": "canonical-json-v1"} if payload.block_content is not None else {}),
    }
    if row:
        row.deleted_at = None
        apply_patch(row, values, set(values))
    else:
        row = WritingBlockBinding(tenant_id=user.tenant_id, project_id=project.id, document_id=document.id, **values)
        db.add(row)
    audit(db, user.tenant_id, user.id, "writing.document.binding.upsert", "writing_document", document.id, {"block_id": payload.block_id, "block_type": payload.block_type, "source_type": payload.source_type})
    _commit(db)
    return serialize_row(row)


@router.post("/documents/{document_id}/validate")
def validate_document(
    document_id: str,
    payload: WritingDocumentValidate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document, project = _document(db, document_id, user)
    version = db.get(WritingDocumentVersion, document.current_version_id) if document.current_version_id else None
    content = payload.content if payload.content is not None else (version.content if version else [])
    bindings = {row.block_id: serialize_row(row) for row in db.scalars(select(WritingBlockBinding).where(WritingBlockBinding.document_id == document.id, _active(WritingBlockBinding)))}
    issues = validate_plate_content(content, bindings)
    pending_gates = [serialize_row(row) for row in db.scalars(select(DecisionGate).where(DecisionGate.project_id == project.id, DecisionGate.required.is_(True), DecisionGate.status != "confirmed", _active(DecisionGate)))]
    return {"ok": not issues and (not payload.for_publish or not pending_gates), "issues": issues, "pending_decision_gates": pending_gates, "content_hash": content_hash(content)}


@router.get("/documents/{document_id}/stale-blocks")
def stale_blocks(document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    document, _ = _document(db, document_id, user)
    current = db.get(WritingDocumentVersion, document.current_version_id) if document.current_version_id else None
    current_block_ids = {
        str(node.get("id"))
        for node in walk_plate_nodes((current.content if current else []) or [])
        if node.get("id")
    }
    if not current_block_ids:
        return []
    return [
        serialize_row(row)
        for row in db.scalars(
            select(WritingBlockBinding)
            .where(
                WritingBlockBinding.document_id == document_id,
                WritingBlockBinding.block_id.in_(current_block_ids),
                WritingBlockBinding.freshness_status != "current",
                _active(WritingBlockBinding),
            )
            .order_by(WritingBlockBinding.updated_at.desc())
        )
    ]


@router.post("/documents/{document_id}/recompute")
def recompute_impacts(
    document_id: str,
    payload: WritingRecomputeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document, project = _document(db, document_id, user, "editor")
    requested_fact_ids = set(payload.changed_fact_ids)
    facts = list(db.scalars(select(ProjectFact).where(ProjectFact.id.in_(requested_fact_ids), ProjectFact.project_id == project.id, _active(ProjectFact))))
    if len(facts) != len(requested_fact_ids):
        raise HTTPException(status_code=422, detail="变更事实不存在或不属于当前方案任务")
    changed_keys = {fact.fact_key for fact in facts}
    # Dependency runs are immutable and retain the exact historical fact ID.
    # Expand a changed logical fact to every version of that key so a new fact
    # version invalidates calculations that consumed its predecessor.
    fact_ids = set(
        db.scalars(
            select(ProjectFact.id).where(
                ProjectFact.project_id == project.id,
                ProjectFact.fact_key.in_(changed_keys),
                _active(ProjectFact),
            )
        )
    )
    run_rows = list(db.scalars(select(ComputationRun).where(ComputationRun.project_id == project.id, _active(ComputationRun)).order_by(ComputationRun.created_at.desc())))
    runs = [serialize_row(row) for row in run_rows]
    bindings = [serialize_row(row) for row in db.scalars(select(WritingBlockBinding).where(WritingBlockBinding.document_id == document.id, _active(WritingBlockBinding)))]
    impact = affected_dependency_ids(fact_ids, runs, bindings)
    if impact["block_ids"]:
        db.query(WritingBlockBinding).filter(WritingBlockBinding.document_id == document.id, WritingBlockBinding.block_id.in_(impact["block_ids"]), _active(WritingBlockBinding)).update({"freshness_status": "stale"}, synchronize_session=False)
    active_by_key = {
        row.fact_key: row
        for row in db.scalars(
            select(ProjectFact).where(
                ProjectFact.project_id == project.id,
                ProjectFact.active.is_(True),
                ProjectFact.verification_status == "verified",
                _active(ProjectFact),
            )
        )
    }
    latest_dependencies: dict[tuple[str, str], ComputationRun] = {}
    for old_run in run_rows:
        if old_run.id not in set(impact["computation_run_ids"]):
            continue
        output = dict((old_run.result or {}).get("output_fact") or {})
        key = (old_run.definition_version_id, str(output.get("fact_key") or old_run.id))
        latest_dependencies.setdefault(key, old_run)
    replacements = []
    for old_run in latest_dependencies.values():
        dependencies = dict((old_run.result or {}).get("dependencies") or {})
        if not dependencies:
            replacements.append({"previous_run_id": old_run.id, "status": "manual_input_mapping_required"})
            continue
        missing = sorted(set(dependencies.values()) - set(active_by_key))
        if missing:
            replacements.append({"previous_run_id": old_run.id, "status": "missing_verified_facts", "missing": missing})
            continue
        input_facts = {name: active_by_key[fact_key] for name, fact_key in dependencies.items()}
        inputs = dict(old_run.inputs or {})
        for name, fact in input_facts.items():
            inputs[name] = _fact_number(fact)
        definition = _tenant_row(db, ComputationDefinitionVersion, old_run.definition_version_id, user.tenant_id, "公式版本")
        expected_checksum = content_hash(
            {
                "definition": definition.checksum,
                "inputs": inputs,
                "parameters": (old_run.result or {}).get("parameters") or {},
                "dependencies": dependencies,
            }
        )
        new_run = db.scalar(
            select(ComputationRun).where(
                ComputationRun.project_id == project.id,
                ComputationRun.checksum == expected_checksum,
                _active(ComputationRun),
            ).order_by(ComputationRun.created_at.desc())
        )
        output_fact = dict((old_run.result or {}).get("output_fact") or {}) or None
        generated = None
        if new_run is None:
            new_run, generated = _execute_computation_run(
                db,
                project=project,
                user=user,
                definition_version=definition,
                inputs=inputs,
                parameters=(old_run.result or {}).get("parameters") or {},
                rounding=(old_run.result or {}).get("rounding") or definition.rounding,
                input_facts=input_facts,
                output_fact=output_fact,
            )
        replacements.append(
            {
                "previous_run_id": old_run.id,
                "replacement_run_id": new_run.id,
                "status": "recomputed",
                "result": new_run.result,
                "generated_fact_id": generated.id if generated else None,
            }
        )
    audit(db, user.tenant_id, user.id, "writing.document.impact", "writing_document", document.id, impact)
    db.commit()
    return {
        **impact,
        "action": "recomputed_and_marked_stale",
        "automatic_overwrite": False,
        "replacement_runs": replacements,
    }


@router.post("/documents/{document_id}/exports")
def create_export(
    document_id: str,
    payload: WritingExportCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document, project = _document(db, document_id, user, "publisher")
    version = db.get(WritingDocumentVersion, document.current_version_id) if document.current_version_id else None
    if version is None or version.deleted_at is not None:
        raise HTTPException(status_code=409, detail="请先保存一个文稿版本")
    bindings = {
        row.block_id: serialize_row(row)
        for row in db.scalars(
            select(WritingBlockBinding).where(
                WritingBlockBinding.document_id == document.id,
                _active(WritingBlockBinding),
            )
        )
    }
    issues = validate_plate_content(version.content or [], bindings)
    if issues:
        raise HTTPException(status_code=409, detail=f"文稿存在 {len(issues)} 个来源或有效性问题，请先完成审校")
    pending_gates = list(
        db.scalars(
            select(DecisionGate).where(
                DecisionGate.project_id == project.id,
                DecisionGate.required.is_(True),
                DecisionGate.status != "confirmed",
                _active(DecisionGate),
            )
        )
    )
    if payload.output_format in {"docx", "pdf"} and pending_gates:
        raise HTTPException(status_code=409, detail=f"仍有 {len(pending_gates)} 个业务确认节点未完成")
    template_version = None
    if payload.template_version_id:
        template_version = _tenant_row(db, ExportTemplateVersion, payload.template_version_id, user.tenant_id, "导出模板版本")
        if template_version.status != "active":
            raise HTTPException(status_code=409, detail="导出模板版本未激活")
    facts = [
        serialize_row(row)
        for row in db.scalars(
            select(ProjectFact).where(
                ProjectFact.project_id == project.id,
                ProjectFact.active.is_(True),
                ProjectFact.verification_status == "verified",
                _active(ProjectFact),
            ).order_by(ProjectFact.fact_key)
        )
    ]
    computations = [
        serialize_row(row)
        for row in db.scalars(
            select(ComputationRun).where(
                ComputationRun.project_id == project.id,
                _active(ComputationRun),
            ).order_by(ComputationRun.created_at.desc())
        )
    ]
    plans = [
        serialize_row(row)
        for row in db.scalars(
            select(AlternativePlan).where(
                AlternativePlan.project_id == project.id,
                _active(AlternativePlan),
            ).order_by(AlternativePlan.created_at.desc())
        )
    ]
    release = _tenant_row(db, KnowledgeProductRelease, project.knowledge_product_release_id, user.tenant_id, "知识产品版本")
    scenario = _tenant_row(db, ScenarioPackageVersion, project.scenario_package_version_id, user.tenant_id, "场景包版本")
    selected_plan = next((item["name"] for item in plans if item["status"] == "selected"), None)
    audit_summary = {
        "knowledge_product_release": release.version,
        "scenario_package_version": scenario.version,
        "verified_fact_count": len(facts),
        "computation_count": len(computations),
        "reasoning_count": int(
            db.scalar(
                select(func.count(WritingReasoningRun.id)).where(
                    WritingReasoningRun.project_id == project.id,
                    WritingReasoningRun.status == "succeeded",
                    _active(WritingReasoningRun),
                )
            ) or 0
        ),
        "selected_plan": selected_plan,
    }
    job = ExportJob(
        tenant_id=user.tenant_id,
        project_id=project.id,
        document_id=document.id,
        document_version_id=version.id,
        template_version_id=template_version.id if template_version else None,
        requested_by=user.id,
        output_format=payload.output_format,
        status="running",
        progress=10,
        manifest={"audit_summary": audit_summary},
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()
    safe_title = "".join(character if character not in "\\/:*?\"<>|" else "_" for character in document.title).strip() or "妙笔文稿"
    filename = f"{safe_title}-v{version.version}.{payload.output_format}"
    object_key = f"{user.tenant_id}/writing/{project.id}/{document.id}/{job.id}/{filename}"
    try:
        with tempfile.TemporaryDirectory(prefix="miaobi-export-") as temp_directory:
            target = Path(temp_directory) / filename
            project_config = dict(project.config or {})
            plan_inputs = project_config.get("plan_inputs")
            route_coordinates = project_config.get("route_coordinates")
            if not route_coordinates and isinstance(plan_inputs, dict):
                route_coordinates = plan_inputs.get("coordinates")
            build_export_artifact(
                target,
                output_format=payload.output_format,
                title=document.title,
                content=version.content or [],
                facts=facts,
                computations=computations,
                plans=plans,
                audit_summary=audit_summary,
                coordinates=dict(route_coordinates or {}),
            )
            checksum = hashlib.sha256(target.read_bytes()).hexdigest()
            object_storage.put_file(object_key, target, CONTENT_TYPES[payload.output_format])
    except (ValueError, RuntimeError) as exc:
        job.status = "failed"
        job.progress = 100
        job.error_code = type(exc).__name__
        job.error_message = str(exc)[:500]
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        job.status = "failed"
        job.progress = 100
        job.error_code = type(exc).__name__
        job.error_message = "导出服务执行失败"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=500, detail="导出服务执行失败") from exc
    job.status = "succeeded"
    job.progress = 100
    job.object_key = object_key
    job.checksum = checksum
    job.manifest = {
        "filename": filename,
        "content_type": CONTENT_TYPES[payload.output_format],
        "document_version": version.version,
        "audit_summary": audit_summary,
    }
    job.finished_at = datetime.now(timezone.utc)
    audit(db, user.tenant_id, user.id, "writing.export.create", "writing_export_job", job.id, {"format": payload.output_format, "checksum": checksum, "document_version": version.version})
    db.commit()
    db.refresh(job)
    return serialize_row(job)


@router.get("/documents/{document_id}/exports")
def list_exports(document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _document(db, document_id, user)
    return [
        serialize_row(row)
        for row in db.scalars(
            select(ExportJob).where(
                ExportJob.document_id == document_id,
                _active(ExportJob),
            ).order_by(ExportJob.created_at.desc())
        )
    ]


@router.get("/exports/{job_id}/download")
def download_export(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = _tenant_row(db, ExportJob, job_id, user.tenant_id, "导出任务")
    _document(db, job.document_id, user)
    if job.status != "succeeded" or not job.object_key:
        raise HTTPException(status_code=409, detail="导出文件尚未可用")
    filename = str((job.manifest or {}).get("filename") or f"妙笔文稿.{job.output_format}")
    media_type = str((job.manifest or {}).get("content_type") or CONTENT_TYPES.get(job.output_format, "application/octet-stream"))
    return Response(
        object_storage.get_bytes(job.object_key),
        media_type=media_type,
        headers={"Content-Disposition": attachment_content_disposition(filename), "X-Content-Type-Options": "nosniff"},
    )
