from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.semantica_adapter.writing_assets import SCHEMA_VERSION, asset_prompt, extract_assets
from packages.semantica_adapter.writing_extract import WritingEvidenceInput
from packages.semantica_adapter.upstream import check_writing_ontology
from .models import Document, DocumentVersion, WritingAssetVersion, WritingAssetRelease, WritingExtractionRun, WritingEvidence
from .writing_assets_schema import AssetCandidate, EXECUTABLE_KINDS
from .writing_graph import ensure_writing_evidence, evidence_batches, content_hash, utcnow


def validate_asset(candidate: AssetCandidate) -> dict[str, Any]:
    result: dict[str, Any] = {"schema": "passed", "evidence": "passed", "business": "requires_review", "execution": "not_required"}
    if candidate.kind in EXECUTABLE_KINDS:
        result["execution"] = "not_implemented"
    elif candidate.kind == "computation_method":
        result["execution"] = "requires_tests"
        result["unit_check"] = "requires_review"
    elif candidate.kind == "ontology":
        result["ontology"] = check_writing_ontology({
            "classes": [{"name": name} for name in candidate.payload["classes"]],
            "properties": candidate.payload["properties"],
        })
    return result


def process_writing_assets(db: Session, *, document: Document, version: DocumentVersion,
                          model_config_id: str, api_key: str, model: str, base_url: str | None,
                          material_role: str, request_parameters: dict | None = None,
                          actor_id: str | None = None, generator: Callable | None = None,
                          on_progress: Callable[[int, int], None] | None = None) -> dict:
    evidence = ensure_writing_evidence(db, document=document, version=version, actor_id=actor_id)
    if not evidence:
        raise ValueError("资料没有可读取的解析文本，不能完成写作资产抽取")
    count, completed = 0, 0
    for batch in evidence_batches(evidence, target_chars=3500, max_items=12):
        inputs = [WritingEvidenceInput(evidence_id=item.id, text=item.text, locator=item.locator) for item in batch]
        prompt = asset_prompt(inputs, material_role)
        # The prompt, schema, selected model and source all participate in cache identity.
        key = content_hash({"prompt": prompt, "model": model, "model_config_id": model_config_id,
                            "parameters": request_parameters or {}, "source": [(item.id, item.content_hash) for item in batch]})
        run = db.scalar(select(WritingExtractionRun).where(
            WritingExtractionRun.document_version_id == version.id,
            WritingExtractionRun.strategy_version == SCHEMA_VERSION,
            WritingExtractionRun.batch_key == key,
            WritingExtractionRun.deleted_at.is_(None),
        ))
        if run and run.status == "succeeded":
            count += int((run.metrics or {}).get("asset_count", 0)); completed += len(batch)
            if on_progress: on_progress(completed, len(evidence))
            continue
        if run is None:
            run = WritingExtractionRun(tenant_id=document.tenant_id, space_id=document.space_id,
                document_version_id=version.id, model_config_id=model_config_id,
                strategy_version=SCHEMA_VERSION, prompt_schema_version=SCHEMA_VERSION,
                batch_key=key, evidence_ids=[item.id for item in batch])
            db.add(run); db.flush()
        run.status = "running"; run.started_at = utcnow()
        run.metrics = {"stage": "reusable_assets", "prompt": prompt,
                       "input_evidence_ids": [item.id for item in batch], "material_role": material_role,
                       "coverage": "pending"}
        run.error_code = None; run.error_message = None
        db.flush()
        attempts = []
        try:
            extracted, executed_prompt = extract_assets(inputs, material_role=material_role, api_key=api_key,
                model=model, base_url=base_url, request_parameters=request_parameters, generator=generator,
                on_attempt=attempts.append)
            with db.begin_nested():
                for asset in extracted.assets:
                    definition = asset.model_dump()
                    checksum = content_hash(definition)
                    # Source-version namespacing prevents unrelated reports sharing a model key.
                    asset_key = content_hash({"document": document.id, "kind": asset.kind, "key": asset.key})
                    previous = db.scalar(select(WritingAssetVersion).where(
                        WritingAssetVersion.tenant_id == document.tenant_id,
                        WritingAssetVersion.space_id == document.space_id,
                        WritingAssetVersion.asset_key == asset_key,
                    ).order_by(WritingAssetVersion.version.desc()).limit(1))
                    if previous and previous.checksum == checksum: continue
                    db.add(WritingAssetVersion(tenant_id=document.tenant_id, space_id=document.space_id,
                        extraction_run_id=run.id, asset_key=asset_key, kind=asset.kind,
                        version=(previous.version + 1) if previous else 1, title=asset.title,
                        definition=definition, checksum=checksum, status="candidate",
                        validation=validate_asset(asset)))
                db.flush()
            run.metrics = {**run.metrics, "prompt": executed_prompt, "attempts": attempts, "output": extracted.model_dump(), "coverage": "complete",
                           "asset_count": len(extracted.assets), "processed_evidence_count": len(batch)}
            run.status = "succeeded"; run.finished_at = utcnow()
            count += len(extracted.assets); completed += len(batch)
            if on_progress: on_progress(completed, len(evidence))
        except Exception:
            run.metrics = {**run.metrics, "attempts": attempts, "coverage": "failed"}
            run.status = "failed"; run.finished_at = utcnow()
            run.error_code = "ASSET_EXTRACTION_INCOMPLETE"
            run.error_message = "资产抽取未通过结构、来源或覆盖校验；已完成批次保留，可重试。"
            db.flush()
            raise
    return {"asset_count": count, "processed_evidence_count": completed,
            "total_evidence_count": len(evidence), "coverage": "complete" if completed == len(evidence) else "partial"}


