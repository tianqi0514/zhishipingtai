from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import get_current_user, require_space_permission
from apps.api.utils import serialize_row
from packages.platform.audit import audit
from packages.platform.database import get_db
from packages.platform.models import (
    User, DocumentVersion, Document, Job, WritingAssetVersion, WritingAssetRelease,
    WritingExtractionRun, WritingEvidence, ProjectFact, ComputationDefinition, ComputationDefinitionVersion, ComputationRun,
)
from packages.platform.model_routing import resolve_model_for_scene
from packages.platform.security import decrypt_secret
from packages.platform.writing_graph import content_hash, utcnow
from packages.platform.writing_assets import publish_assets
from packages.platform.writing_assets_schema import (
    Strict, ASSET_KINDS, AssetCandidate, ComputationMethod, compute_method, numeric_value,
)
from packages.semantica_adapter.upstream import upgraded_capabilities

router = APIRouter(prefix="/writing-assets", tags=["writing-assets"])


class TestCase(Strict):
    inputs: dict[str, Any]
    expected: str


class Review(Strict):
    checksum: str
    action: Literal["accept", "reject"]
    reason: str = Field(min_length=2, max_length=1000)
    unit_confirmed: bool = False
    tests: list[TestCase] = Field(default_factory=list, max_length=30)


class Revise(Strict):
    checksum: str
    definition: AssetCandidate


class Publish(Strict):
    space_id: str
    asset_ids: list[str] = Field(min_length=1, max_length=500)


class Adopt(Strict):
    release_id: str
    asset_ids: list[str] = Field(min_length=1, max_length=100)
    base_version_id: str | None
    scope_confirmed: bool
    adopt_outline: bool = False


class ComputeAll(Strict):
    base_version_id: str | None
    release_id: str
    checksum: str


class ExtractRequest(Strict):
    version_id: str
    request_id: str = Field(min_length=8, max_length=100)
    material_role: Literal["task_data", "policy_basis", "reference", "sample_style", "attachment"]


def _row(db: Session, row_id: str, user: User, model=WritingAssetVersion, permission="read"):
    row = db.get(model, row_id)
    if row is None or row.tenant_id != user.tenant_id or row.deleted_at is not None:
        raise HTTPException(404, "写作资产不存在")
    require_space_permission(db, user, row.space_id, permission)
    return row


def _commit(db: Session):
    try: db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "资料或版本已变化，请刷新后重试") from exc


@router.get("/kinds")
def kinds(user: User = Depends(get_current_user)):
    return {"kinds": [{"key": key, "label": label} for key, label in ASSET_KINDS.items()], "engine": upgraded_capabilities()}


