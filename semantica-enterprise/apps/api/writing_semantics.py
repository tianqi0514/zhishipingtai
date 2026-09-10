"""Small business endpoints for chapter evidence; reuse writing permissions."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict, Field

from apps.api.deps import get_current_user, has_space_permission
from packages.platform.database import get_db
from packages.platform.audit import audit
from packages.platform.models import (AnalysisRule, AnalysisRuleSet, AnalysisRuleVersion,
    Ontology, OntologyVersion, ScenarioPackageVersion, WritingReasoningRun,
    WritingBlockBinding, WritingDocument, WritingDocumentVersion, ProjectFact, ComputationRun)
from packages.platform.writing import content_hash, walk_plate_nodes
from packages.platform.writing_knowledge import (ENGINE, applied_packet, prepare_packet,
    project_fingerprint, resolve_requirement)

router = APIRouter()


class ParagraphReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_version_id: str
    reason: str = Field(min_length=4, max_length=1000)


@router.post("/documents/{document_id}/paragraphs/{block_id}/review")
def review_paragraph(document_id: str, block_id: str, payload: ParagraphReview, user=Depends(get_current_user), db=Depends(get_db)):
    """A human reviews narrative only; never certify or modify calculated values."""
    from apps.api.writing import _document, _release_scope, _latest_computation_rows
    document, project = _document(db, document_id, user, "editor")
    _release_scope(db, project, user)
    if document.status == "published" or document.current_version_id != payload.document_version_id:
        raise HTTPException(409, "文稿版本已变化或已发布，请重新查看")
    binding = db.scalar(select(WritingBlockBinding).where(WritingBlockBinding.document_id == document_id, WritingBlockBinding.block_id == block_id, WritingBlockBinding.deleted_at.is_(None)))
    version = db.get(WritingDocumentVersion, document.current_version_id)
    node = next((n for n in version.content if n.get("id") == block_id), None)
    if not node or not binding or binding.source_type != "model_extraction" or binding.freshness_status != "stale":
        raise HTTPException(409, "只能核对需要更新的普通段落，测算和推演须重新执行")
    metadata = binding.metadata_json or {}
    if metadata.get("knowledge_refs") and not applied_packet(db, project):
        raise HTTPException(409, "请先重新准备并确认章节依据")
    # Retain old references as a historical audit; do not silently certify a
    # removed premise just because the author confirmed wording.
    packet = applied_packet(db, project)
    if set(metadata.get("knowledge_refs") or []) - set((packet or {}).get("references") or {}):
        raise HTTPException(409, "本段引用的关系已失效，请重新生成本段并绑定有效依据")
    facts = list(db.scalars(select(ProjectFact).where(ProjectFact.project_id == project.id, ProjectFact.active.is_(True), ProjectFact.deleted_at.is_(None))))
    computations = _latest_computation_rows(db, project.id)
    new_metadata = {**metadata,
        "input_fact_ids": [f.id for f in facts if f.fact_key in (metadata.get("input_keys") or [])],
        "computation_run_ids": [r.id for r in computations if ((r.result or {}).get("output_fact") or {}).get("fact_key") in (metadata.get("metric_keys") or [])],
        "human_review": {"user_id": user.id, "at": datetime.now(timezone.utc).isoformat(), "reason": payload.reason, "document_version_id": version.id},
        "content_hash_algorithm": "canonical-json-v1"}
    audit(db, user.tenant_id, user.id, "writing.paragraph.review", "writing_block_binding", binding.id,
          {"previous_metadata": metadata, "review": new_metadata["human_review"]})
    binding.metadata_json = new_metadata
    binding.content_hash = content_hash(node)
    binding.freshness_status = "manual_override"
    binding.verification_status = "unverified"  # Human wording review != factual certification.
    db.commit()
    return {"status": "reviewed", "block_id": block_id}


@router.get("/knowledge-options")
def knowledge_options(user=Depends(get_current_user), db=Depends(get_db)):
    ontologies, rules = [], []
    for version in db.scalars(select(OntologyVersion).where(OntologyVersion.tenant_id == user.tenant_id, OntologyVersion.status == "published", OntologyVersion.deleted_at.is_(None)).order_by(OntologyVersion.version.desc())):
        ontology = db.get(Ontology, version.ontology_id)
        if not ontology or ontology.deleted_at is not None or (ontology.space_id and not has_space_permission(db, user, ontology.space_id, "read")):
            continue
        terms = [t for t in version.manifest.get("terms", []) if t.get("enabled", True)]
        ontologies.append({"id": version.id, "name": ontology.name, "version": version.version,
                           "entity_types": [t["label"] for t in terms if t["term_type"] == "class"],
                           "predicates": [t["label"] for t in terms if t["term_type"] == "relation"]})
    for rule in db.scalars(select(AnalysisRule).where(AnalysisRule.tenant_id == user.tenant_id, AnalysisRule.enabled.is_(True), AnalysisRule.deleted_at.is_(None))):
        rule_set = db.get(AnalysisRuleSet, rule.rule_set_id)
        if not rule_set or not rule_set.enabled or rule_set.deleted_at is not None or any(not has_space_permission(db, user, s, "read") for s in rule_set.space_ids or []):
            continue
        version = db.scalar(select(AnalysisRuleVersion).where(AnalysisRuleVersion.rule_id == rule.id).order_by(AnalysisRuleVersion.version.desc()))
        if version:
            rules.append({"id": version.id, "name": rule.name, "version": version.version})
    return {"ontologies": ontologies, "rules": rules}


def checked_project(db, project_id, user, role="viewer"):
    from apps.api.writing import _project, _release_scope
    project = _project(db, project_id, user, role)
    spaces, _ = _release_scope(db, project, user)
    return project, spaces


@router.get("/projects/{project_id}/chapter-evidence")
def chapter_evidence(project_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    project, spaces = checked_project(db, project_id, user)
    packet = applied_packet(db, project)
    scenario = db.get(ScenarioPackageVersion, project.scenario_package_version_id)
    try:
        for chapter in scenario.chapter_template.get("chapters", []):
            resolve_requirement(db, user.tenant_id, chapter.get("knowledge") or {}, spaces)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"applied": packet, "needs_preparation": packet is None,
            "configured": any(any((c.get("knowledge") or {}).get(k) for k in ("entity_types", "predicates", "rule_version_ids")) for c in scenario.chapter_template.get("chapters", []))}


@router.post("/projects/{project_id}/chapter-evidence/preview")
def preview_evidence(project_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    project, spaces = checked_project(db, project_id, user, "editor")
    try:
        result = prepare_packet(db, project, spaces)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    previous_run = db.get(WritingReasoningRun, (project.config or {}).get("writing_knowledge_run_id")) if (project.config or {}).get("writing_knowledge_run_id") else None
    previous = {**previous_run.result, "run_id": previous_run.id} if previous_run and previous_run.project_id == project.id else {}
    before, after = set(previous.get("references") or {}), set(result["references"])
    changed = before - after
    prior_sections = {s["key"]: set(s["references"]) for s in previous.get("sections") or []}
    changed_sections = {s["key"] for s in result["sections"] if prior_sections.get(s["key"], set()) != set(s["references"])}
    bindings = list(db.scalars(select(WritingBlockBinding).where(WritingBlockBinding.project_id == project.id, WritingBlockBinding.deleted_at.is_(None))))
    affected = [b.block_id for b in bindings if changed.intersection((b.metadata_json or {}).get("knowledge_refs") or []) or (b.metadata_json or {}).get("section_key") in changed_sections]
    result["comparison"] = {"new": len(after - before), "unchanged": len(before & after), "removed": len(changed), "affected_blocks": affected}
    result["previous_run_id"] = previous.get("run_id")
    row = WritingReasoningRun(tenant_id=user.tenant_id, project_id=project.id, status="completed", mode="preview", engine=ENGINE,
        engine_version="snapshot-v1", input_fact_ids=[], rule_manifest=[], result=result, proof={}, checksum=content_hash(result),
        started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc), created_by=user.id)
    db.add(row)
    db.flush()
    audit(db, user.tenant_id, user.id, "writing.chapter_evidence.preview", "writing_reasoning_run", row.id, {"project_id": project.id, "reference_count": len(result["references"])})
    db.commit()
    return {**result, "run_id": row.id}


@router.post("/projects/{project_id}/chapter-evidence/{run_id}/apply")
def apply_evidence(project_id: str, run_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    project, spaces = checked_project(db, project_id, user, "editor")
    row = db.get(WritingReasoningRun, run_id)
    if not row or row.tenant_id != user.tenant_id or row.project_id != project.id or row.engine != ENGINE:
        raise HTTPException(404, "章节依据不存在")
    if row.mode == "applied" and (project.config or {}).get("writing_knowledge_run_id") == row.id:
        return {**row.result, "run_id": row.id}
    if row.mode != "preview" or row.result["fingerprint"] != project_fingerprint(db, project):
        raise HTTPException(409, "资料或场景已变化，请重新预览章节依据")
    if row.result.get("previous_run_id") != (project.config or {}).get("writing_knowledge_run_id"):
        raise HTTPException(409, "其他用户已更新章节依据，请重新预览")
    # Revalidate access and rule availability at application time.
    scenario = db.get(ScenarioPackageVersion, project.scenario_package_version_id)
    try:
        for c in scenario.chapter_template.get("chapters", []):
            resolve_requirement(db, user.tenant_id, c.get("knowledge") or {}, spaces)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    affected = set(row.result["comparison"]["affected_blocks"])
    for binding in db.scalars(select(WritingBlockBinding).where(WritingBlockBinding.project_id == project.id, WritingBlockBinding.block_id.in_(affected))):
        binding.freshness_status = "stale"
    row.mode = "applied"
    project.config = {**(project.config or {}), "writing_knowledge_run_id": row.id}
    audit(db, user.tenant_id, user.id, "writing.chapter_evidence.apply", "writing_reasoning_run", row.id, {"affected_blocks": sorted(affected)})
    db.commit()
    return {**row.result, "run_id": row.id}


@router.get("/projects/{project_id}/chapter-evidence/source/{chunk_id}")
def evidence_source(project_id: str, chunk_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    from packages.platform.models import Chunk
    from packages.platform.curation import effective_chunk_payloads
    project, _ = checked_project(db, project_id, user)
    packet = applied_packet(db, project)
    sources = [s for r in (packet or {}).get("references", {}).values() for s in r["sources"] if s["chunk_id"] == chunk_id]
    if not sources:
        raise HTTPException(404, "来源不在当前任务已确认的章节依据中")
    chunk = db.get(Chunk, chunk_id)
    effective = effective_chunk_payloads(db, [chunk]) if chunk else []
    if not effective:
        raise HTTPException(404, "来源已撤回")
    return {**sources[0], "text": effective[0]["text"]}


@router.get("/documents/{document_id}/paragraph-evidence")
def paragraph_evidence(document_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    from apps.api.writing import _document, _release_scope
    document, project = _document(db, document_id, user)
    _release_scope(db, project, user)
    version = db.get(WritingDocumentVersion, document.current_version_id)
    bindings = {b.block_id: b for b in db.scalars(select(WritingBlockBinding).where(WritingBlockBinding.document_id == document.id, WritingBlockBinding.deleted_at.is_(None)))}
    result, title = [], "正文"
    for node in (version.content if version else []):
        text = "".join(str(n.get("text", "")) for n in walk_plate_nodes([node]))
        if node.get("type") in ("h1", "h2", "h3"):
            title = text
            continue
        evidence, statuses = [], []
        for child in walk_plate_nodes([node]):
            b = bindings.get(child.get("id"))
            if not b:
                continue
            metadata = b.metadata_json or {}
            statuses.append(b.freshness_status)
            evidence.append({"type": b.source_type, "status": b.freshness_status,
                             "verification": b.verification_status, "chunk_id": b.chunk_id,
                             "query_run_id": b.retrieval_query_run_id, "metadata": metadata,
                             "computation_run_id": b.computation_run_id})
        if text.strip() or evidence:
            result.append({"block_id": node.get("id"), "section": title, "text": text[:180],
                           "status": "stale" if "stale" in statuses else "current" if evidence else "unverified",
                           "evidence": evidence})
    return {"version_id": document.current_version_id, "paragraphs": result}
