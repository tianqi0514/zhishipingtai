from __future__ import annotations

from copy import deepcopy
import hashlib
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from apps.worker.celery_app import celery_app
from packages.platform.config import get_settings
from packages.platform.database import SessionLocal
from packages.platform.models import (
    AnalysisRuleSet,
    AutoDecisionRecord,
    CanonicalEntity,
    Chunk,
    ChunkPolicy,
    ConflictCase,
    ContentElement,
    CurationBatch,
    DataSourceSchemaVersion,
    Document,
    DocumentProfile,
    DocumentVersion,
    EntityMention,
    EventAssertion,
    ExtractionPolicy,
    ExtractionRun,
    Fact,
    GovernancePolicy,
    IndexRelease,
    InferenceRun,
    Job,
    JobStep,
    ModelConfig,
    ParserPolicy,
    RelationAssertion,
    SourceConnector,
)
from packages.platform.analysis import execute_inference_run
from packages.platform.curation import (
    constrained_canonical_entity,
    effective_elements,
    entity_pair_constraints,
    invalidate_inference_for_space,
    upsert_conflict_cases,
    upsert_profile_cases,
)
from packages.platform.graph_release import publish_graph_snapshot
from packages.platform.index_release import activate_knowledge_release, publish_index_snapshot
from packages.platform.security import decrypt_secret
from packages.platform.storage import object_storage
from packages.platform.structured_materialization import materialize_database_mapping
from packages.semantica_adapter import ingest_source, parse_document, track_elements
from packages.semantica_adapter.embedding import SemanticEmbedder
from packages.semantica_adapter.extract import extract_semantics
from packages.semantica_adapter.governance import govern_entities
from packages.semantica_adapter.indexing import SearchIndexer, search_point_id
from packages.semantica_adapter.normalize import normalize_and_split
from packages.semantica_adapter.profile import analyze_profile_with_model, build_deterministic_profile
from packages.semantica_adapter.transcription import transcribe_media
from packages.semantica_adapter.vision import describe_visual


def now() -> datetime:
    return datetime.now(timezone.utc)


def _step(db, job_id: str, name: str, sequence: int, status: str, detail: dict | None = None):
    row = db.scalar(select(JobStep).where(JobStep.job_id == job_id, JobStep.name == name))
    if row is None:
        row = JobStep(job_id=job_id, name=name, sequence=sequence)
        db.add(row)
    row.status = status
    row.detail = detail or {}
    if status == "running":
        row.started_at = now()
    if status in {"succeeded", "failed"}:
        row.finished_at = now()
    db.commit()
    return row


def _policy_dict(policy: ParserPolicy) -> dict[str, Any]:
    return {
        "parser_type": policy.parser_type,
        "enable_ocr": policy.enable_ocr,
        "ocr_language": policy.ocr_language,
        "extract_tables": policy.extract_tables,
        "extract_images": policy.extract_images,
        "max_pages": policy.max_pages,
        **(policy.config or {}),
    }


def _fail(db, job: Job, code: str, message: str) -> None:
    job.status = "failed"
    job.error_code = code
    job.error_message = message[:4000]
    job.finished_at = now()
    db.commit()


def _progress(db, job: Job, value: int) -> None:
    job.progress = max(0, min(100, value))
    db.commit()


def _queue_automatic_inference(
    db,
    *,
    tenant_id: str,
    space_id: str,
    document_id: str,
    version_id: str,
) -> list[str]:
    """Queue enabled rule sets after a new knowledge version is safely published."""
    rule_sets = list(
        db.scalars(
            select(AnalysisRuleSet).where(
                AnalysisRuleSet.tenant_id == tenant_id,
                AnalysisRuleSet.auto_run.is_(True),
                AnalysisRuleSet.enabled.is_(True),
                AnalysisRuleSet.deleted_at.is_(None),
            )
        )
    )
    run_ids: list[str] = []
    queued: list[tuple[str, str]] = []
    for rule_set in rule_sets:
        if space_id not in (rule_set.space_ids or []):
            continue
        idempotency_key = f"inference:auto:{rule_set.id}:{version_id}"
        existing_job = db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing_job is not None:
            existing_run_id = (existing_job.input or {}).get("inference_run_id")
            if existing_run_id:
                run_ids.append(existing_run_id)
            continue
        run = InferenceRun(
            tenant_id=tenant_id,
            rule_set_id=rule_set.id,
            trigger_type="document_update",
            mode="publish" if rule_set.auto_publish else "preview",
            space_ids=[space_id],
            run_input={
                "document_id": document_id,
                "version_id": version_id,
                "reason": "current_version_published",
            },
        )
        db.add(run)
        db.flush()
        inference_job = Job(
            tenant_id=tenant_id,
            job_type="knowledge_inference",
            idempotency_key=idempotency_key,
            input={"inference_run_id": run.id},
        )
        db.add(inference_job)
        db.flush()
        run_ids.append(run.id)
        queued.append((run.id, inference_job.id))
    db.commit()
    for run_id, inference_job_id in queued:
        inference_job = db.get(Job, inference_job_id)
        if inference_job is None or inference_job.status != "queued":
            continue
        try:
            run_inference_task.delay(inference_job_id)
        except Exception as exc:
            inference_job.status = "failed"
            inference_job.error_code = "INFERENCE_QUEUE_FAILED"
            inference_job.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            inference_job.finished_at = now()
            run = db.get(InferenceRun, run_id)
            if run:
                run.status = "failed"
                run.error_code = "INFERENCE_QUEUE_FAILED"
                run.error_message = inference_job.error_message
                run.finished_at = now()
            db.commit()
    return run_ids