@router.get("")
def list_assets(space_id: str | None = None, project_id: str | None = None,
                kind: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if project_id:
        from apps.api.writing import _project
        space_id = _project(db, project_id, user).knowledge_space_id
        if not space_id: return []
    if not space_id: raise HTTPException(422, "请选择知识空间")
    require_space_permission(db, user, space_id, "read")
    query = select(WritingAssetVersion).where(
        WritingAssetVersion.tenant_id == user.tenant_id, WritingAssetVersion.space_id == space_id,
        WritingAssetVersion.deleted_at.is_(None),
    ).order_by(WritingAssetVersion.created_at.desc())
    if kind: query = query.where(WritingAssetVersion.kind == kind)
    # Explicit pagination prevents a misleading all-results view on large corpora.
    return [serialize_row(row) for row in db.scalars(query.limit(500))]


@router.get("/releases")
def releases(space_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, space_id, "read")
    return [serialize_row(row) for row in db.scalars(select(WritingAssetRelease).where(
        WritingAssetRelease.tenant_id == user.tenant_id, WritingAssetRelease.space_id == space_id,
        WritingAssetRelease.deleted_at.is_(None),
    ).order_by(WritingAssetRelease.release_number.desc()).limit(50))]


@router.post("/extract", status_code=202)
def start_extract(payload: ExtractRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    version = db.get(DocumentVersion, payload.version_id)
    if not version or version.tenant_id != user.tenant_id or version.deleted_at is not None:
        raise HTTPException(404, "资料版本不存在")
    document = db.get(Document, version.document_id)
    if not document or document.tenant_id != user.tenant_id or document.deleted_at is not None:
        raise HTTPException(404, "资料不存在")
    require_space_permission(db, user, document.space_id, "write")
    if version.status not in {"parsed", "ready", "processed", "published"}:
        raise HTTPException(409, "请先完成文档解析")
    model = resolve_model_for_scene(db, user.tenant_id, "semantic_extract").model
    if model is None or not decrypt_secret(model.api_key_encrypted):
        raise HTTPException(409, "当前抽取模型不可用，请先检查模型配置")
    key = content_hash({"tenant": user.tenant_id, "version": version.id, "request": payload.request_id})
    existing = db.scalar(select(Job).where(Job.idempotency_key == key))
    if existing: return serialize_row(existing)
    job = Job(tenant_id=user.tenant_id, job_type="writing_assets", idempotency_key=key,
    input={"version_id": version.id, "document_id": document.id, "space_id": document.space_id,
                     "model_config_id": model.id, "material_role": payload.material_role, "actor_id": user.id})
    db.add(job); _commit(db); db.refresh(job)
    try:
        from apps.worker.tasks import extract_writing_assets_task
        extract_writing_assets_task.delay(job.id)
    except Exception as exc:
        job.status = "failed"; job.error_code = "ASSET_QUEUE_UNAVAILABLE"
        job.error_message = "抽取任务未能进入队列，请检查任务服务并重试。"; _commit(db)
        raise HTTPException(503, job.error_message) from exc
    return serialize_row(job)


@router.get("/{asset_id}/trace")
def trace(asset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _row(db, asset_id, user)
    run = db.get(WritingExtractionRun, row.extraction_run_id)
    sources = list(db.scalars(select(WritingEvidence).where(
        WritingEvidence.tenant_id == user.tenant_id, WritingEvidence.space_id == row.space_id,
        WritingEvidence.id.in_(run.evidence_ids), WritingEvidence.deleted_at.is_(None),
    )))
    return {"asset": serialize_row(row), "input": [serialize_row(item) for item in sources],
            "process": serialize_row(run), "output": (run.metrics or {}).get("output"),
            "runtime_prompt": (run.metrics or {}).get("prompt")}


@router.post("/{asset_id}/revise")
def revise(asset_id: str, payload: Revise, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from packages.platform.writing_assets import validate_asset
    row = _row(db, asset_id, user, permission="write")
    if payload.checksum != row.checksum: raise HTTPException(409, "资产版本已变化")
    definition = payload.definition
    if definition.kind != row.kind or definition.key != row.definition["key"]:
        raise HTTPException(422, "类型和语义键不可在修订时更换")
    signed = set(db.get(WritingExtractionRun, row.extraction_run_id).evidence_ids)
    if set(definition.evidence_ids) - signed: raise HTTPException(422, "来源不属于原抽取批次")
    version = int(db.scalar(select(func.max(WritingAssetVersion.version)).where(
        WritingAssetVersion.asset_key == row.asset_key, WritingAssetVersion.space_id == row.space_id)) or 0) + 1
    replacement = WritingAssetVersion(tenant_id=user.tenant_id, space_id=row.space_id, extraction_run_id=row.extraction_run_id,
        asset_key=row.asset_key, kind=row.kind, version=version, title=definition.title,
        definition=definition.model_dump(), checksum=content_hash(definition.model_dump()),
        validation=validate_asset(definition), status="candidate")
    db.add(replacement)
    audit(db, user.tenant_id, user.id, "writing_asset.revise", "writing_asset", row.id)
    _commit(db); return serialize_row(replacement)


@router.post("/{asset_id}/review")
def review(asset_id: str, payload: Review, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _row(db, asset_id, user, permission="write")
    if payload.checksum != row.checksum: raise HTTPException(409, "资产版本已变化")
    if payload.action == "accept" and row.kind == "computation_method":
        if not payload.unit_confirmed or len(payload.tests) < 2:
            raise HTTPException(422, "计算方法须确认量纲和舍入，并至少验证两组输入（含边界）")
        method = ComputationMethod.model_validate(row.definition["payload"])
        try:
            normalized_inputs = {tuple(sorted((key, numeric_value(value)) for key, value in test.inputs.items()))
                                 for test in payload.tests}
            if len(normalized_inputs) < 2:
                raise ValueError("请提供至少两组不同的输入，重复验算不能代替边界验证")
            if any(numeric_value(compute_method(method, test.inputs)) != numeric_value(test.expected) for test in payload.tests):
                raise ValueError("公式与预期结果不符")
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc
        row.validation = {**row.validation, "execution": "passed", "unit_check": "human_confirmed", "tests": [test.model_dump() for test in payload.tests]}
    if payload.action == "accept" and row.kind == "ontology" and not (row.validation.get("ontology") or {}).get("passed"):
        raise HTTPException(422, "本体未通过原生质量检查，请修订后再确认")
    row.status = "reviewed" if payload.action == "accept" else "rejected"
    row.validation = {**row.validation, "business": row.status}
    row.reviewed_by = user.id; row.reviewed_at = utcnow()
    audit(db, user.tenant_id, user.id, "writing_asset.review", "writing_asset", row.id, {"reason": payload.reason, "action": payload.action})
    _commit(db); return serialize_row(row)


@router.post("/releases")
def publish(payload: Publish, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_space_permission(db, user, payload.space_id, "write")
    try: row = publish_assets(db, user.tenant_id, payload.space_id, user.id, payload.asset_ids)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    audit(db, user.tenant_id, user.id, "writing_asset.publish", "writing_asset_release", row.id)
    _commit(db); return serialize_row(row)


@router.post("/documents/{document_id}/adopt")
def adopt(document_id: str, payload: Adopt, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from apps.api.writing import _document, _next_project_fact_version
    from apps.api.writing_schemas import NativeOutlineSection
    document, project = _document(db, document_id, user, "editor")
    if document.status == "published": raise HTTPException(409, "定稿文章不能被直接修改")
    if document.current_version_id != payload.base_version_id: raise HTTPException(409, "文章已变化，请重新预览")
    if not payload.scope_confirmed: raise HTTPException(422, "请先确认资产适用于本文，历史数值不会采用")
    release = _row(db, payload.release_id, user, model=WritingAssetRelease)
    if release.space_id != project.knowledge_space_id: raise HTTPException(422, "资产与项目知识空间不一致")
    assets = [item for item in release.snapshot["assets"] if item["id"] in payload.asset_ids]
    if len(assets) != len(set(payload.asset_ids)) or len(payload.asset_ids) != len(set(payload.asset_ids)):
        raise HTTPException(422, "资产不属于所选发布版本")
    keys = {item["definition"]["key"] for item in assets}
    if any(set(item["definition"].get("dependencies") or []) - keys for item in assets):
        raise HTTPException(422, "所选内容缺少前置定义，请一并选择依赖的资产")
    # Validate the complete candidate before mutating the document.
    applicability = {**(document.applicability or {}), "asset_bundle": {
        "release_id": release.id, "checksum": release.checksum, "assets": assets,
        "adopted_by": user.id, "scope_confirmed": True,
    }}
    variables: dict[str, dict] = {}
    outputs: dict[str, dict] = {}
    for item in assets:
        definition = item["definition"]
        if definition["kind"] == "variable": variables[definition["payload"]["key"]] = definition["payload"]
        if definition["kind"] == "report_form": variables.update({field["key"]: field for field in definition["payload"]["fields"]})
        if definition["kind"] == "computation_method":
            variables.update({field["key"]: field for field in definition["payload"]["inputs"]})
            output = definition["payload"]["output"]
            outputs[output["key"]] = output
    # A structural template may describe inputs by business name. Resolve exact
    # names to declared keys; never guess a value, type or unit for unknowns.
    names: dict[str, list[str]] = {}
    for key, field in {**variables, **outputs}.items():
        names.setdefault(field["name"], []).append(key)
    def resolve_input(name: str) -> str:
        if name in variables or name in outputs: return name
        matches = names.get(name, [])
        if len(matches) > 1: raise HTTPException(422, f"章节输入“{name}”对应多个指标，请先修订名称")
        if matches: return matches[0]
        key = name if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.]{0,99}", name) else f"article_input_{content_hash(name)[:16]}"
        variables[key] = {"key": key, "name": name, "unit": "", "value_type": "text",
                          "instruction": "章节要求的本次信息，需补充并确认；不是历史事实"}
        return key
    structure = [item for item in assets if item["definition"]["kind"] == "report_structure"]
    if payload.adopt_outline:
        if len(structure) != 1: raise HTTPException(422, "请只采用一个文章结构")
        if applicability.get("native_outline"):
            raise HTTPException(409, "本文已有目录；请在目录编辑中确认合并，不自动覆盖")
        sections = structure[0]["definition"]["payload"]["chapters"]
        def section_name(value: str) -> str:
            return re.sub(r"^[一二三四五六七八九十百\d]+[、.．\s]+", "", value.strip())
        def chapter_outputs(chapter: dict) -> list[str]:
            # Only explicit chapter-key/title matches; do not guess usage from
            # semantically similar prose or rewrite all chapters with a result.
            return [item["definition"]["payload"]["output"]["key"] for item in assets
                    if item["definition"]["kind"] == "computation_method"
                    and any(name == chapter["key"] or section_name(name) == section_name(chapter["title"])
                            for name in item["definition"].get("used_in_sections", []))]
        planned = [
            {**chapter, "key": re.sub(r"[^a-z0-9_-]", "_", chapter["key"].lower()),
             "required_inputs": [resolve_input(name) for name in chapter.get("required_inputs", [])],
             "toolbox_outputs": chapter_outputs(chapter)} for chapter in sections]
        try:
            planned = [NativeOutlineSection.model_validate(chapter).model_dump() for chapter in planned]
            if len({chapter["key"] for chapter in planned}) != len(planned):
                raise ValueError("章节标识转换后重复")
        except ValueError as exc:
            raise HTTPException(422, "结构不能作为目录采用，请修订章节标识或要求") from exc
        applicability["native_outline"] = {"sections": planned}
    # Computed outputs are not empty author inputs. They arrive only through a
    # successful formula run, including when a later method consumes them.
    variables = {key: value for key, value in variables.items() if key not in outputs}
    existing = set(db.scalars(select(ProjectFact.fact_key).where(ProjectFact.project_id == project.id, ProjectFact.active.is_(True), ProjectFact.deleted_at.is_(None))))
    for key, variable in variables.items():
        if key in existing: continue
        db.add(ProjectFact(tenant_id=user.tenant_id, project_id=project.id, fact_key=key,
            label=variable["name"], unit=variable.get("unit"), fact_type="manual_input", source_type="manual_input",
            value={}, verification_status="unverified", version=_next_project_fact_version(db, project.id, key), active=True, created_by=user.id,
            source_locator={"required_by_asset_release": release.id, "instruction": variable.get("instruction", ""),
                            "value_type": variable.get("value_type", "text")}))
    document.applicability = applicability
    audit(db, user.tenant_id, user.id, "writing_asset.adopt", "writing_document", document.id, {"release_id": release.id, "asset_ids": payload.asset_ids})
    _commit(db); return {"document": serialize_row(document), "required_current_inputs": list(variables)}


def _adopted_assets(db: Session, document, project, user: User) -> list[dict]:
    bundle = (document.applicability or {}).get("asset_bundle") or {}
    release_id = bundle.get("release_id")
    if not release_id:
        raise HTTPException(409, "请先采用已确认的结构与方法")
    release = _row(db, release_id, user, model=WritingAssetRelease)
    if release.space_id != project.knowledge_space_id or release.checksum != bundle.get("checksum"):
        raise HTTPException(409, "本文采用的资产版本不一致，请重新确认")
    assets = bundle.get("assets") or []
    signed = {item["id"]: item for item in release.snapshot["assets"]}
    if (len(assets) != len({item["id"] for item in assets})
            or any(signed.get(item["id"]) != item for item in assets)):
        raise HTTPException(409, "本文定义与发布快照不一致，不能执行")
    return assets


def _compute_asset(db: Session, project, user: User, item: dict) -> dict:
    from apps.api.writing import _execute_computation_run
    if item["definition"]["kind"] != "computation_method" or item["validation"].get("execution") != "passed":
        raise HTTPException(409, "本文未采用经过测试的计算方法")
    asset_id = item["id"]
    method = ComputationMethod.model_validate(item["definition"]["payload"])
    facts = {row.fact_key: row for row in db.scalars(select(ProjectFact).where(
        ProjectFact.project_id == project.id, ProjectFact.tenant_id == user.tenant_id,
        ProjectFact.active.is_(True), ProjectFact.verification_status == "verified",
        ProjectFact.freshness_status.in_(["current", "manual_override"]), ProjectFact.deleted_at.is_(None),
    ))}
    selected = {}
    for variable in method.inputs:
        row = facts.get(variable.key)
        if not row: raise HTTPException(409, f"请先补充并确认当前输入：{variable.name}")
        if (row.unit or "") != variable.unit: raise HTTPException(422, f"{variable.name}单位不一致，请确认口径")
        selected[variable.key] = row
    code = f"asset-{asset_id}"
    definition = db.scalar(select(ComputationDefinition).where(ComputationDefinition.tenant_id == user.tenant_id, ComputationDefinition.code == code))
    if definition is None:
        definition = ComputationDefinition(tenant_id=user.tenant_id, code=code, name=item["definition"]["title"][:200], description=item["definition"]["purpose"])
        db.add(definition); db.flush()
    version = db.scalar(select(ComputationDefinitionVersion).where(ComputationDefinitionVersion.definition_id == definition.id, ComputationDefinitionVersion.checksum == item["checksum"]))
    if version is None:
        version = ComputationDefinitionVersion(tenant_id=user.tenant_id, definition_id=definition.id,
            version=1, operation="writing_asset_expression", expression=method.expression,
            input_schema={"required": [field.key for field in method.inputs]}, output_schema={"type": "number"},
            unit=method.output.unit, rounding={"mode": "half_up", "digits": method.precision},
            default_parameters={"asset_method": method.model_dump()}, tests=item["validation"].get("tests") or [],
            checksum=item["checksum"], created_by=user.id, status="active")
        db.add(version); db.flush(); definition.current_version_id = version.id
    try:
        inputs = {key: str(numeric_value(row.value.get("number", row.value.get("value")))) for key, row in selected.items()}
    except ValueError as exc:
        raise HTTPException(422, "当前输入不是有效数值，请检查事实口径") from exc
    current_output = facts.get(method.output.key)
    prior = db.get(ComputationRun, current_output.source_id) if current_output and current_output.source_type == "computation" else None
    signature = content_hash({"definition": version.checksum, "inputs": inputs, "parameters": {},
        "dependencies": {key: row.fact_key for key, row in selected.items()},
        "input_fact_ids": [row.id for row in selected.values()]})
    if (prior and prior.tenant_id == user.tenant_id and prior.project_id == project.id
            and prior.definition_version_id == version.id and prior.checksum == signature
            and prior.status == "succeeded" and (current_output.unit or "") == method.output.unit):
        return {"run": serialize_row(prior), "fact": serialize_row(current_output), "reused": True}
    run, output = _execute_computation_run(db, project=project, user=user, definition_version=version,
        inputs=inputs, parameters={}, rounding={}, input_facts=selected,
        output_fact={"fact_key": method.output.key, "label": method.output.name, "unit": method.output.unit})
    return {"run": serialize_row(run), "fact": serialize_row(output) if output else None, "reused": False}


@router.post("/documents/{document_id}/compute/{asset_id}")
def compute(document_id: str, asset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from apps.api.writing import _document
    document, project = _document(db, document_id, user, "editor")
    if document.status == "published": raise HTTPException(409, "定稿后请创建新草稿再计算")
    item = next((item for item in _adopted_assets(db, document, project, user) if item["id"] == asset_id), None)
    if not item: raise HTTPException(409, "本文未采用经过测试的计算方法")
    result = _compute_asset(db, project, user, item)
    _commit(db)
    return result


@router.post("/documents/{document_id}/compute")
def compute_all(document_id: str, payload: ComputeAll, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Run only reviewed formula methods, in dependency order, in one transaction.

    Extracted tasks, skills or code are never executed. Failed chains commit no
    partial results. Existing prose is not modified by this operation.
    """
    from apps.api.writing import _document
    document, project = _document(db, document_id, user, "editor")
    if document.status == "published": raise HTTPException(409, "定稿后请创建新草稿再计算")
    bundle = (document.applicability or {}).get("asset_bundle") or {}
    if (payload.base_version_id != document.current_version_id or payload.release_id != bundle.get("release_id")
            or payload.checksum != bundle.get("checksum")):
        raise HTTPException(409, "文章或计算定义已变化，请刷新后重新计算")
    assets = _adopted_assets(db, document, project, user)
    by_key = {item["definition"]["key"]: item for item in assets}
    ordered, visiting, visited = [], set(), set()

    def visit(key: str):
        if key in visited: return
        if key in visiting: raise HTTPException(422, "计算依赖存在循环")
        if key not in by_key: raise HTTPException(422, "计算缺少前置定义")
        visiting.add(key)
        item = by_key[key]
        for dependency in item["definition"].get("dependencies") or []:
            visit(dependency)
        visiting.remove(key); visited.add(key)
        if item["definition"]["kind"] == "computation_method": ordered.append(item)

    for key in sorted(by_key): visit(key)
    if not ordered: raise HTTPException(409, "本文没有可执行的计算方法")
    try:
        results = []
        for item in ordered:
            result = _compute_asset(db, project, user, item)
            results.append({"asset_id": item["id"], "title": item["definition"]["title"], **result})
            db.flush()
        audit(db, user.tenant_id, user.id, "writing_asset.compute_chain", "writing_document", document.id,
              {"release_id": payload.release_id, "run_ids": [item["run"]["id"] for item in results]})
        _commit(db)
    except Exception:
        db.rollback()
        raise
    return {"items": results, "document_unchanged": True}
