"""Controlled-writing orchestration APIs.

These endpoints expose deterministic corpus projection, inheritance alignment,
editor semantic diff and rollback.  They deliberately reuse the main writing
router's permission and version helpers rather than creating a parallel
writing subsystem.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import get_current_user
from apps.api.utils import serialize_row
from apps.api.writing_schemas import (
    WritingChangeSetApply,
    WritingChangeSetPreview,
    WritingCorpusPackageCreate,
    WritingInheritanceApply,
    WritingInheritancePreview,
)
from packages.platform.audit import audit
from packages.platform.controlled_writing import (
    PropagationEdge,
    align_inheritance,
    classify_edit_operations,
    corpus_artifact_manifest,
    propagation_closure,
    skeletonize_untrusted_sample,
    stable_checksum,
)
from packages.platform.database import get_db
from packages.platform.models import (
    Chunk,
    ComputationRun,
    DocumentVersion,
    ProjectFact,
    ScenarioPackageVersion,
    User,
    WritingBlockBinding,
    WritingChangeSet,
    WritingChunk,
    WritingChunkDependency,
    WritingCorpusPackage,
    WritingCorpusPackageVersion,
    WritingDocument,
    WritingDocumentVersion,
    WritingGraphRelease,
    WritingGraphReleaseItem,
    WritingInheritanceAlignment,
    WritingInputChange,
    WritingProjectMaterial,
    WritingPropagationRun,
)
from packages.platform.writing import content_hash
from packages.platform.writing_chunks import sync_writing_version_chunks
from packages.platform.writing_sample_profile import extract_sample_profile


router = APIRouter()


def _active(model: type) -> Any:
    return model.deleted_at.is_(None)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _material_text(db: Session, version_id: str, *, limit: int = 200_000) -> str:
    parts: list[str] = []
    size = 0
    for chunk in db.scalars(select(Chunk).where(
        Chunk.version_id == version_id,
        _active(Chunk),
    ).order_by(Chunk.ordinal)):
        value = str(chunk.text or "")
        if not value:
            continue
        remaining = limit - size
        if remaining <= 0:
            break
        parts.append(value[:remaining])
        size += len(parts[-1])
    return "\n".join(parts)


def _current_fact_snapshot(db: Session, project_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "fact_key": row.fact_key,
            "label": row.label,
            "value": row.value,
            "unit": row.unit,
            "version": row.version,
            "verification_status": row.verification_status,
        }
        for row in db.scalars(select(ProjectFact).where(
            ProjectFact.project_id == project_id,
            ProjectFact.active.is_(True),
            _active(ProjectFact),
        ).order_by(ProjectFact.fact_key))
    ]


@router.post("/projects/{project_id}/corpus-packages")
def create_corpus_package(
    project_id: str,
    payload: WritingCorpusPackageCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Project pinned materials into a versioned, value-free corpus package."""
    from apps.api.writing import _project

    project = _project(db, project_id, user, "editor")
    materials = list(db.scalars(select(WritingProjectMaterial).where(
        WritingProjectMaterial.project_id == project.id,
        WritingProjectMaterial.id.in_(payload.source_material_ids),
        WritingProjectMaterial.status == "active",
        _active(WritingProjectMaterial),
    )))
    if len(materials) != len(payload.source_material_ids):
        raise HTTPException(status_code=422, detail="语料包包含不存在、未授权或已移除的项目资料")
    graph_release_id = payload.writing_graph_release_id or project.writing_graph_release_id
    if graph_release_id:
        release = db.get(WritingGraphRelease, graph_release_id)
        if release is None or release.tenant_id != user.tenant_id or release.deleted_at is not None:
            raise HTTPException(status_code=404, detail="写作图谱版本不存在")
        if project.knowledge_space_id and release.space_id != project.knowledge_space_id:
            raise HTTPException(status_code=409, detail="写作图谱版本不属于项目知识空间")

    package = WritingCorpusPackage(
        tenant_id=user.tenant_id,
        project_id=project.id,
        space_id=project.knowledge_space_id,
        code=payload.code,
        name=payload.name,
        status="building",
        created_by=user.id,
    )
    db.add(package)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同编码语料包已经存在") from exc

    outlines: list[dict[str, Any]] = []
    skeletons: list[dict[str, Any]] = []
    source_version_ids: list[str] = []
    style_profiles: list[dict[str, Any]] = []
    for material in materials:
        source_version_ids.append(material.version_id)
        text = _material_text(db, material.version_id)
        if not text.strip():
            raise HTTPException(status_code=409, detail="选中资料尚未形成可读取的真实 Chunk")
        # A corpus package never exposes sample/history numeric literals to the
        # writing agent.  Every stored skeleton is therefore value-free even
        # when the five-layer graph has not yet classified the metric.
        skeleton = skeletonize_untrusted_sample(text, source_id=material.version_id)
        skeletons.append({
            "source_material_id": material.id,
            "source_version_id": material.version_id,
            "material_role": material.material_role,
            **skeleton,
        })
        if material.material_role == "sample_style":
            version = db.get(DocumentVersion, material.version_id)
            if version is None:
                raise HTTPException(status_code=409, detail="样稿版本不存在")
            try:
                profile = extract_sample_profile(
                    text,
                    version_id=material.version_id,
                    source_sha256=str(getattr(version, "sha256", "") or stable_checksum(text)),
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"样稿结构提取失败：{exc}") from exc
            outlines.extend(profile.get("chapters") or [])
            style_profiles.append(profile.get("style") or {})
    manifest = corpus_artifact_manifest(
        package_id=package.id,
        version=1,
        source_ids=source_version_ids,
        outline=outlines,
        skeletons=skeletons,
        graph_release_id=graph_release_id,
        created_at=_utc_iso(),
    )
    version = WritingCorpusPackageVersion(
        tenant_id=user.tenant_id,
        package_id=package.id,
        version=1,
        source_document_version_ids=source_version_ids,
        writing_graph_release_id=graph_release_id,
        manifest=manifest,
        outline=outlines,
        skeletons=skeletons,
        style_profile={"sources": style_profiles, "sample_values_available_to_agent": False},
        artifact_mapping=manifest["artifact_mapping"],
        checksum=manifest["checksum"],
        status="ready",
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    package.current_version_id = version.id
    package.status = "ready"
    audit(db, user.tenant_id, user.id, "writing.corpus.create", "writing_corpus_package", package.id, {
        "version_id": version.id,
        "source_count": len(source_version_ids),
        "skeleton_count": len(skeletons),
        "contains_historical_values": False,
    })
    db.commit()
    return {**serialize_row(package), "current_version": serialize_row(version)}


@router.get("/corpus-packages/{package_id}")
def get_corpus_package(
    package_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from apps.api.writing import _project, _tenant_row

    package = _tenant_row(db, WritingCorpusPackage, package_id, user.tenant_id, "语料包")
    if package.project_id:
        _project(db, package.project_id, user)
    version = db.get(WritingCorpusPackageVersion, package.current_version_id) if package.current_version_id else None
    return {**serialize_row(package), "current_version": serialize_row(version) if version else None}


@router.get("/corpus-packages/{package_id}/artifacts")
def get_corpus_artifacts(
    package_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = get_corpus_package(package_id, user=user, db=db)
    version = payload.get("current_version") or {}
    return {
        "package_id": package_id,
        "version_id": version.get("id"),
        "manifest": version.get("manifest") or {},
        "artifact_mapping": version.get("artifact_mapping") or {},
        "outline": version.get("outline") or [],
        "skeletons": version.get("skeletons") or [],
        "style_profile": version.get("style_profile") or {},
    }


def _expected_inheritance_nodes(db: Session, corpus: WritingCorpusPackageVersion) -> list[dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    if corpus.writing_graph_release_id:
        for item in db.scalars(select(WritingGraphReleaseItem).where(
            WritingGraphReleaseItem.release_id == corpus.writing_graph_release_id,
            WritingGraphReleaseItem.object_type == "fact",
            _active(WritingGraphReleaseItem),
        )):
            snapshot = dict(item.snapshot or {})
            key = str(snapshot.get("fact_key") or snapshot.get("predicate") or item.object_id)
            expected[key] = {
                "key": key,
                "label": snapshot.get("label") or snapshot.get("predicate") or key,
                "unit": snapshot.get("unit"),
                "required": bool(snapshot.get("required", True)),
                "formula": snapshot.get("formula"),
                "dependencies": snapshot.get("dependencies") or [],
            }
    for skeleton in corpus.skeletons or []:
        for slot in skeleton.get("slots") or []:
            key = str(slot.get("key") or "")
            expected.setdefault(key, {
                "key": key, "label": slot.get("label") or key,
                "unit": slot.get("unit"), "required": True,
            })
    return list(expected.values())


@router.post("/projects/{project_id}/inheritance/preview")
def preview_inheritance(
    project_id: str,
    payload: WritingInheritancePreview,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from apps.api.writing import _project, _tenant_row

    project = _project(db, project_id, user, "editor")
    corpus = _tenant_row(
        db, WritingCorpusPackageVersion, payload.corpus_package_version_id,
        user.tenant_id, "语料包版本",
    )
    package = _tenant_row(db, WritingCorpusPackage, corpus.package_id, user.tenant_id, "语料包")
    if package.project_id and package.project_id != project.id:
        raise HTTPException(status_code=403, detail="语料包不属于当前项目")
    facts = _current_fact_snapshot(db, project.id)
    result = align_inheritance(_expected_inheritance_nodes(db, corpus), facts)
    scenario = db.get(ScenarioPackageVersion, project.scenario_package_version_id)
    current_outline = [
        {"key": item.get("key"), "title": item.get("title")}
        for item in ((scenario.chapter_template if scenario else {}) or {}).get("chapters", [])
    ]
    source_outline = [
        {"key": item.get("key"), "title": item.get("title")}
        for item in corpus.outline or []
    ]
    outline_diff = {
        "source": source_outline,
        "current": current_outline,
        "source_only": [item for item in source_outline if item.get("title") not in {row.get("title") for row in current_outline}],
        "current_only": [item for item in current_outline if item.get("title") not in {row.get("title") for row in source_outline}],
    }
    fingerprint_payload = {
        "corpus_checksum": corpus.checksum,
        "facts": [{"id": item["id"], "version": item["version"], "value": item["value"]} for item in facts],
        "outline": current_outline,
    }
    row = WritingInheritanceAlignment(
        tenant_id=user.tenant_id,
        project_id=project.id,
        corpus_package_version_id=corpus.id,
        requested_by=user.id,
        status="preview",
        alignment=result["alignments"],
        todo=result["todo"],
        outline_diff=outline_diff,
        blocking_issues=[item for item in result["todo"] if item["status"] == "blocking"],
        fingerprint=stable_checksum(fingerprint_payload),
    )
    db.add(row)
    db.flush()
    audit(db, user.tenant_id, user.id, "writing.inheritance.preview", "writing_inheritance_alignment", row.id, {
        "ready_count": result["ready_count"], "blocking_count": result["blocking_count"],
        "historical_values_inherited": 0,
    })
    db.commit()
    return serialize_row(row)


@router.get("/projects/{project_id}/inheritance/{alignment_id}")
def get_inheritance(
    project_id: str,
    alignment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from apps.api.writing import _project, _tenant_row

    _project(db, project_id, user)
    row = _tenant_row(db, WritingInheritanceAlignment, alignment_id, user.tenant_id, "继承对齐")
    if row.project_id != project_id:
        raise HTTPException(status_code=404, detail="继承对齐不存在")
    return serialize_row(row)


@router.post("/projects/{project_id}/inheritance/apply")
def apply_inheritance(
    project_id: str,
    payload: WritingInheritanceApply,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from apps.api.writing import _project, _tenant_row

    project = _project(db, project_id, user, "editor")
    row = _tenant_row(db, WritingInheritanceAlignment, payload.alignment_id, user.tenant_id, "继承对齐")
    if row.project_id != project.id or row.status != "preview":
        raise HTTPException(status_code=409, detail="继承对齐不存在、已失效或已经应用")
    corpus = db.get(WritingCorpusPackageVersion, row.corpus_package_version_id)
    facts = _current_fact_snapshot(db, project.id)
    scenario = db.get(ScenarioPackageVersion, project.scenario_package_version_id)
    current_outline = [
        {"key": item.get("key"), "title": item.get("title")}
        for item in ((scenario.chapter_template if scenario else {}) or {}).get("chapters", [])
    ]
    current_fingerprint = stable_checksum({
        "corpus_checksum": corpus.checksum if corpus else None,
        "facts": [{"id": item["id"], "version": item["version"], "value": item["value"]} for item in facts],
        "outline": current_outline,
    })
    if current_fingerprint != row.fingerprint:
        row.status = "superseded"
        db.commit()
        raise HTTPException(status_code=409, detail="项目事实或目录已变化，请重新执行继承对齐")
    by_key = {item["node_key"]: item for item in row.alignment or []}
    unknown = set(payload.accepted_node_keys) - set(by_key)
    if unknown:
        raise HTTPException(status_code=422, detail="包含不属于本次对齐的节点")
    invalid = [key for key in payload.accepted_node_keys if by_key[key]["status"] not in {"ready", "prune"}]
    if invalid:
        raise HTTPException(status_code=409, detail="缺值或单位待确认的节点不能直接采用")
    decisions = [by_key[key] for key in payload.accepted_node_keys]
    project.config = {
        **(project.config or {}),
        "inheritance_alignment_id": row.id,
        "inheritance_decisions": decisions,
        "inheritance_corpus_version_id": row.corpus_package_version_id,
    }
    row.status = "applied"
    row.applied_by = user.id
    row.applied_at = datetime.now(timezone.utc)
    audit(db, user.tenant_id, user.id, "writing.inheritance.apply", "writing_inheritance_alignment", row.id, {
        "accepted_node_keys": payload.accepted_node_keys,
        "historical_values_inherited": 0,
    })
    db.commit()
    return serialize_row(row)


def _binding_map(db: Session, document: WritingDocument) -> tuple[dict[str, list[dict[str, Any]]], list[PropagationEdge]]:
    version_id = document.current_version_id
    chunks = list(db.scalars(select(WritingChunk).where(
        WritingChunk.document_version_id == version_id,
        _active(WritingChunk),
    )))
    by_chunk_row = {row.id: row for row in chunks}
    by_block: dict[str, list[dict[str, Any]]] = {row.chunk_id: [] for row in chunks}
    edges: list[PropagationEdge] = []
    dependencies = list(db.scalars(select(WritingChunkDependency).where(
        WritingChunkDependency.writing_chunk_id.in_(list(by_chunk_row)) if by_chunk_row else False,
        _active(WritingChunkDependency),
    ))) if by_chunk_row else []
    for dependency in dependencies:
        chunk = by_chunk_row[dependency.writing_chunk_id]
        source = f"{dependency.binding_type}:{dependency.binding_id}"
        target = f"chunk:{chunk.chunk_id}"
        item = {
            "binding_type": dependency.binding_type,
            "binding_id": dependency.binding_id,
            "binding_version": dependency.binding_version,
            "freshness_status": dependency.freshness_status,
        }
        by_block.setdefault(chunk.chunk_id, []).append(item)
        relation = "RESTATES" if dependency.binding_type in {
            "project_fact", "computation_run", "inferred_fact", "writing_fact",
        } else "REFERENCES"
        edges.append(PropagationEdge(source, target, relation, {
            "certainty": "definite", "block_id": chunk.chunk_id,
            "binding_type": dependency.binding_type,
        }))
    project_id = document.project_id
    for run in db.scalars(select(ComputationRun).where(
        ComputationRun.project_id == project_id,
        ComputationRun.status == "succeeded",
        _active(ComputationRun),
    )):
        for fact_id in run.input_fact_ids or []:
            edges.append(PropagationEdge(
                f"project_fact:{fact_id}", f"computation_run:{run.id}", "DERIVES_FROM",
                {"certainty": "definite", "formula_run_id": run.id},
            ))
    return by_block, edges


@router.post("/documents/{document_id}/changesets/preview")
def preview_editor_changeset(
    document_id: str,
    payload: WritingChangeSetPreview,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from apps.api.writing import _document

    document, project = _document(db, document_id, user, "editor")
    if not document.current_version_id:
        raise HTTPException(status_code=409, detail="文稿还没有可比较的版本")
    bindings, edges = _binding_map(db, document)
    operations = [item.model_dump() for item in payload.operations]
    try:
        verdicts = classify_edit_operations(operations, bindings)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    roots: set[str] = set()
    for verdict in verdicts:
        if not verdict["propagation_required"]:
            continue
        for binding in bindings.get(verdict["block_id"], []):
            roots.add(f"{binding['binding_type']}:{binding['binding_id']}")
    closure = propagation_closure(roots, edges) if roots else {
        "roots": [], "impacts": [], "direct_count": 0, "indirect_count": 0,
        "cycles": [], "truncated": False,
    }
    fingerprint = stable_checksum({
        "document_version_id": document.current_version_id,
        "operations": operations,
        "binding_hash": stable_checksum(bindings),
    })
    row = WritingChangeSet(
        tenant_id=user.tenant_id,
        project_id=project.id,
        document_id=document.id,
        base_document_version_id=document.current_version_id,
        requested_by=user.id,
        status="preview",
        operations=operations,
        semantic_verdicts=verdicts,
        propagation=closure,
        fingerprint=fingerprint,
    )
    db.add(row)
    db.flush()
    propagation = WritingPropagationRun(
        tenant_id=user.tenant_id,
        project_id=project.id,
        source_type="editor_changeset",
        source_id=row.id,
        root_node_ids=closure.get("roots") or [],
        graph_snapshot={
            "document_version_id": document.current_version_id,
            "edge_count": len(edges),
            "binding_checksum": stable_checksum(bindings),
        },
        result=closure,
        checksum=stable_checksum(closure),
        created_by=user.id,
    )
    db.add(propagation)
    audit(db, user.tenant_id, user.id, "writing.changeset.preview", "writing_change_set", row.id, {
        "operation_count": len(operations), "impact_count": len(closure.get("impacts") or []),
    })
    db.commit()
    return serialize_row(row)


def _set_node_text(node: dict[str, Any], text: str) -> dict[str, Any]:
    updated = deepcopy(node)
    leaves: list[dict[str, Any]] = []

    def walk(item: dict[str, Any]) -> None:
        if isinstance(item.get("text"), str):
            leaves.append(item)
        for child in item.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    walk(updated)
    if not leaves:
        updated["children"] = [{"text": text}]
    else:
        leaves[0]["text"] = text
        for leaf in leaves[1:]:
            leaf["text"] = ""
    return updated


@router.get("/documents/{document_id}/changesets/{changeset_id}")
def get_editor_changeset(
    document_id: str,
    changeset_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from apps.api.writing import _document, _tenant_row

    _document(db, document_id, user)
    row = _tenant_row(db, WritingChangeSet, changeset_id, user.tenant_id, "编辑差异")
    if row.document_id != document_id:
        raise HTTPException(status_code=404, detail="编辑差异不存在")
    return serialize_row(row)


@router.post("/documents/{document_id}/changesets/{changeset_id}/apply")
def apply_editor_changeset(
    document_id: str,
    changeset_id: str,
    payload: WritingChangeSetApply,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Apply selected prose/structure edits; controlled values use input-change API."""
    from apps.api.writing import _document, _tenant_row

    document, project = _document(db, document_id, user, "editor")
    row = _tenant_row(db, WritingChangeSet, changeset_id, user.tenant_id, "编辑差异")
    if row.document_id != document.id or row.status != "preview":
        raise HTTPException(status_code=409, detail="编辑差异不存在、已失效或已经应用")
    if document.status == "published":
        raise HTTPException(status_code=409, detail="定稿文稿不能直接修改，请先创建新草稿")
    if document.current_version_id != row.base_document_version_id:
        row.status = "superseded"
        db.commit()
        raise HTTPException(status_code=409, detail="文稿已在预览后变化，请重新分析编辑差异")
    accepted = set(payload.accepted_operation_indexes)
    if max(accepted, default=-1) >= len(row.operations or []):
        raise HTTPException(status_code=422, detail="选择了不存在的编辑操作")
    for index in accepted:
        verdict = (row.semantic_verdicts or [])[index]
        if verdict.get("confidence") == "blocking":
            raise HTTPException(status_code=409, detail="无权威绑定的精确数字不能进入正式正文")
        if verdict.get("propagation_required") and verdict.get("operation") == "MOD":
            raise HTTPException(status_code=409, detail="受控数值修改必须通过事实变更和影响预览执行")
    base = db.get(WritingDocumentVersion, row.base_document_version_id)
    content = deepcopy(base.content or [])

    def top_index(block_id: str) -> int | None:
        return next((index for index, node in enumerate(content) if str(node.get("id") or "") == block_id), None)

    for index, operation in enumerate(row.operations or []):
        if index not in accepted:
            continue
        kind, block_id = operation["operation"], operation["block_id"]
        position = top_index(block_id)
        if kind == "ADD":
            if position is not None:
                raise HTTPException(status_code=409, detail="新增正文块ID已经存在")
            new_node = {"id": block_id, "type": "p", "children": [{"text": operation.get("after") or ""}]}
            target = top_index(str(operation.get("to_section_id") or ""))
            content.insert(target + 1 if target is not None else len(content), new_node)
        elif position is None:
            raise HTTPException(status_code=409, detail="编辑目标已被删除或移动，请重新预览")
        elif kind == "DEL":
            content.pop(position)
        elif kind == "MOD":
            content[position] = _set_node_text(content[position], str(operation.get("after") or ""))
        elif kind == "MOVE":
            moved = content.pop(position)
            target = top_index(str(operation.get("to_section_id") or ""))
            content.insert(target + 1 if target is not None else len(content), moved)
    number = int(db.scalar(select(func.max(WritingDocumentVersion.version)).where(
        WritingDocumentVersion.document_id == document.id,
    )) or 0) + 1
    version = WritingDocumentVersion(
        tenant_id=user.tenant_id,
        document_id=document.id,
        version=number,
        content=content,
        content_hash=content_hash(content),
        scenario_package_version_id=document.scenario_package_version_id or project.scenario_package_version_id,
        knowledge_product_release_id=document.knowledge_product_release_id or project.knowledge_product_release_id,
        writing_graph_release_id=document.writing_graph_release_id or project.writing_graph_release_id,
        status="draft",
        change_summary="应用已确认的编辑差异",
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    sync_writing_version_chunks(
        db, project=project, document=document, version=version,
        legacy_bindings=db.scalars(select(WritingBlockBinding).where(
            WritingBlockBinding.document_id == document.id, _active(WritingBlockBinding),
        )),
    )
    document.current_version_id = version.id
    row.status = "applied"
    row.applied_document_version_id = version.id
    row.applied_by = user.id
    row.applied_at = datetime.now(timezone.utc)
    audit(db, user.tenant_id, user.id, "writing.changeset.apply", "writing_change_set", row.id, {
        "accepted_operation_indexes": sorted(accepted), "document_version_id": version.id,
    })
    db.commit()
    return {**serialize_row(row), "document_version": serialize_row(version)}


@router.post("/projects/{project_id}/input-changes/{preview_id}/rollback")
def rollback_input_change(
    project_id: str,
    preview_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reinstate prior facts and content as a *new* immutable document version."""
    from apps.api.writing import _document, _project, _tenant_row

    project = _project(db, project_id, user, "editor")
    preview = _tenant_row(db, WritingInputChange, preview_id, user.tenant_id, "事实变更")
    if preview.project_id != project.id or preview.status != "applied":
        raise HTTPException(status_code=409, detail="只有已应用且尚未撤销的事实变更可以回滚")
    document, _ = _document(db, preview.document_id, user, "editor")
    if document.current_version_id != preview.applied_document_version_id:
        raise HTTPException(status_code=409, detail="文稿在该变更后已有新版本，不能静默回滚")
    base_version_id = str((preview.impact or {}).get("document_version_id") or "")
    base = db.get(WritingDocumentVersion, base_version_id)
    if base is None:
        raise HTTPException(status_code=409, detail="变更前文稿版本不存在")
    replacement_ids = dict((preview.impact or {}).get("replacement_fact_ids") or {})
    if not replacement_ids:
        raise HTTPException(status_code=409, detail="该旧变更没有完整回滚元数据")
    for old_id, new_id in replacement_ids.items():
        old = db.get(ProjectFact, old_id)
        new = db.get(ProjectFact, new_id)
        if old is None or new is None or not new.active:
            raise HTTPException(status_code=409, detail="事实已在变更后再次修改，请通过新的影响预览处理")
        new.active = False
        new.freshness_status = "superseded"
        old.active = True
        old.freshness_status = "current"
    binding_snapshots = list((preview.impact or {}).get("binding_snapshots") or [])
    for snapshot in binding_snapshots:
        binding = db.get(WritingBlockBinding, snapshot.get("id"))
        if binding is None or binding.document_id != document.id:
            continue
        for field in (
            "source_type", "source_id", "source_version", "chunk_id", "fact_id",
            "inferred_fact_id", "query_run_id", "retrieval_query_run_id",
            "computation_run_id", "tool_run_id", "evidence_ids", "content_hash",
            "verification_status", "freshness_status", "metadata_json",
        ):
            if field in snapshot:
                setattr(binding, field, snapshot[field])
    number = int(db.scalar(select(func.max(WritingDocumentVersion.version)).where(
        WritingDocumentVersion.document_id == document.id,
    )) or 0) + 1
    version = WritingDocumentVersion(
        tenant_id=user.tenant_id,
        document_id=document.id,
        version=number,
        content=deepcopy(base.content or []),
        content_hash=content_hash(base.content or []),
        scenario_package_version_id=base.scenario_package_version_id,
        knowledge_product_release_id=base.knowledge_product_release_id,
        writing_graph_release_id=base.writing_graph_release_id,
        status="draft",
        change_summary="撤销事实变更并恢复先前投影",
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    sync_writing_version_chunks(
        db, project=project, document=document, version=version,
        legacy_bindings=db.scalars(select(WritingBlockBinding).where(
            WritingBlockBinding.document_id == document.id, _active(WritingBlockBinding),
        )),
    )
    document.current_version_id = version.id
    preview.status = "rolled_back"
    preview.rollback_document_version_id = version.id
    preview.rolled_back_by = user.id
    preview.rolled_back_at = datetime.now(timezone.utc)
    audit(db, user.tenant_id, user.id, "writing.input_change.rollback", "writing_input_change", preview.id, {
        "restored_document_version_id": base.id,
        "rollback_document_version_id": version.id,
    })
    db.commit()
    return {**serialize_row(preview), "document_version": serialize_row(version)}