def publish_assets(db: Session, tenant_id: str, space_id: str, actor_id: str,
                   selected_ids: list[str]) -> WritingAssetRelease:
    rows = list(db.scalars(select(WritingAssetVersion).where(
        WritingAssetVersion.tenant_id == tenant_id, WritingAssetVersion.space_id == space_id,
        WritingAssetVersion.id.in_(selected_ids), WritingAssetVersion.deleted_at.is_(None),
    )))
    if not selected_ids or len(selected_ids) != len(set(selected_ids)) or len(rows) != len(selected_ids):
        raise ValueError("请选择当前空间真实存在的资产版本")
    if any(row.status != "reviewed" for row in rows):
        raise ValueError("资产必须经过人工确认才能发布")
    if len({row.asset_key for row in rows}) != len(rows):
        raise ValueError("同一资产不能同时发布多个版本")
    keys = {row.definition["key"] for row in rows}
    if len(keys) != len(rows):
        raise ValueError("不同来源中存在同名资产，请先修订语义键或只选择一个来源")
    if any(set(row.definition.get("dependencies") or []) - keys for row in rows):
        raise ValueError("资产发布缺少必需依赖")
    graph = {row.definition["key"]: row.definition.get("dependencies") or [] for row in rows}
    visiting, visited = set(), set()
    def visit(key):
        if key in visiting: raise ValueError("资产依赖存在循环")
        if key in visited: return
        visiting.add(key)
        for dependency in graph[key]: visit(dependency)
        visiting.remove(key); visited.add(key)
    for key in graph: visit(key)
    # A shared input key must keep its type and unit across forms and methods.
    # Two producers for one output would make recomputation ambiguous.
    fields: dict[str, tuple[str, str]] = {}
    producers: dict[str, str] = {}
    methods = []
    for row in rows:
        payload = row.definition["payload"]
        variables = []
        if row.kind == "variable": variables = [payload]
        elif row.kind == "report_form": variables = payload["fields"]
        elif row.kind == "computation_method":
            methods.append(row)
            variables = [*payload["inputs"], payload["output"]]
            output_key = payload["output"]["key"]
            if output_key in producers:
                raise ValueError("同一指标存在多个计算方法，请明确唯一的计算口径")
            producers[output_key] = row.definition["key"]
        for variable in variables:
            signature = (variable["value_type"], variable.get("unit", ""))
            if variable["key"] in fields and fields[variable["key"]] != signature:
                raise ValueError(f"指标 {variable['name']} 的数据类型或单位在资产中不一致")
            fields[variable["key"]] = signature
    for row in methods:
        required_methods = {producers[field["key"]] for field in row.definition["payload"]["inputs"]
                            if field["key"] in producers}
        if required_methods - set(row.definition.get("dependencies") or []):
            raise ValueError("计算方法未声明所消费指标的前置方法，请修订依赖")
    evidence_ids = {source for row in rows for source in row.definition["evidence_ids"]}
    available = set(db.scalars(select(WritingEvidence.id).where(
        WritingEvidence.tenant_id == tenant_id, WritingEvidence.space_id == space_id,
        WritingEvidence.id.in_(evidence_ids), WritingEvidence.deleted_at.is_(None),
    )))
    if available != evidence_ids: raise ValueError("资产引用的来源已删除或不属于当前知识空间")
    snapshot = {"schema_version": SCHEMA_VERSION, "assets": [
        {"id": row.id, "version": row.version, "checksum": row.checksum,
         "definition": row.definition, "validation": row.validation} for row in sorted(rows, key=lambda item: item.id)]}
    checksum = content_hash(snapshot)
    existing = db.scalar(select(WritingAssetRelease).where(
        WritingAssetRelease.tenant_id == tenant_id, WritingAssetRelease.space_id == space_id,
        WritingAssetRelease.checksum == checksum, WritingAssetRelease.deleted_at.is_(None),
    ))
    if existing: return existing
    number = int(db.scalar(select(func.max(WritingAssetRelease.release_number)).where(WritingAssetRelease.space_id == space_id)) or 0) + 1
    release = WritingAssetRelease(tenant_id=tenant_id, space_id=space_id, release_number=number,
        snapshot=snapshot, checksum=checksum, published_by=actor_id)
    db.add(release); db.flush()
    return release