@celery_app.task(bind=True, autoretry_for=(), name="documents.parse")
def parse_version_task(self, job_id: str) -> dict[str, Any]:
    settings = get_settings()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return {"status": "missing"}
        version = db.get(DocumentVersion, job.input.get("version_id"))
        if version is None:
            _fail(db, job, "VERSION_NOT_FOUND", "文档版本不存在")
            return {"status": "failed"}
        document = db.get(Document, version.document_id)
        policy = db.get(ParserPolicy, version.parser_policy_id) if version.parser_policy_id else None
        if policy is None:
            policy = db.scalar(
                select(ParserPolicy).where(
                    ParserPolicy.tenant_id == version.tenant_id,
                    ParserPolicy.is_default.is_(True),
                    ParserPolicy.deleted_at.is_(None),
                )
            )
        if policy is None:
            _fail(db, job, "PARSER_POLICY_MISSING", "未配置解析策略")
            return {"status": "failed"}

        version_id = version.id
        document_id = document.id if document else None
        job.status = "running"
        job.started_at = now()
        job.attempts += 1
        job.progress = 2
        version.status = "processing"
        if document:
            document.status = "processing"
        db.commit()

        suffix = Path(version.filename).suffix
        try:
            _step(db, job.id, "download", 1, "running")
            _progress(db, job, 5)
            with tempfile.TemporaryDirectory(prefix="semantica-job-") as temp_dir:
                local_path = Path(temp_dir) / f"source{suffix}"
                object_storage.get_file(version.object_key, local_path)
                _step(db, job.id, "download", 1, "succeeded", {"bytes": local_path.stat().st_size})
                _progress(db, job, 15)

                _step(db, job.id, "parse", 2, "running")
                _progress(db, job, 20)
                asr_model = db.scalar(
                    select(ModelConfig).where(
                        ModelConfig.tenant_id == version.tenant_id,
                        ModelConfig.model_kind == "asr",
                        ModelConfig.is_default.is_(True),
                        ModelConfig.enabled.is_(True),
                        ModelConfig.deleted_at.is_(None),
                    )
                )
                transcriber = None
                if asr_model:
                    asr_secret = decrypt_secret(asr_model.api_key_encrypted)

                    def transcriber(media_path: Path, media_type: str) -> dict[str, Any]:
                        _progress(db, job, 35)
                        config = asr_model.config or {}
                        result = transcribe_media(
                            media_path,
                            media_type,
                            api_key=asr_secret or "",
                            model=asr_model.model_name,
                            base_url=asr_model.base_url,
                            timeout=float(config.get("timeout", 300)),
                            max_retries=int(config.get("max_retries", config.get("retry", 2))),
                            language=config.get("language"),
                            prompt=config.get("prompt"),
                        )
                        _progress(db, job, 62)
                        return result
                vision_model = db.scalar(
                    select(ModelConfig).where(
                        ModelConfig.tenant_id == version.tenant_id,
                        ModelConfig.model_kind == "vision",
                        ModelConfig.is_default.is_(True),
                        ModelConfig.enabled.is_(True),
                        ModelConfig.deleted_at.is_(None),
                    )
                )
                visual_describer = None
                if vision_model:
                    vision_secret = decrypt_secret(vision_model.api_key_encrypted)

                    def visual_describer(visual_path: Path, media_type: str) -> dict[str, Any]:
                        _progress(db, job, 45)
                        config = vision_model.config or {}
                        result = describe_visual(
                            visual_path,
                            media_type,
                            api_key=vision_secret or "",
                            model=vision_model.model_name,
                            base_url=vision_model.base_url,
                            timeout=float(config.get("timeout", 120)),
                            max_retries=int(config.get("max_retries", config.get("retry", 2))),
                            prompt=config.get("prompt"),
                            max_tokens=int(config.get("max_tokens", 700)),
                            keyframe_count=int(config.get("keyframe_count", 3)),
                        )
                        _progress(db, job, 62)
                        return result
                parse_policy = _policy_dict(policy)
                if document.source_id:
                    source = db.get(SourceConnector, document.source_id)
                    schema_version = db.scalar(select(DataSourceSchemaVersion).where(
                        DataSourceSchemaVersion.source_id == document.source_id,
                        DataSourceSchemaVersion.status == "current",
                        DataSourceSchemaVersion.deleted_at.is_(None),
                    ).order_by(DataSourceSchemaVersion.version_number.desc()).limit(1))
                    if source is not None and source.source_type == "database":
                        parse_policy["database_context"] = {
                            "source_id": source.id,
                            "schema_version_id": schema_version.id if schema_version else None,
                            "schema_fingerprint": schema_version.schema_fingerprint if schema_version else None,
                            "default_schema": (
                                (schema_version.catalog or {}).get("default_schema") if schema_version
                                else (source.config or {}).get("schema")
                            ),
                            "sync_time": version.created_at.isoformat(),
                        }
                elements, summary = parse_document(
                    local_path,
                    # The document is the stable identity scope. Unchanged
                    # elements therefore retain their IDs across versions.
                    version_id=document.id,
                    policy=parse_policy,
                    supplied_mime=version.content_type,
                    media_transcriber=transcriber,
                    visual_describer=visual_describer,
                )
                _step(db, job.id, "parse", 2, "succeeded", summary)
                _progress(db, job, 70)

                _step(db, job.id, "persist", 3, "running")
                _progress(db, job, 75)
                db.execute(delete(ContentElement).where(ContentElement.version_id == version.id))
                for item in elements:
                    db.add(
                        ContentElement(
                            tenant_id=version.tenant_id,
                            space_id=document.space_id,
                            document_id=document.id,
                            version_id=version.id,
                            element_id=item.element_id,
                            element_type=item.element_type,
                            ordinal=item.ordinal,
                            text=item.text,
                            structural_path=item.structural_path,
                            page_number=item.page_number,
                            bbox=item.bbox,
                            element_metadata=item.metadata,
                            scope_tokens=[],
                        )
                    )
                version.parse_summary = summary
                version.status = "ready"
                version.error_code = None
                version.error_message = None
                document.current_version_id = version.id
                document.status = "ready"
                db.commit()
                _step(db, job.id, "persist", 3, "succeeded", {"elements": len(elements)})
                _progress(db, job, 85)

                _step(db, job.id, "provenance", 4, "running")
                _progress(db, job, 90)
                tracked = track_elements(
                    settings.provenance_storage_path,
                    version_id=version.id,
                    source=version.object_key,
                    elements=elements,
                )
                _step(db, job.id, "provenance", 4, "succeeded", {"tracked": tracked})
                _progress(db, job, 95)

            job.status = "succeeded"
            job.progress = 100
            job.result = {"version_id": version.id, **summary}
            job.finished_at = now()
            db.commit()
            if settings.knowledge_auto_process:
                process_job = db.scalar(select(Job).where(Job.idempotency_key == f"knowledge:{version.id}"))
                if process_job is None:
                    process_job = Job(
                        tenant_id=version.tenant_id,
                        job_type="process_knowledge",
                        idempotency_key=f"knowledge:{version.id}",
                        input={"version_id": version.id},
                    )
                    db.add(process_job)
                    db.commit()
                    try:
                        process_version_task.delay(process_job.id)
                    except Exception as dispatch_error:
                        # Parsing is already durable and valid.  Only the
                        # downstream knowledge-processing job failed to queue.
                        dispatch_message = f"{type(dispatch_error).__name__}: {dispatch_error}"
                        process_job.status = "failed"
                        process_job.error_code = "QUEUE_DISPATCH_FAILED"
                        process_job.error_message = dispatch_message[:4000]
                        process_job.finished_at = now()
                        db.commit()
                        job.result = {**(job.result or {}), "knowledge_dispatch_warning": True}
                        db.commit()
            return job.result
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            # Persist may fail inside a transaction (for example on malformed
            # PDF text). Roll back first, then reload durable rows so the task
            # itself can always record an actionable failure state.
            db.rollback()
            job = db.get(Job, job_id)
            version = db.get(DocumentVersion, version_id)
            document = db.get(Document, document_id) if document_id else None
            if job is None or version is None:
                return {"status": "failed", "error": message}
            version.status = "failed"
            version.error_code = "PARSE_FAILED"
            version.error_message = message[:4000]
            if document:
                document.status = "failed"
            running = db.scalar(
                select(JobStep).where(JobStep.job_id == job.id, JobStep.status == "running")
            )
            if running:
                running.status = "failed"
                running.detail = {"message": message[:1000]}
                running.finished_at = now()
            _fail(db, job, "PARSE_FAILED", message)
            return {"status": "failed", "error": message}


def _source_payload(source: SourceConnector) -> tuple[bytes, str, str, str]:
    result = ingest_source(
        source_type=source.source_type,
        source_name=source.name,
        config=source.config or {},
        secret=decrypt_secret(source.secret_encrypted),
    )
    return result.body, result.filename, result.content_type, result.title


def _update_source_cursor(source: SourceConnector, digest: str, status: str) -> None:
    config = source.config or {}
    schedule_minutes = int(config.get("schedule_minutes") or 0)
    cursor = dict(source.cursor or {})
    cursor.update(
        {
            "last_digest": digest,
            "last_status": status,
            "sync_count": int(cursor.get("sync_count") or 0) + 1,
        }
    )
    if schedule_minutes:
        cursor["next_sync_at"] = (now() + timedelta(minutes=schedule_minutes)).isoformat()
    source.cursor = cursor


