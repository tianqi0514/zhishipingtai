from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import get_current_user, has_space_permission, require_permission
from apps.api.utils import apply_patch, serialize_row
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
    WritingMemberCreate,
    WritingProjectCreate,
    WritingProjectUpdate,
    WritingRecomputeRequest,
)
from packages.platform.audit import audit
from packages.platform.database import get_db
from packages.platform.models import (
    AlternativePlan,
    Application,
    ComputationDefinition,
    ComputationDefinitionVersion,
    ComputationRun,
    DecisionGate,
    DecisionRecord,
    KnowledgeProductRelease,
    KnowledgeProductReleaseItem,
    ProjectFact,
    ScenarioPackage,
    ScenarioPackageVersion,
    User,
    WritingBlockBinding,
    WritingDocument,
    WritingDocumentVersion,
    WritingProject,
    WritingProjectMember,
)
from packages.platform.writing import (
    BUILTIN_FORMULAS,
    affected_dependency_ids,
    content_hash,
    execute_formula,
    generate_alternative_plans,
    validate_plate_content,
    validate_scenario_contract,
)


router = APIRouter(prefix="/writing", tags=["miaobi-writing"])
require_writing_admin = require_permission("writing.manage")

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
        row.value = payload.new_value
        row.fact_type = "manual_override"
        row.source_type = "manual_override"
        row.freshness_status = "manual_override"
    row.verification_status = "verified" if payload.decision in {"confirm", "override"} else "rejected"
    row.confirmed_by = user.id
    row.confirmed_at = datetime.now(timezone.utc)
    audit(db, user.tenant_id, user.id, f"writing.fact.{payload.decision}", "writing_project_fact", row.id, {"reason": payload.reason})
    db.commit()
    return serialize_row(row)


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
    try:
        result = execute_formula(
            definition_version.operation,
            payload.inputs,
            parameters={**(definition_version.default_parameters or {}), **payload.parameters},
            rounding=payload.rounding or definition_version.rounding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = datetime.now(timezone.utc)
    run = ComputationRun(
        tenant_id=user.tenant_id,
        project_id=project.id,
        definition_version_id=definition_version.id,
        status="succeeded",
        inputs=payload.inputs,
        result=result,
        input_fact_ids=payload.input_fact_ids,
        checksum=content_hash({"definition": definition_version.checksum, "inputs": payload.inputs, "parameters": payload.parameters}),
        started_at=now,
        finished_at=now,
        created_by=user.id,
    )
    db.add(run)
    audit(db, user.tenant_id, user.id, "writing.computation.execute", "computation_run", run.id, {"operation": definition_version.operation, "checksum": run.checksum})
    db.commit()
    db.refresh(run)
    return serialize_row(run)


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
    try:
        generated = generate_alternative_plans(payload.inputs, payload.count)
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
            inputs=payload.inputs,
            constraints=payload.inputs.get("constraints") or [],
            result={"route": result["route"], "resource_allocation": result["resource_allocation"], "input_fingerprint": result["input_fingerprint"]},
            algorithm=result["algorithm"],
            unresolved_gaps=result["unresolved_gaps"],
            risks=payload.inputs.get("risks") or [],
            status="candidate",
        )
        db.add(row)
        rows.append(row)
    audit(db, user.tenant_id, user.id, "writing.plans.generate", "writing_project", project.id, {"count": len(rows), "input_fingerprint": content_hash(payload.inputs)})
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
        blocking = [item for item in issues if item["code"] in {"missing_binding", "stale_binding", "unverified_binding"}]
        if blocking:
            raise HTTPException(status_code=409, detail={"message": "文稿仍有不可发布的问题", "issues": blocking})
    next_hash = content_hash(payload.content)
    current = db.get(WritingDocumentVersion, document.current_version_id) if document.current_version_id else None
    if current and current.content_hash == next_hash and not payload.publish:
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
    if payload.fact_id:
        fact = _tenant_row(db, ProjectFact, payload.fact_id, user.tenant_id, "事实")
        if fact.project_id != project.id:
            raise HTTPException(status_code=403, detail="事实不属于当前方案任务")
    if payload.computation_run_id:
        run = _tenant_row(db, ComputationRun, payload.computation_run_id, user.tenant_id, "计算运行")
        if run.project_id != project.id:
            raise HTTPException(status_code=403, detail="计算运行不属于当前方案任务")
    row = db.scalar(select(WritingBlockBinding).where(WritingBlockBinding.document_id == document.id, WritingBlockBinding.block_id == payload.block_id))
    values = payload.model_dump(exclude={"metadata"})
    values["metadata_json"] = payload.metadata
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
    _document(db, document_id, user)
    return [serialize_row(row) for row in db.scalars(select(WritingBlockBinding).where(WritingBlockBinding.document_id == document_id, WritingBlockBinding.freshness_status != "current", _active(WritingBlockBinding)).order_by(WritingBlockBinding.updated_at.desc()))]


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
    runs = [serialize_row(row) for row in db.scalars(select(ComputationRun).where(ComputationRun.project_id == project.id, _active(ComputationRun)))]
    bindings = [serialize_row(row) for row in db.scalars(select(WritingBlockBinding).where(WritingBlockBinding.document_id == document.id, _active(WritingBlockBinding)))]
    impact = affected_dependency_ids(fact_ids, runs, bindings)
    if impact["block_ids"]:
        db.query(WritingBlockBinding).filter(WritingBlockBinding.document_id == document.id, WritingBlockBinding.block_id.in_(impact["block_ids"]), _active(WritingBlockBinding)).update({"freshness_status": "stale"}, synchronize_session=False)
    audit(db, user.tenant_id, user.id, "writing.document.impact", "writing_document", document.id, impact)
    db.commit()
    return {**impact, "action": "marked_stale", "automatic_overwrite": False}