@celery_app.task(bind=True, name="sources.sync")
def sync_source_task(self, job_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return {"status": "missing"}
        source_id = (job.input or {}).get("source_id")
        source = db.get(SourceConnector, source_id)
        if (
            source is None
            or source.deleted_at is not None
            or source.tenant_id != job.tenant_id
        ):
            _fail(db, job, "SOURCE_NOT_FOUND", "连接器不存在")
            return {"status": "failed"}
        if not source.enabled:
            _fail(db, job, "SOURCE_DISABLED", "连接器已停用")
            return {"status": "failed"}
        job.status = "running"
        job.started_at = now()
        job.attempts += 1
        job.progress = 5
        db.commit()

        uploaded_object_key: str | None = None
        object_persisted = False
        try:
            _step(db, job.id, "fetch", 1, "running")
            _progress(db, job, 10)
            payload, filename, content_type, title = _source_payload(source)
            digest = hashlib.sha256(payload).hexdigest()
            _step(db, job.id, "fetch", 1, "succeeded", {"bytes": len(payload), "sha256": digest})
            _progress(db, job, 40)

            _step(db, job.id, "persist", 2, "running")
            _progress(db, job, 50)

            document = db.scalar(
                select(Document).where(
                    Document.tenant_id == source.tenant_id,
                    Document.source_id == source.id,
                    Document.deleted_at.is_(None),
                )
            )
            if document is None:
                document = Document(
                    tenant_id=source.tenant_id,
                    space_id=source.space_id,
                    source_id=source.id,
                    title=title,
                    status="draft",
                )
                db.add(document)
                db.flush()
            existing = db.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.sha256 == digest,
                )
            )
            if existing:
                _step(
                    db,
                    job.id,
                    "persist",
                    2,
                    "succeeded",
                    {"version_id": existing.id, "unchanged": True},
                )
                source.last_sync_at = now()
                source.last_sync_status = "unchanged"
                _update_source_cursor(source, digest, "unchanged")
                job.status = "succeeded"
                job.progress = 100
                job.result = {"document_id": document.id, "version_id": existing.id, "unchanged": True}
                job.finished_at = now()
                db.commit()
                return job.result

            version_number = (
                db.scalar(
                    select(func.max(DocumentVersion.version_number)).where(
                        DocumentVersion.document_id == document.id
                    )
                )
                or 0
            ) + 1
            policy = db.scalar(
                select(ParserPolicy).where(
                    ParserPolicy.tenant_id == source.tenant_id,
                    ParserPolicy.is_default.is_(True),
                    ParserPolicy.deleted_at.is_(None),
                )
            )
            if policy is None:
                raise ValueError("未配置可用的默认解析策略")
            version = DocumentVersion(
                tenant_id=source.tenant_id,
                document_id=document.id,
                version_number=version_number,
                filename=filename,
                content_type=content_type,
                size=len(payload),
                sha256=digest,
                object_key=f"{source.tenant_id}/{source.space_id}/{document.id}/{digest}/{filename}",
                parser_policy_id=policy.id if policy else None,
            )
            db.add(version)
            db.flush()
            with tempfile.TemporaryDirectory(prefix="semantica-source-") as temp_dir:
                path = Path(temp_dir) / filename
                path.write_bytes(payload)
                object_storage.put_file(version.object_key, path, content_type)
                uploaded_object_key = version.object_key
            parse_job = Job(
                tenant_id=source.tenant_id,
                job_type="parse_document",
                idempotency_key=f"parse:{version.id}",
                input={"version_id": version.id},
            )
            db.add(parse_job)
            db.flush()
            job.result = {"document_id": document.id, "version_id": version.id, "parse_job_id": parse_job.id}
            db.commit()
            object_persisted = True
            _step(
                db,
                job.id,
                "persist",
                2,
                "succeeded",
                {"document_id": document.id, "version_id": version.id, "bytes": len(payload)},
            )
            _progress(db, job, 80)

            _step(db, job.id, "dispatch", 3, "running")
            _progress(db, job, 90)
            try:
                parse_version_task.delay(parse_job.id)
            except Exception as dispatch_error:
                dispatch_message = f"{type(dispatch_error).__name__}: {dispatch_error}"
                parse_job.status = "failed"
                parse_job.error_code = "QUEUE_DISPATCH_FAILED"
                parse_job.error_message = dispatch_message[:4000]
                parse_job.finished_at = now()
                version.status = "failed"
                version.error_code = "QUEUE_DISPATCH_FAILED"
                version.error_message = dispatch_message[:4000]
                document.status = "failed"
                db.commit()
                raise RuntimeError(f"解析任务提交失败：{dispatch_error}") from dispatch_error
            _step(db, job.id, "dispatch", 3, "succeeded", {"parse_job_id": parse_job.id})

            source.last_sync_at = now()
            source.last_sync_status = "fetched"
            _update_source_cursor(source, digest, "fetched")
            job.status = "succeeded"
            job.progress = 100
            job.finished_at = now()
            db.commit()
            return job.result
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            db.rollback()
            if uploaded_object_key and not object_persisted:
                try:
                    object_storage.delete(uploaded_object_key)
                except Exception:
                    pass
            job = db.get(Job, job_id)
            source = db.get(SourceConnector, source_id) if source_id else None
            if job is None:
                return {"status": "failed", "error": message}
            if source is not None:
                source.last_sync_at = now()
                source.last_sync_status = "failed"
            for running in db.scalars(
                select(JobStep).where(JobStep.job_id == job.id, JobStep.status == "running")
            ):
                running.status = "failed"
                running.detail = {"message": message[:1000]}
                running.finished_at = now()
            _fail(db, job, "SOURCE_SYNC_FAILED", message)
            return {"status": "failed", "error": message}


def _default_policy(db, model, tenant_id: str):
    return db.scalar(
        select(model).where(
            model.tenant_id == tenant_id,
            model.is_default.is_(True),
            model.enabled.is_(True),
            model.deleted_at.is_(None),
        )
    )


def _canonical_for_name(
    db,
    *,
    tenant_id: str,
    space_id: str,
    name: str,
    entity_type: str = "其他",
    confidence: float = 0.7,
    scope_tokens: list[str] | None = None,
) -> CanonicalEntity:
    from semantica.normalize import EntityNormalizer

    canonical_name = EntityNormalizer().normalize_entity(name, entity_type=entity_type)
    normalized_name = canonical_name.casefold()
    row = constrained_canonical_entity(
        db,
        tenant_id=tenant_id,
        space_id=space_id,
        normalized_name=normalized_name,
        entity_type=entity_type,
    ) or db.scalar(
        select(CanonicalEntity).where(
            CanonicalEntity.space_id == space_id,
            CanonicalEntity.normalized_name == normalized_name,
            CanonicalEntity.entity_type == entity_type,
            CanonicalEntity.deleted_at.is_(None),
        )
    )
    if row is None:
        row = CanonicalEntity(
            tenant_id=tenant_id,
            space_id=space_id,
            canonical_name=canonical_name[:500],
            normalized_name=normalized_name[:500],
            entity_type=entity_type[:100],
            confidence=confidence,
            scope_tokens=scope_tokens or [],
        )
        db.add(row)
        db.flush()
    return row


def _detect_and_resolve_conflicts(
    db,
    *,
    tenant_id: str,
    space_id: str,
    policy: GovernancePolicy,
) -> int:
    from semantica.conflicts.conflict_detector import ConflictDetector

    facts = list(
        db.scalars(
            select(Fact).where(
                Fact.space_id == space_id,
                Fact.deleted_at.is_(None),
                Fact.status.in_(["published", "superseded"]),
            )
        )
    )
    grouped: dict[tuple[str, str], list[Fact]] = {}
    for fact in facts:
        grouped.setdefault((fact.subject_entity_id, fact.predicate), []).append(fact)
    count = 0
    single_value_predicates = {
        str(value).strip().casefold()
        for value in (policy.config or {}).get("single_value_predicates", [])
        if str(value).strip()
    }
    if not single_value_predicates:
        return 0
    detector = ConflictDetector(confidence_threshold=policy.publish_confidence, auto_resolve=True)
    for (entity_id, predicate), rows in grouped.items():
        # A subject can legitimately have many values for predicates such as
        # “支持” or “包含”. Only ontology/policy-declared functional properties
        # are candidates for automatic conflict resolution.
        if predicate.strip().casefold() not in single_value_predicates:
            continue
        objects = [row.object_entity_id or row.object_value or "" for row in rows]
        if len(set(objects)) < 2:
            continue
        candidates = [
            {
                "id": entity_id,
                "value": value,
                "source": row.source_chunk_id,
                "confidence": row.confidence,
            }
            for row, value in zip(rows, objects)
        ]
        detected = detector.detect_value_conflicts(candidates, "value")
        if not detected:
            continue
        winner = max(rows, key=lambda row: (row.confidence, row.created_at))
        if policy.conflict_strategy == "latest":
            winner = max(rows, key=lambda row: row.created_at)
        if policy.conflict_strategy != "keep_all":
            for row in rows:
                row.status = "published" if row.id == winner.id else "superseded"
        case = ConflictCase(
            tenant_id=tenant_id,
            space_id=space_id,
            entity_id=entity_id,
            property_name=predicate,
            conflicting_values=objects,
            source_chunk_ids=[row.source_chunk_id for row in rows],
            strategy=policy.conflict_strategy,
            decision={"winner_fact_id": winner.id, "detector": "Semantica ConflictDetector"},
            status="resolved",
        )
        db.add(case)
        count += 1
    return count


def _reusable_extraction_snapshots(db, chunks: list[Chunk]) -> dict[tuple[str, str], dict[str, Any]]:
    if not chunks:
        return {}
    version_ids = {row.version_id for row in chunks}
    runs = list(
        db.scalars(
            select(ExtractionRun)
            .where(
                ExtractionRun.version_id.in_(version_ids),
                ExtractionRun.status == "succeeded",
            )
            .order_by(ExtractionRun.finished_at.desc())
        )
    )
    latest_run_by_version: dict[str, ExtractionRun] = {}
    for run in runs:
        latest_run_by_version.setdefault(run.version_id, run)
    snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    for chunk in chunks:
        run = latest_run_by_version.get(chunk.version_id)
        if run is None:
            continue
        snapshots[(chunk.structural_path, chunk.content_hash)] = {
            "source_chunk_id": chunk.id,
            "entities": [
                {
                    "text": item.text,
                    "normalized_name": item.normalized_name,
                    "entity_type": item.entity_type,
                    "confidence": item.confidence,
                    "attributes": item.attributes or {},
                }
                for item in db.scalars(
                    select(EntityMention).where(
                        EntityMention.run_id == run.id,
                        EntityMention.chunk_id == chunk.id,
                    )
                )
            ],
            "relations": [
                {
                    "subject_name": item.subject_name,
                    "predicate": item.predicate,
                    "object_name": item.object_name,
                    "confidence": item.confidence,
                    "evidence": item.evidence,
                    "attributes": item.attributes or {},
                    "scope_tokens": item.scope_tokens or [],
                }
                for item in db.scalars(
                    select(RelationAssertion).where(
                        RelationAssertion.run_id == run.id,
                        RelationAssertion.chunk_id == chunk.id,
                    )
                )
            ],
            "events": [
                {
                    "event_type": item.event_type,
                    "trigger": item.trigger,
                    "participants": item.participants or [],
                    "event_time": item.event_time,
                    "confidence": item.confidence,
                    "evidence": item.evidence,
                }
                for item in db.scalars(
                    select(EventAssertion).where(
                        EventAssertion.run_id == run.id,
                        EventAssertion.chunk_id == chunk.id,
                    )
                )
            ],
        }
    return snapshots


@celery_app.task(bind=True, autoretry_for=(), name="documents.process_knowledge")
def process_version_task(self, job_id: str) -> dict[str, Any]:
    settings = get_settings()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return {"status": "missing"}
        version_id = (job.input or {}).get("version_id")
        version = db.get(DocumentVersion, version_id)
        document = db.get(Document, version.document_id) if version else None
        if version is None or document is None or version.tenant_id != job.tenant_id:
            _fail(db, job, "VERSION_NOT_FOUND", "文档版本不存在")
            return {"status": "failed"}
        if version.status != "ready":
            _fail(db, job, "VERSION_NOT_READY", "文档尚未完成解析")
            return {"status": "failed"}
        if (version.parse_summary or {}).get("knowledge_status") == "published" and not job.input.get("force"):
            job.status = "succeeded"
            job.progress = 100
            job.result = {"version_id": version.id, "unchanged": True}
            job.finished_at = now()
            db.commit()
            return job.result

        chunk_policy = _default_policy(db, ChunkPolicy, version.tenant_id)
        extraction_policy = _default_policy(db, ExtractionPolicy, version.tenant_id)
        governance_policy = _default_policy(db, GovernancePolicy, version.tenant_id)
        embedding_model = db.scalar(
            select(ModelConfig).where(
                ModelConfig.tenant_id == version.tenant_id,
                ModelConfig.model_kind == "embedding",
                ModelConfig.is_default.is_(True),
                ModelConfig.enabled.is_(True),
                ModelConfig.deleted_at.is_(None),
            )
        )
        if not all([chunk_policy, extraction_policy, governance_policy, embedding_model]):
            _fail(db, job, "KNOWLEDGE_POLICY_MISSING", "切片、抽取、治理或向量模型配置不完整")
            return {"status": "failed"}
        llm_model = db.get(ModelConfig, extraction_policy.model_config_id) if extraction_policy.model_config_id else None
        if llm_model is None or not llm_model.enabled:
            _fail(db, job, "EXTRACTION_MODEL_MISSING", "语义抽取策略未关联可用大模型")
            return {"status": "failed"}

        job.status = "running"
        job.started_at = now()
        job.attempts += 1
        job.progress = 1
        document.status = "processing"
        db.commit()
        prior_parse_summary = deepcopy(version.parse_summary or {})
        rollback_chunk_state: dict[str, dict[str, Any]] = {}
        rollback_fact_state: dict[str, dict[str, Any]] = {}
        try:
            _step(db, job.id, "normalize_split", 1, "running")
            old_chunks = list(db.scalars(select(Chunk).where(Chunk.version_id == version.id)))
            reuse_source_chunks = old_chunks
            if not reuse_source_chunks:
                previous_version = db.scalar(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.document_id == document.id,
                        DocumentVersion.version_number < version.version_number,
                        DocumentVersion.status == "ready",
                        DocumentVersion.deleted_at.is_(None),
                    )
                    .order_by(DocumentVersion.version_number.desc())
                )
                if previous_version:
                    reuse_source_chunks = list(
                        db.scalars(select(Chunk).where(Chunk.version_id == previous_version.id))
                    )
            reusable_snapshots = _reusable_extraction_snapshots(db, reuse_source_chunks)
            existing_chunks = {row.chunk_id: row for row in old_chunks}
            if old_chunks:
                old_ids = [row.id for row in old_chunks]
                chunk_state_fields = (
                    "element_id", "chunk_policy_id", "ordinal", "text", "content_hash",
                    "structural_path", "page_number", "source_span", "scope_tokens",
                    "status", "deleted_at",
                )
                rollback_chunk_state = {
                    row.id: {field: deepcopy(getattr(row, field)) for field in chunk_state_fields}
                    for row in old_chunks
                }
                old_facts = list(db.scalars(select(Fact).where(Fact.source_chunk_id.in_(old_ids))))
                fact_state_fields = (
                    "subject_entity_id", "predicate", "object_entity_id", "object_value",
                    "confidence", "scope_tokens", "status", "deleted_at",
                )
                rollback_fact_state = {
                    fact.id: {field: deepcopy(getattr(fact, field)) for field in fact_state_fields}
                    for fact in old_facts
                }
                for fact in old_facts:
                    fact.status = "superseded"
                run_ids = list(db.scalars(select(ExtractionRun.id).where(ExtractionRun.version_id == version.id)))
                if run_ids:
                    db.execute(delete(EventAssertion).where(EventAssertion.run_id.in_(run_ids)))
                    db.execute(delete(RelationAssertion).where(RelationAssertion.run_id.in_(run_ids)))
                    db.execute(delete(EntityMention).where(EntityMention.run_id.in_(run_ids)))
                    db.execute(delete(ExtractionRun).where(ExtractionRun.id.in_(run_ids)))
                for old_chunk in old_chunks:
                    old_chunk.status = "superseded"
                for case in db.scalars(
                    select(ConflictCase).where(
                        ConflictCase.space_id == document.space_id,
                        ConflictCase.deleted_at.is_(None),
                    )
                ):
                    if set(case.source_chunk_ids or []) & set(old_ids):
                        db.delete(case)
                db.commit()
            raw_elements = list(
                db.scalars(
                    select(ContentElement)
                    .where(ContentElement.version_id == version.id, ContentElement.deleted_at.is_(None))
                    .order_by(ContentElement.ordinal)
                )
            )
            elements = effective_elements(db, raw_elements, version.id)
            chunks: list[Chunk] = []
            reused_extractions: dict[str, dict[str, Any]] = {}
            global_ordinal = 0
            for element in elements:
                generated = normalize_and_split(
                    element.text,
                    # Keep chunk IDs stable across versions of this document.
                    version_id=document.id,
                    element_key=element.element_id,
                    method=chunk_policy.method,
                    chunk_size=chunk_policy.chunk_size,
                    chunk_overlap=chunk_policy.chunk_overlap,
                    config=chunk_policy.config or {},
                )
                for item in generated:
                    row = existing_chunks.get(item.chunk_id)
                    if row is None:
                        row = Chunk(
                            tenant_id=version.tenant_id,
                            space_id=document.space_id,
                            document_id=document.id,
                            version_id=version.id,
                            element_id=element.id,
                            chunk_policy_id=chunk_policy.id,
                            chunk_id=item.chunk_id,
                            ordinal=global_ordinal,
                            text=item.text,
                            content_hash=item.content_hash,
                            structural_path=element.structural_path,
                            page_number=element.page_number,
                            source_span={"start": item.start_index, "end": item.end_index, **item.metadata},
                            scope_tokens=element.scope_tokens or [],
                        )
                        db.add(row)
                    else:
                        row.element_id = element.id
                        row.chunk_policy_id = chunk_policy.id
                        row.ordinal = global_ordinal
                        row.text = item.text
                        row.content_hash = item.content_hash
                        row.structural_path = element.structural_path
                        row.page_number = element.page_number
                        row.source_span = {"start": item.start_index, "end": item.end_index, **item.metadata}
                        row.scope_tokens = element.scope_tokens or []
                        row.status = "staged"
                        row.deleted_at = None
                    snapshot = reusable_snapshots.get((element.structural_path, item.content_hash))
                    if snapshot:
                        row.source_span = {
                            **row.source_span,
                            "incremental": "unchanged",
                            "reused_from_chunk_id": snapshot["source_chunk_id"],
                        }
                        reused_extractions[row.chunk_id] = snapshot
                    else:
                        row.source_span = {**row.source_span, "incremental": "changed"}
                    chunks.append(row)
                    global_ordinal += 1
            db.commit()
            _step(db, job.id, "normalize_split", 1, "succeeded", {"elements": len(elements), "chunks": len(chunks)})
            _progress(db, job, 16)

            _step(db, job.id, "document_profile", 2, "running")
            deterministic = build_deterministic_profile(elements)
            profile_config = governance_policy.config or {}
            profile_model = llm_model
            configured_profile_model_id = profile_config.get("model_config_id")
            if configured_profile_model_id:
                candidate = db.get(ModelConfig, str(configured_profile_model_id))
                if candidate and candidate.tenant_id == version.tenant_id and candidate.enabled:
                    profile_model = candidate
            model_status = "disabled" if not profile_config.get("enable_model_analysis", True) else "not_configured"
            model_error = None
            model_profile: dict[str, Any] = {}
            if profile_config.get("enable_model_analysis", True) and profile_model:
                profile_secret = decrypt_secret(profile_model.api_key_encrypted)
                if profile_secret:
                    try:
                        model_profile = analyze_profile_with_model(
                            "\n\n".join(element.text for element in elements if element.text),
                            model=profile_model.model_name,
                            api_key=profile_secret,
                            base_url=profile_model.base_url,
                            taxonomy=profile_config.get("taxonomy") or profile_config.get("classification_taxonomy"),
                            summary_length=int(profile_config.get("summary_length", 240)),
                            tag_count=int(profile_config.get("tag_count", 8)),
                            timeout=float((profile_model.config or {}).get("timeout", 60)),
                            max_retries=int(
                                (profile_model.config or {}).get(
                                    "max_retries", (profile_model.config or {}).get("retry", 2)
                                )
                            ),
                        )
                        model_status = "succeeded"
                    except Exception as exc:
                        model_status = "partial_failed"
                        safe_message = str(exc).replace(profile_secret, "***")
                        model_error = f"{type(exc).__name__}: {safe_message}"[:2000]
                else:
                    model_status = "partial_failed"
                    model_error = "治理模型未配置 API Key"
            db.execute(delete(DocumentProfile).where(DocumentProfile.version_id == version.id))
            profile = DocumentProfile(
                tenant_id=version.tenant_id,
                space_id=document.space_id,
                document_id=document.id,
                version_id=version.id,
                summary=model_profile.get("summary", "") if profile_config.get("auto_summary", True) else "",
                classification=model_profile.get("classification", "未分类") if profile_config.get("auto_classify", True) else "未分类",
                document_type=model_profile.get("document_type", "其他"),
                tags=model_profile.get("tags", []),
                keywords=model_profile.get("keywords", []),
                language=deterministic.language,
                main_objects=model_profile.get("main_objects", []),
                time_range=model_profile.get("time_range", {}),
                quality_score=deterministic.quality_score,
                completeness_score=deterministic.completeness_score,
                readability_score=deterministic.readability_score,
                structure_score=deterministic.structure_score,
                media_confidence=deterministic.media_confidence,
                duplicate_ratio=deterministic.duplicate_ratio,
                quality_issues=list(dict.fromkeys(deterministic.quality_issues + model_profile.get("quality_issues", []))),
                recommended_actions=deterministic.recommended_actions,
                deterministic_metrics=deterministic.metrics,
                policy_id=governance_policy.id,
                policy_version=governance_policy.policy_version,
                model_config_id=profile_model.id if model_status in {"succeeded", "partial_failed"} else None,
                model_status=model_status,
                model_error=model_error,
                generated_at=now(),
            )
            db.add(profile)
            db.flush()
            upsert_profile_cases(db, profile)
            if profile.tags:
                document.tags = sorted(set(document.tags or []) | set(profile.tags))
            version.parse_summary = {
                **(version.parse_summary or {}),
                "profile_status": model_status,
                "quality_score": deterministic.quality_score,
            }
            db.commit()
            profile_metrics = {
                "quality_score": deterministic.quality_score,
                "model_status": model_status,
                "issue_count": len(profile.quality_issues),
            }
            _step(db, job.id, "document_profile", 2, "succeeded", profile_metrics)
            _progress(db, job, 20)

            _step(db, job.id, "semantic_extract", 3, "running")
            run = ExtractionRun(
                tenant_id=version.tenant_id,
                space_id=document.space_id,
                version_id=version.id,
                policy_id=extraction_policy.id,
                model_config_id=llm_model.id,
            )
            db.add(run)
            db.flush()
            api_key = decrypt_secret(llm_model.api_key_encrypted)
            if not api_key:
                raise ValueError("语义抽取模型未配置 API Key")
            entity_count = relation_count = event_count = 0
            selected_chunks = chunks[: extraction_policy.max_chunks]
            reused_chunk_count = 0
            model_chunk_count = 0
            for index, chunk in enumerate(selected_chunks):
                snapshot = reused_extractions.get(chunk.chunk_id)
                if snapshot:
                    for entity_index, item in enumerate(snapshot["entities"]):
                        mention_id = hashlib.sha256(
                            f"{chunk.chunk_id}:entity:{entity_index}:{item['text']}".encode()
                        ).hexdigest()
                        db.add(
                            EntityMention(
                                tenant_id=version.tenant_id,
                                space_id=document.space_id,
                                run_id=run.id,
                                chunk_id=chunk.id,
                                mention_id=mention_id,
                                text=item["text"],
                                normalized_name=item["normalized_name"],
                                entity_type=item["entity_type"],
                                confidence=item["confidence"],
                                attributes=item["attributes"],
                                scope_tokens=chunk.scope_tokens,
                            )
                        )
                        entity_count += 1
                    for item in snapshot["relations"]:
                        db.add(
                            RelationAssertion(
                                tenant_id=version.tenant_id,
                                space_id=document.space_id,
                                run_id=run.id,
                                chunk_id=chunk.id,
                                **item,
                            )
                        )
                        relation_count += 1
                    for item in snapshot["events"]:
                        db.add(
                            EventAssertion(
                                tenant_id=version.tenant_id,
                                space_id=document.space_id,
                                run_id=run.id,
                                chunk_id=chunk.id,
                                **item,
                            )
                        )
                        event_count += 1
                    reused_chunk_count += 1
                    _progress(db, job, 20 + round(30 * (index + 1) / max(1, len(selected_chunks))))
                    continue
                output = extract_semantics(
                    chunk.text,
                    chunk_key=chunk.chunk_id,
                    api_key=api_key,
                    model=llm_model.model_name,
                    base_url=llm_model.base_url,
                    entity_types=extraction_policy.entity_types,
                    relation_types=extraction_policy.relation_types,
                    temperature=float((extraction_policy.config or {}).get("temperature", 0.1)),
                    timeout=float((llm_model.config or {}).get("timeout", 60)),
                    max_retries=int(
                        (llm_model.config or {}).get(
                            "max_retries", (llm_model.config or {}).get("retry", 2)
                        )
                    ),
                )
                for item in output.entities:
                    if item["confidence"] < extraction_policy.min_confidence:
                        continue
                    db.add(
                        EntityMention(
                            tenant_id=version.tenant_id,
                            space_id=document.space_id,
                            run_id=run.id,
                            chunk_id=chunk.id,
                            mention_id=item["mention_id"],
                            text=item["text"],
                            normalized_name=item["text"].casefold(),
                            entity_type=item["entity_type"],
                            confidence=item["confidence"],
                            attributes=item["attributes"],
                            scope_tokens=chunk.scope_tokens,
                        )
                    )
                    entity_count += 1
                for item in output.relations:
                    if item["confidence"] < extraction_policy.min_confidence:
                        continue
                    db.add(RelationAssertion(tenant_id=version.tenant_id, space_id=document.space_id, run_id=run.id, chunk_id=chunk.id, scope_tokens=chunk.scope_tokens, **item))
                    relation_count += 1
                for item in output.events:
                    if item["confidence"] < extraction_policy.min_confidence:
                        continue
                    db.add(EventAssertion(tenant_id=version.tenant_id, space_id=document.space_id, run_id=run.id, chunk_id=chunk.id, **item))
                    event_count += 1
                model_chunk_count += 1
                _progress(db, job, 20 + round(30 * (index + 1) / max(1, len(selected_chunks))))
            run.status = "succeeded"
            run.metrics = {
                "chunks": len(selected_chunks),
                "model_chunks": model_chunk_count,
                "reused_chunks": reused_chunk_count,
                "entities": entity_count,
                "relations": relation_count,
                "events": event_count,
            }
            run.finished_at = now()
            db.commit()
            _step(db, job.id, "semantic_extract", 3, "succeeded", run.metrics)
            _progress(db, job, 52)

            _step(db, job.id, "governance", 4, "running")
            mentions = list(db.scalars(select(EntityMention).where(EntityMention.run_id == run.id)))
            governed, decisions = govern_entities(
                [
                    {
                        "mention_id": row.mention_id,
                        "text": row.text,
                        "entity_type": row.entity_type,
                        "confidence": row.confidence,
                    }
                    for row in mentions
                ],
                similarity_threshold=governance_policy.similarity_threshold,
                constraints=entity_pair_constraints(db, version.tenant_id, document.space_id),
            )
            mention_entity: dict[str, CanonicalEntity] = {}
            for item in governed:
                entity = constrained_canonical_entity(
                    db,
                    tenant_id=version.tenant_id,
                    space_id=document.space_id,
                    normalized_name=item.normalized_name,
                    entity_type=item.entity_type,
                ) or db.scalar(
                    select(CanonicalEntity).where(
                        CanonicalEntity.space_id == document.space_id,
                        CanonicalEntity.normalized_name == item.normalized_name,
                        CanonicalEntity.entity_type == item.entity_type,
                        CanonicalEntity.deleted_at.is_(None),
                    )
                )
                if entity is None:
                    entity = CanonicalEntity(
                        tenant_id=version.tenant_id,
                        space_id=document.space_id,
                        canonical_name=item.canonical_name,
                        normalized_name=item.normalized_name,
                        entity_type=item.entity_type,
                        aliases=item.aliases,
                        confidence=item.confidence,
                    )
                    db.add(entity)
                    db.flush()
                else:
                    entity.aliases = sorted(set(entity.aliases or []) | set(item.aliases))
                    entity.confidence = max(entity.confidence, item.confidence)
                    entity.source_count += 1
                for mention_id in item.mention_ids:
                    mention_entity[mention_id] = entity
            for decision in decisions:
                db.add(
                    AutoDecisionRecord(
                        tenant_id=version.tenant_id,
                        space_id=document.space_id,
                        policy_id=governance_policy.id,
                        decision_type=decision["type"],
                        object_type="entity_mention",
                        object_ids=decision["mention_ids"],
                        decision=decision,
                    )
                )
            relations = list(db.scalars(select(RelationAssertion).where(RelationAssertion.run_id == run.id)))
            published_facts = 0
            for relation in relations:
                if relation.confidence < governance_policy.publish_confidence:
                    relation.status = "rejected"
                    continue
                subject = _canonical_for_name(db, tenant_id=version.tenant_id, space_id=document.space_id, name=relation.subject_name, confidence=relation.confidence, scope_tokens=relation.scope_tokens)
                obj = _canonical_for_name(db, tenant_id=version.tenant_id, space_id=document.space_id, name=relation.object_name, confidence=relation.confidence, scope_tokens=relation.scope_tokens)
                fact = db.scalar(select(Fact).where(
                    Fact.space_id == document.space_id,
                    Fact.subject_entity_id == subject.id,
                    Fact.predicate == relation.predicate,
                    Fact.object_entity_id == obj.id,
                    Fact.source_chunk_id == relation.chunk_id,
                ).order_by(Fact.created_at.desc()).limit(1))
                if fact is None:
                    fact = Fact(
                        tenant_id=version.tenant_id,
                        space_id=document.space_id,
                        subject_entity_id=subject.id,
                        predicate=relation.predicate,
                        object_entity_id=obj.id,
                        source_chunk_id=relation.chunk_id,
                        confidence=relation.confidence,
                        scope_tokens=relation.scope_tokens,
                    )
                    db.add(fact)
                else:
                    fact.confidence = relation.confidence
                    fact.scope_tokens = relation.scope_tokens
                    fact.status = "published"
                    fact.deleted_at = None
                relation.status = "published"
                published_facts += 1
            for mention in mentions:
                mention.status = "published" if mention.mention_id in mention_entity else "rejected"
            db.commit()
            structured_materialization = materialize_database_mapping(
                db,
                document=document,
                version=version,
            )
            if structured_materialization.get("enabled"):
                published_facts += int(structured_materialization.get("attribute_facts") or 0)
                published_facts += int(structured_materialization.get("relationship_facts") or 0)
            db.commit()
            conflicts = _detect_and_resolve_conflicts(db, tenant_id=version.tenant_id, space_id=document.space_id, policy=governance_policy)
            upsert_conflict_cases(db, version.tenant_id, document.space_id)
            db.commit()
            governance_metrics = {
                "canonical_entities": len(governed),
                "facts": published_facts,
                "conflicts": conflicts,
                "decisions": len(decisions),
                "structured_materialization": structured_materialization,
            }
            _step(db, job.id, "governance", 4, "succeeded", governance_metrics)
            _progress(db, job, 68)

            _step(db, job.id, "graph_publish", 5, "running")
            graph_release = publish_graph_snapshot(db, version.tenant_id, document.space_id)
            db.flush()
            graph_number = graph_release.release_number
            _step(
                db,
                job.id,
                "graph_publish",
                5,
                "succeeded",
                {
                    "release": graph_number,
                    "entities": graph_release.entity_count,
                    "facts": graph_release.fact_count,
                    "asserted_facts": (graph_release.validation_report or {}).get("asserted_facts", 0),
                    "inferred_facts": (graph_release.validation_report or {}).get("inferred_facts", 0),
                },
            )
            _progress(db, job, 78)

            _step(db, job.id, "index_publish", 6, "running")
            release, index_result = publish_index_snapshot(
                db,
                tenant_id=version.tenant_id,
                space_id=document.space_id,
                graph_release=graph_release,
                embedding_model=embedding_model,
            )
            index_number = release.release_number
            knowledge_release = activate_knowledge_release(
                db,
                tenant_id=version.tenant_id,
                space_id=document.space_id,
                graph_release=graph_release,
                index_release=release,
            )
            for row in chunks:
                row.status = "published"
            version.parse_summary = {
                **(version.parse_summary or {}),
                "knowledge_status": "published",
                "chunks": len(chunks),
                "entities": entity_count,
                "facts": published_facts,
                "graph_release": graph_number,
                "index_release": index_number,
                "structured_materialization": structured_materialization,
            }
            document.status = "ready"
            job.status = "succeeded"
            job.progress = 100
            job.result = {
                "version_id": version.id,
                "chunks": len(chunks),
                "entities": entity_count,
                "relations": relation_count,
                "events": event_count,
                "facts": published_facts,
                "graph_release": graph_number,
                "index_release": index_number,
                "knowledge_release": knowledge_release.release_number,
                "structured_materialization": structured_materialization,
            }
            job.finished_at = now()
            curation_batch = db.get(CurationBatch, (job.input or {}).get("curation_batch_id"))
            if curation_batch:
                curation_batch.status = "published"
                curation_batch.published_at = now()
                curation_batch.publish_error = None
            db.commit()
            _step(db, job.id, "index_publish", 6, "succeeded", {"release": index_number, "chunks": release.chunk_count, "dimension": release.embedding_dimension, "embedded": index_result["embedded_count"], "reused": index_result["reused_vector_count"]})
            automatic_runs = _queue_automatic_inference(
                db,
                tenant_id=version.tenant_id,
                space_id=document.space_id,
                document_id=document.id,
                version_id=version.id,
            )
            if automatic_runs:
                job = db.get(Job, job.id)
                job.result = {**(job.result or {}), "automatic_inference_runs": automatic_runs}
                db.commit()
            return job.result
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            db.rollback()
            job = db.get(Job, job_id)
            version = db.get(DocumentVersion, version_id)
            document = db.get(Document, version.document_id) if version else None
            if job is None:
                return {"status": "failed", "error": message}
            if version:
                if rollback_chunk_state:
                    current_chunks = list(db.scalars(select(Chunk).where(Chunk.version_id == version.id)))
                    current_chunk_ids = [row.id for row in current_chunks]
                    for row in current_chunks:
                        snapshot = rollback_chunk_state.get(row.id)
                        if snapshot:
                            for field, value in snapshot.items():
                                setattr(row, field, deepcopy(value))
                        else:
                            row.status = "superseded"
                    if current_chunk_ids:
                        for fact in db.scalars(select(Fact).where(Fact.source_chunk_id.in_(current_chunk_ids))):
                            snapshot = rollback_fact_state.get(fact.id)
                            if snapshot:
                                for field, value in snapshot.items():
                                    setattr(fact, field, deepcopy(value))
                            else:
                                fact.status = "superseded"
                    version.parse_summary = {
                        **prior_parse_summary,
                        "curation_status": "publish_failed",
                        "curation_error": message[:1000],
                    }
                else:
                    version.parse_summary = {**(version.parse_summary or {}), "knowledge_status": "failed", "knowledge_error": message[:1000]}
            if document:
                document.status = "ready"
            curation_batch = db.get(CurationBatch, (job.input or {}).get("curation_batch_id"))
            if curation_batch:
                curation_batch.status = "publish_failed"
                curation_batch.publish_error = message[:2000]
            run = db.scalar(select(ExtractionRun).where(ExtractionRun.version_id == version_id).order_by(ExtractionRun.created_at.desc()))
            if run and run.status == "running":
                run.status = "failed"
                run.error_message = message[:4000]
                run.finished_at = now()
            for running in db.scalars(select(JobStep).where(JobStep.job_id == job.id, JobStep.status == "running")):
                running.status = "failed"
                running.detail = {"message": message[:1000]}
                running.finished_at = now()
            _fail(db, job, "KNOWLEDGE_PROCESS_FAILED", message)
            return {"status": "failed", "error": message}


@celery_app.task(bind=True, autoretry_for=(), name="curation.publish_space")
def publish_curation_task(self, job_id: str) -> dict[str, Any]:
    """Rebuild graph and search projections after a non-parser curation decision."""
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return {"status": "missing"}
        space_id = str((job.input or {}).get("space_id") or "")
        batch_id = (job.input or {}).get("batch_id")
        batch = db.get(CurationBatch, batch_id) if batch_id else None
        job.status = "running"
        job.started_at = now()
        job.attempts += 1
        job.progress = 5
        db.commit()
        try:
            _step(db, job.id, "resolve_effective", 1, "running")
            invalidated = invalidate_inference_for_space(db, job.tenant_id, space_id)
            _step(db, job.id, "resolve_effective", 1, "succeeded", {"invalidated_inference": invalidated})
            _progress(db, job, 25)

            _step(db, job.id, "graph_publish", 2, "running")
            graph_release = publish_graph_snapshot(db, job.tenant_id, space_id)
            db.flush()
            _step(db, job.id, "graph_publish", 2, "succeeded", {"release": graph_release.release_number})
            _progress(db, job, 60)

            _step(db, job.id, "index_publish", 3, "running")
            try:
                index_release, index_result = publish_index_snapshot(
                    db,
                    tenant_id=job.tenant_id,
                    space_id=space_id,
                    graph_release=graph_release,
                )
            except RuntimeError as exc:
                # A freshly-created graph space may legitimately have no
                # document/index release yet.  The graph projection is still
                # publishable; the first document pipeline will create the
                # composite knowledge release later.
                if "尚无向量模型发布记录" not in str(exc):
                    raise
                _step(
                    db,
                    job.id,
                    "index_publish",
                    3,
                    "succeeded",
                    {"skipped": True, "reason": "space_has_no_search_index"},
                )
                if batch:
                    batch.status = "published"
                    batch.published_at = now()
                    batch.publish_error = None
                job.status = "succeeded"
                job.progress = 100
                job.result = {
                    "space_id": space_id,
                    "graph_release": graph_release.release_number,
                    "index_release": None,
                    "knowledge_release": None,
                    "invalidated_inference": invalidated,
                    "warnings": ["space_has_no_search_index"],
                }
                job.finished_at = now()
                db.commit()
                return job.result
            knowledge_release = activate_knowledge_release(
                db,
                tenant_id=job.tenant_id,
                space_id=space_id,
                graph_release=graph_release,
                index_release=index_release,
                curation_batch_id=batch.id if batch else None,
            )
            _step(db, job.id, "index_publish", 3, "succeeded", {
                "release": index_release.release_number,
                "embedded": index_result["embedded_count"],
                "reused": index_result["reused_vector_count"],
            })
            if batch:
                batch.status = "published"
                batch.published_at = now()
                batch.publish_error = None
            job.status = "succeeded"
            job.progress = 100
            job.result = {
                "space_id": space_id,
                "graph_release": graph_release.release_number,
                "index_release": index_release.release_number,
                "knowledge_release": knowledge_release.release_number,
                "invalidated_inference": invalidated,
            }
            job.finished_at = now()
            db.commit()
            return job.result
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            db.rollback()
            job = db.get(Job, job_id)
            batch = db.get(CurationBatch, batch_id) if batch_id else None
            if batch:
                batch.status = "publish_failed"
                batch.publish_error = message[:2000]
            if job:
                _fail(db, job, "CURATION_PUBLISH_FAILED", message)
            return {"status": "failed", "error": message}


@celery_app.task(bind=True, autoretry_for=(), name="analysis.infer")
def run_inference_task(self, job_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return {"status": "missing"}
        run = db.get(InferenceRun, (job.input or {}).get("inference_run_id"))
        if run is None or run.tenant_id != job.tenant_id:
            _fail(db, job, "INFERENCE_RUN_NOT_FOUND", "推理运行不存在")
            return {"status": "failed"}
        job.status = "running"
        job.started_at = now()
        job.attempts += 1
        db.commit()
        stage_sequences = {"scope": 1, "rules": 2, "facts": 3, "reasoning": 4, "persist": 5}

        def report(percent: int, stage: str, detail: dict[str, Any]) -> None:
            current_job = db.get(Job, job.id)
            if current_job:
                current_job.progress = percent
            _step(db, job.id, stage, stage_sequences.get(stage, 99), "succeeded", detail)

        try:
            result = execute_inference_run(db, run.id, progress=report)
            run = db.get(InferenceRun, run.id)
            if run and run.mode == "publish":
                releases: dict[str, int] = {}
                for space_id in run.space_ids or []:
                    release = publish_graph_snapshot(db, run.tenant_id, space_id)
                    db.flush()
                    releases[space_id] = release.release_number
                run.graph_releases = releases
                result["graph_releases"] = releases
                run.metrics = {**(run.metrics or {}), "graph_releases": releases}
                db.commit()
            job = db.get(Job, job.id)
            job.status = "succeeded"
            job.progress = 100
            job.result = result
            job.finished_at = now()
            db.commit()
            return result
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            db.rollback()
            run = db.get(InferenceRun, run.id)
            if run:
                run.status = "failed"
                run.error_code = "INFERENCE_FAILED"
                run.error_message = message[:4000]
                run.finished_at = now()
            job = db.get(Job, job.id)
            if job:
                _fail(db, job, "INFERENCE_FAILED", message)
            return {"status": "failed", "error": message}


@celery_app.task(name="sources.schedule_due")
def schedule_due_sources() -> dict[str, int]:
    queued = 0
    current = now()
    with SessionLocal() as db:
        sources = list(
            db.scalars(
                select(SourceConnector).where(
                    SourceConnector.enabled.is_(True),
                    SourceConnector.deleted_at.is_(None),
                )
            )
        )
        active_jobs = list(
            db.scalars(
                select(Job).where(
                    Job.job_type == "sync_source",
                    Job.status.in_(["queued", "running"]),
                    Job.deleted_at.is_(None),
                )
            )
        )
        active_ids = {(row.input or {}).get("source_id") for row in active_jobs}
        for source in sources:
            minutes = int((source.config or {}).get("schedule_minutes") or 0)
            if minutes <= 0 or source.id in active_ids:
                continue
            next_value = (source.cursor or {}).get("next_sync_at")
            try:
                due = datetime.fromisoformat(next_value) if next_value else source.created_at
            except (TypeError, ValueError):
                due = source.created_at
            if due > current:
                continue
            bucket = int(current.timestamp() // max(60, minutes * 60))
            job = Job(
                tenant_id=source.tenant_id,
                job_type="sync_source",
                idempotency_key=f"scheduled-sync:{source.id}:{bucket}",
                input={"source_id": source.id, "scheduled": True},
            )
            db.add(job)
            try:
                db.commit()
            except Exception:
                db.rollback()
                continue
            try:
                sync_source_task.delay(job.id)
                queued += 1
            except Exception as exc:
                job.status = "failed"
                job.error_code = "QUEUE_DISPATCH_FAILED"
                job.error_message = f"{type(exc).__name__}: {exc}"[:4000]
                job.finished_at = now()
                source.last_sync_status = "failed"
                db.commit()
    return {"queued": queued}
