from __future__ import annotations

from copy import deepcopy
import hashlib
import tempfile
import time
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
    MediaAudioSegment,
    MediaFrame,
    MediaProcessingRun,
    MediaScene,
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
from packages.semantica_adapter.media_pipeline import MediaProcessingCancelled, process_media_file
from packages.semantica_adapter.ingest import IngestedPayload, extract_media_payloads
from packages.platform.media import (
    fingerprint,
    media_stage_fingerprints,
    media_type_for,
    require_cloud_confirmation,
)
from packages.platform.media_policy import resolve_media_policy


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


_MEDIA_PROGRESS_RANGES = {
    "media_probe": (15, 20),
    "asr": (20, 45),
    "scene_detection": (20, 30),
    "frame_extraction": (30, 48),
    "ocr": (48, 62),
    "vision": (62, 78),
}


def _media_model(db, tenant_id: str, kind: str, configured_id: str | None) -> ModelConfig | None:
    if configured_id:
        row = db.get(ModelConfig, configured_id)
        if row is None or (
            row.tenant_id != tenant_id
            or row.model_kind != kind
            or not row.enabled
            or row.deleted_at is not None
        ):
            # An immutable policy snapshot explicitly names this model.  Falling
            # back to a default here could silently change local/cloud execution
            # and send data to a provider the user did not select.
            return None
        return row
    return db.scalar(select(ModelConfig).where(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.model_kind == kind,
            ModelConfig.is_default.is_(True),
            ModelConfig.enabled.is_(True),
            ModelConfig.deleted_at.is_(None),
        ))


def _media_model_secret(db, model: ModelConfig | None) -> str | None:
    if model is None:
        return None
    secret = decrypt_secret(model.api_key_encrypted)
    credential_id = str((model.config or {}).get("credential_model_config_id") or "").strip()
    if not secret and credential_id:
        credential = db.get(ModelConfig, credential_id)
        if credential and credential.tenant_id == model.tenant_id and credential.deleted_at is None:
            secret = decrypt_secret(credential.api_key_encrypted)
    return secret


def _media_model_fingerprint(model: ModelConfig | None) -> dict[str, Any] | None:
    """Describe functional model settings without credentials or audit state.

    Connection tests update ``updated_at``/``last_test_at`` but do not alter
    inference. Including either would make an otherwise identical media run
    miss every cache immediately after the mandatory connection test.
    """
    if model is None:
        return None
    return {
        "id": model.id,
        "kind": model.model_kind,
        "provider": model.provider,
        "model": model.model_name,
        "base_url": model.base_url,
        "config": model.config or {},
    }


def _persist_media_result(db, run: MediaProcessingRun, result: dict[str, Any]) -> None:
    scene_ids: dict[int, str] = {}
    scene_rows: dict[int, MediaScene] = {}
    for item in result.get("scenes") or []:
        scene_frames = [
            frame for frame in (result.get("frames") or [])
            if frame.get("scene_index") == int(item.get("scene_index") or 0)
        ]
        component_failed = any(
            frame.get("ocr_status") == "failed" or frame.get("vision_status") == "failed"
            for frame in scene_frames
        )
        scene_evidence = dict(item.get("evidence") or {})
        scene_evidence.update({
            "detection_method": item.get("detection_method"),
            "duration_seconds": max(
                0.0,
                float(item.get("time_end") or 0) - float(item.get("time_start") or 0),
            ),
        })
        item["evidence"] = scene_evidence
        scene = MediaScene(
            tenant_id=run.tenant_id,
            run_id=run.id,
            scene_index=int(item.get("scene_index") or 0),
            time_start=float(item.get("time_start") or 0),
            time_end=float(item.get("time_end") or 0),
            detection_score=item.get("detection_score"),
            summary=str(item.get("summary") or ""),
            evidence=scene_evidence,
            status="partial_failed" if component_failed else "ready",
        )
        db.add(scene); db.flush(); scene_ids[scene.scene_index] = scene.id; scene_rows[scene.scene_index] = scene
        item["id"] = scene.id
    for item in result.get("segments") or []:
        db.add(MediaAudioSegment(
            tenant_id=run.tenant_id,
            run_id=run.id,
            segment_index=int(item.get("segment_index") or 0),
            time_start=float(item.get("start") or 0),
            time_end=float(item.get("end") or item.get("start") or 0),
            text=str(item.get("text") or ""),
            language=item.get("language"),
            speaker=item.get("speaker"),
            confidence=item.get("confidence"),
            segment_metadata={key: value for key, value in item.items() if key not in {"text"}},
        ))
    for item in result.get("frames") or []:
        scene_index = item.get("scene_index")
        frame = MediaFrame(
            tenant_id=run.tenant_id,
            run_id=run.id,
            scene_id=scene_ids.get(int(scene_index)) if scene_index is not None else None,
            frame_index=int(item.get("frame_index") or 0),
            timestamp_seconds=float(item.get("timestamp") or 0),
            object_key=str(item["object_key"]),
            thumbnail_key=str(item["thumbnail_key"]),
            width=item.get("width"),
            height=item.get("height"),
            sha256=str(item["sha256"]),
            perceptual_hash=item.get("perceptual_hash"),
            selection_reason=str(item.get("selection_reason") or "interval"),
            ocr_status=str(item.get("ocr_status") or "not_configured"),
            ocr_text=str(item.get("ocr_text") or ""),
            ocr_confidence=item.get("ocr_confidence"),
            vision_status=str(item.get("vision_status") or "not_configured"),
            vision_result=item.get("vision_result") or {},
            frame_metadata={
                "vision_model": item.get("vision_model"),
                "vision_usage": item.get("vision_usage") or {},
                "vision_elapsed_ms": item.get("vision_elapsed_ms"),
                "cloud_processing": bool(item.get("cloud_processing")),
                "vision_called_at": item.get("vision_called_at"),
                "cloud_processing_reason": item.get("cloud_processing_reason"),
                "scene_index": scene_index,
                "format": item.get("format", "jpeg"),
            },
        )
        db.add(frame); db.flush(); item["id"] = frame.id
    for item in result.get("scenes") or []:
        scene_index = int(item.get("scene_index") or 0)
        evidence = dict(item.get("evidence") or {})
        evidence["frame_ids"] = [
            frame.get("id") for frame in (result.get("frames") or [])
            if frame.get("scene_index") == scene_index and frame.get("id")
        ]
        evidence["transcript_segment_indexes"] = evidence.pop(
            "segment_indexes", evidence.get("transcript_segment_indexes", [])
        )
        item["evidence"] = evidence
        if scene_index in scene_rows:
            scene_rows[scene_index].evidence = evidence


def _rehydrate_cached_media_result(db, run: MediaProcessingRun) -> dict[str, Any]:
    asr_model = db.get(ModelConfig, run.asr_model_config_id) if run.asr_model_config_id else None
    vision_model = db.get(ModelConfig, run.vision_model_config_id) if run.vision_model_config_id else None
    scenes = [{
        "scene_index": row.scene_index, "time_start": row.time_start, "time_end": row.time_end,
        "detection_score": row.detection_score, "summary": row.summary, "evidence": row.evidence or {},
        "detection_method": (row.evidence or {}).get("detection_method"),
    } for row in db.scalars(select(MediaScene).where(
        MediaScene.run_id == run.id, MediaScene.deleted_at.is_(None)
    ).order_by(MediaScene.scene_index))]
    segments = [{
        "segment_index": row.segment_index, "start": row.time_start, "end": row.time_end,
        "text": row.text, "language": row.language, "speaker": row.speaker,
        "confidence": row.confidence, **(row.segment_metadata or {}),
    } for row in db.scalars(select(MediaAudioSegment).where(
        MediaAudioSegment.run_id == run.id, MediaAudioSegment.deleted_at.is_(None)
    ).order_by(MediaAudioSegment.segment_index))]
    frames = [{
        "frame_index": row.frame_index, "timestamp": row.timestamp_seconds,
        "scene_index": (row.frame_metadata or {}).get("scene_index"), "object_key": row.object_key,
        "thumbnail_key": row.thumbnail_key, "width": row.width, "height": row.height,
        "sha256": row.sha256, "perceptual_hash": row.perceptual_hash,
        "selection_reason": row.selection_reason, "ocr_status": row.ocr_status,
        "ocr_text": row.ocr_text, "ocr_confidence": row.ocr_confidence,
        "vision_status": row.vision_status, "vision_result": row.vision_result or {},
        "vision_model": (row.frame_metadata or {}).get("vision_model"),
        "vision_usage": (row.frame_metadata or {}).get("vision_usage") or {},
        "vision_elapsed_ms": (row.frame_metadata or {}).get("vision_elapsed_ms"),
        "cloud_processing": bool((row.frame_metadata or {}).get("cloud_processing")),
        "vision_called_at": (row.frame_metadata or {}).get("vision_called_at"),
        "cloud_processing_reason": (row.frame_metadata or {}).get("cloud_processing_reason"),
        "format": (row.frame_metadata or {}).get("format", "jpeg"),
    } for row in db.scalars(select(MediaFrame).where(
        MediaFrame.run_id == run.id, MediaFrame.deleted_at.is_(None)
    ).order_by(MediaFrame.frame_index))]
    audio_events = [
        {"name": name, "start": item.get("start"), "end": item.get("end")}
        for item in segments for name in (item.get("events") or [])
    ]
    vision_descriptions = list(dict.fromkeys(
        str((item.get("vision_result") or {}).get("scene_summary") or "").strip()
        for item in frames if str((item.get("vision_result") or {}).get("scene_summary") or "").strip()
    ))
    return {
        "type": run.media_type, "metadata": run.probe or {}, "probe": run.probe or {},
        "transcript": " ".join(item["text"] for item in segments if item.get("text")).strip(),
        "segments": segments, "audio_events": audio_events, "scenes": scenes, "frames": frames,
        "model": asr_model.model_name if asr_model else None,
        "model_version": ((asr_model.config or {}).get("version") if asr_model else None),
        "transcription_time_seconds": (run.result or {}).get("transcription_time_seconds"),
        "vision_description": "\n".join(vision_descriptions),
        "transcription_status": (run.result or {}).get("transcription_status", "succeeded"),
        "vision_status": (run.result or {}).get("vision_status", "not_configured"),
        "vision_model": vision_model.model_name if vision_model else None,
        "warnings": run.warnings or [], "frame_count": len(frames),
        "cloud_frame_count": sum(1 for item in frames if item.get("cloud_processing") and item.get("vision_status") == "succeeded"),
        "generate_summary": bool(
            ((run.policy_snapshot or {}).get("config") or {}).get("asr", {}).get("generate_summary", True)
            or ((run.policy_snapshot or {}).get("config") or {}).get("vision", {}).get("generate_video_summary", True)
        ),
        "generate_chapters": bool(
            ((run.policy_snapshot or {}).get("config") or {}).get("asr", {}).get("generate_chapters", True)
        ),
        "scene_count": len(scenes), "segment_count": len(segments),
    }
def _database_processing_modes(source: SourceConnector | None) -> tuple[bool, bool, bool]:
    """Return database-snapshot, profile-model and generic-extraction modes.

    Generic LLM analysis stays opt-in for database snapshots because their
    authoritative semantics come from the versioned schema/ontology mapping.
    Non-database documents preserve the existing model-backed behavior.
    """
    is_database_snapshot = bool(
        source
        and source.deleted_at is None
        and source.source_type == "database"
    )
    if not is_database_snapshot:
        return False, True, True
    config = source.config or {}
    return (
        True,
        bool(config.get("database_profile_model_enabled", False)),
        bool(config.get("generic_semantic_extraction_enabled", False)),
    )


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
        media_run: MediaProcessingRun | None = None
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
                transcriber = None
                visual_describer = None
                media_result = None
                media_kind = media_type_for(version.filename, version.content_type)
                active_media_snapshot = dict(
                    (job.input or {}).get("media_policy_snapshot")
                    or version.media_policy_snapshot
                    or {}
                )
                active_policy_version_id = (
                    (job.input or {}).get("media_policy_version_id")
                    or version.media_policy_version_id
                )
                if media_kind in {"image", "audio", "video"} and active_media_snapshot:
                    media_config = dict(active_media_snapshot.get("config") or {})
                    asr_id = str((media_config.get("asr") or {}).get("model_config_id") or "") or None
                    vision_id = str((media_config.get("vision") or {}).get("model_config_id") or "") or None
                    asr_model = _media_model(db, version.tenant_id, "asr", asr_id)
                    vision_model = _media_model(db, version.tenant_id, "vision", vision_id)
                    if (
                        (media_config.get("vision") or {}).get("enabled")
                        and (media_config.get("vision") or {}).get("execution") == "local"
                        and vision_model is not None
                        and not bool((vision_model.config or {}).get("local_runtime"))
                    ):
                        # A local policy must never become a hidden cloud call
                        # because its selected model points to a public API.
                        vision_model = None
                    stage_fingerprints = media_stage_fingerprints(
                        version.sha256,
                        media_config,
                        asr_model=_media_model_fingerprint(asr_model),
                        vision_model=_media_model_fingerprint(vision_model),
                    )
                    media_fingerprint = fingerprint({
                        "timeline": stage_fingerprints["timeline"],
                        "adapter": "media-pipeline-v2",
                    })
                    media_run = MediaProcessingRun(
                        tenant_id=version.tenant_id,
                        space_id=document.space_id,
                        document_id=document.id,
                        version_id=version.id,
                        job_id=job.id,
                        policy_version_id=active_policy_version_id,
                        policy_snapshot=active_media_snapshot,
                        media_type=media_kind,
                        status="running",
                        stage="media_probe",
                        progress=15,
                        input_fingerprint=media_fingerprint,
                        asr_model_config_id=asr_model.id if asr_model else None,
                        vision_model_config_id=vision_model.id if vision_model else None,
                        cache={"stage_fingerprints": stage_fingerprints, "stage_hits": []},
                        started_at=now(),
                    )
                    db.add(media_run); db.commit()

                    def media_progress(stage: str, completed: int, total: int) -> None:
                        start, end = _MEDIA_PROGRESS_RANGES.get(stage, (20, 78))
                        ratio = min(max(completed / max(total, 1), 0), 1)
                        value = max(job.progress, round(start + (end - start) * ratio))
                        media_run.stage = stage
                        media_run.progress = max(media_run.progress, value)
                        job.progress = value
                        db.commit()

                    def media_cancelled() -> bool:
                        db.refresh(media_run, attribute_names=["cancel_requested_at"])
                        return media_run.cancel_requested_at is not None

                    def media_artifact_writer(frame_path: Path, thumbnail_path: Path, frame_index: int):
                        prefix = f"{version.tenant_id}/{document.space_id}/{document.id}/{version.id}/media/{media_run.id}"
                        object_key = f"{prefix}/frames/{frame_index:05d}.jpg"
                        thumbnail_key = f"{prefix}/thumbnails/{frame_index:05d}.jpg"
                        object_storage.put_file(object_key, frame_path, "image/jpeg")
                        object_storage.put_file(thumbnail_key, thumbnail_path, "image/jpeg")
                        return object_key, thumbnail_key

                    def media_artifact_reader(object_key: str, target: Path) -> None:
                        object_storage.get_file(object_key, target)

                    cached_run = None
                    target_scene_id = str((job.input or {}).get("target_scene_id") or "") or None
                    target_scene_index = None
                    preferred_reuse_run = None
                    if target_scene_id:
                        target_scene = db.get(MediaScene, target_scene_id)
                        if (
                            target_scene is None
                            or target_scene.tenant_id != version.tenant_id
                            or target_scene.deleted_at is not None
                        ):
                            raise ValueError("指定的媒体场景不存在")
                        preferred_reuse_run = db.get(MediaProcessingRun, target_scene.run_id)
                        if preferred_reuse_run is None or preferred_reuse_run.version_id != version.id:
                            raise ValueError("指定的媒体场景不属于当前文档版本")
                        target_scene_index = target_scene.scene_index
                    if (
                        bool((media_config.get("cache") or {}).get("enabled", True))
                        and not bool((job.input or {}).get("force_media"))
                        and not target_scene_id
                    ):
                        cached_run = db.scalar(select(MediaProcessingRun).where(
                            MediaProcessingRun.tenant_id == version.tenant_id,
                            MediaProcessingRun.id != media_run.id,
                            MediaProcessingRun.input_fingerprint == media_fingerprint,
                            MediaProcessingRun.status == "succeeded",
                            MediaProcessingRun.deleted_at.is_(None),
                        ).order_by(MediaProcessingRun.finished_at.desc()).limit(1))
                    media_result = _rehydrate_cached_media_result(db, cached_run) if cached_run else None
                    if cached_run:
                        media_run.cache = {
                            "hit": True,
                            "source_run_id": cached_run.id,
                            "stage_fingerprints": stage_fingerprints,
                            "stage_hits": list(stage_fingerprints),
                        }
                        media_run.stage = "cache_hit"; media_run.progress = 78; job.progress = max(job.progress, 78); db.commit()

                    asr_options = None
                    if asr_model:
                        asr_settings = asr_model.config or {}
                        asr_options = {
                            "api_key": _media_model_secret(db, asr_model) or "local-runtime",
                            "model": asr_model.model_name,
                            "base_url": asr_model.base_url,
                            "timeout": asr_settings.get("timeout", 300),
                            "max_retries": asr_settings.get("max_retries", asr_settings.get("retry", 2)),
                            "prompt": asr_settings.get("prompt"),
                        }
                    vision_options = None
                    if vision_model:
                        vision_settings = vision_model.config or {}
                        vision_options = {
                            "api_key": _media_model_secret(db, vision_model) or "",
                            "model": vision_model.model_name,
                            "base_url": vision_model.base_url,
                            "timeout": vision_settings.get("timeout", 120),
                            "max_retries": vision_settings.get("max_retries", vision_settings.get("retry", 2)),
                            "prompt": vision_settings.get("prompt"),
                        }
                    if media_result is None:
                        reuse_run = preferred_reuse_run
                        if (
                            reuse_run is None
                            and bool((media_config.get("cache") or {}).get("enabled", True))
                            and not bool((job.input or {}).get("force_media"))
                        ):
                            candidates = list(db.scalars(select(MediaProcessingRun).where(
                                MediaProcessingRun.tenant_id == version.tenant_id,
                                MediaProcessingRun.version_id == version.id,
                                MediaProcessingRun.id != media_run.id,
                                MediaProcessingRun.status == "succeeded",
                                MediaProcessingRun.deleted_at.is_(None),
                            ).order_by(MediaProcessingRun.finished_at.desc()).limit(10)))
                            reuse_run = next(
                                (row for row in candidates if (row.cache or {}).get("stage_fingerprints")),
                                None,
                            )
                        reuse_result = _rehydrate_cached_media_result(db, reuse_run) if reuse_run else None
                        previous_fingerprints = (reuse_run.cache or {}).get("stage_fingerprints", {}) if reuse_run else {}
                        reusable_stages = {
                            stage for stage, value in stage_fingerprints.items()
                            if stage != "timeline" and previous_fingerprints.get(stage) == value
                        }
                        if target_scene_id and reuse_run:
                            # Targeted recovery keeps all immutable upstream
                            # products, but deliberately re-executes OCR/Vision
                            # only for frames in the selected scene.
                            reusable_stages.update({"probe", "asr", "scenes", "frames", "ocr", "vision"})
                        media_result = process_media_file(
                            local_path,
                            policy=media_config,
                            working_directory=Path(temp_dir) / "media-work",
                            asr_options=asr_options,
                            vision_options=vision_options,
                            progress=media_progress,
                            cancelled=media_cancelled,
                            artifact_writer=media_artifact_writer,
                            artifact_reader=media_artifact_reader,
                            reuse_result=reuse_result,
                            reuse_stages=reusable_stages,
                            target_scene_index=target_scene_index,
                        )
                        media_run.cache = {
                            "hit": False,
                            "source_run_id": reuse_run.id if reuse_run else None,
                            "stage_fingerprints": stage_fingerprints,
                            "stage_hits": sorted(
                                stage for stage in reusable_stages
                                if not (target_scene_id and stage in {"ocr", "vision"})
                            ),
                            "target_scene_index": target_scene_index,
                        }
                    media_run.stage = "timeline_fusion"
                    media_run.progress = 82
                    media_run.probe = media_result.get("probe") or {}
                    media_run.warnings = media_result.get("warnings") or []
                    media_run.result = {
                        key: media_result.get(key)
                        for key in (
                            "frame_count", "scene_count", "segment_count", "cloud_frame_count",
                            "transcription_status", "vision_status", "model_version",
                            "transcription_time_seconds",
                        )
                    }
                    _persist_media_result(db, media_run, media_result)
                    db.commit()
                    if media_kind in {"audio", "video"}:
                        transcriber = lambda _path, _kind: media_result
                    else:
                        visual_describer = lambda _path, _kind: media_result
                else:
                    asr_model = _media_model(db, version.tenant_id, "asr", None)
                    if asr_model:
                        asr_secret = _media_model_secret(db, asr_model)

                        def transcriber(media_path: Path, media_type: str) -> dict[str, Any]:
                            _progress(db, job, 35)
                            config = asr_model.config or {}
                            result = transcribe_media(
                                media_path, media_type, api_key=asr_secret or "",
                                model=asr_model.model_name, base_url=asr_model.base_url,
                                timeout=float(config.get("timeout", 300)),
                                max_retries=int(config.get("max_retries", config.get("retry", 2))),
                                language=config.get("language"), prompt=config.get("prompt"),
                            )
                            _progress(db, job, 62); return result
                    vision_model = _media_model(db, version.tenant_id, "vision", None)
                    if vision_model:
                        vision_secret = _media_model_secret(db, vision_model)

                        def visual_describer(visual_path: Path, media_type: str) -> dict[str, Any]:
                            _progress(db, job, 45)
                            config = vision_model.config or {}
                            result = describe_visual(
                                visual_path, media_type, api_key=vision_secret or "",
                                model=vision_model.model_name, base_url=vision_model.base_url,
                                timeout=float(config.get("timeout", 120)),
                                max_retries=int(config.get("max_retries", config.get("retry", 2))),
                                prompt=config.get("prompt"), max_tokens=int(config.get("max_tokens", 700)),
                                keyframe_count=int(config.get("keyframe_count", 3)),
                            )
                            _progress(db, job, 62); return result
                parse_policy = _policy_dict(policy)
                if (job.input or {}).get("skip_archive_media"):
                    parse_policy["skip_archive_media"] = True
                source_attachment = (job.input or {}).get("source_media_attachment")
                if media_result and media_run:
                    parse_policy["media_context"] = {
                        "source_file": version.filename,
                        "document_id": document.id,
                        "version_id": version.id,
                        "processing_run_id": media_run.id,
                        "processing_policy_version": active_media_snapshot.get("version_number"),
                        "processing_policy_hash": active_media_snapshot.get("config_hash"),
                        "media_checksum": version.sha256,
                        "asr_model_config_id": asr_model.id if asr_model else None,
                        "vision_model_config_id": vision_model.id if vision_model else None,
                        "prompt_version": (media_config.get("vision") or {}).get("prompt_version"),
                        "source_path": (source_attachment or {}).get("source_path"),
                        "attachment_parent": (source_attachment or {}).get("parent_snapshot"),
                    }
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
                if media_result:
                    summary["media"] = {
                        "run_id": media_run.id if media_run else None,
                        "policy": active_media_snapshot.get("policy_name"),
                        "policy_version": active_media_snapshot.get("version_number"),
                        "frame_count": media_result.get("frame_count", 0),
                        "scene_count": media_result.get("scene_count", 0),
                        "segment_count": media_result.get("segment_count", 0),
                    }
                    summary.setdefault("warnings", []).extend(media_result.get("warnings") or [])
                _step(db, job.id, "parse", 2, "succeeded", summary)
                _progress(db, job, 70)

                _step(db, job.id, "persist", 3, "running")
                _progress(db, job, 75)
                existing_elements = {
                    row.element_id: row
                    for row in db.scalars(
                        select(ContentElement).where(ContentElement.version_id == version.id)
                    )
                }
                retained_element_ids: set[str] = set()
                for item in elements:
                    retained_element_ids.add(item.element_id)
                    element_row = existing_elements.get(item.element_id)
                    if element_row is None:
                        element_row = ContentElement(
                            tenant_id=version.tenant_id,
                            space_id=document.space_id,
                            document_id=document.id,
                            version_id=version.id,
                            element_id=item.element_id,
                        )
                        db.add(element_row)
                    element_row.element_type = item.element_type
                    element_row.ordinal = item.ordinal
                    element_row.text = item.text
                    element_row.structural_path = item.structural_path
                    element_row.page_number = item.page_number
                    element_row.bbox = item.bbox
                    element_row.element_metadata = item.metadata
                    element_row.scope_tokens = []
                    element_row.deleted_at = None
                # Published chunks may still reference elements while the new
                # knowledge job is being prepared. Preserve referential
                # integrity and mark disappeared elements inactive instead of
                # physically deleting them mid-release.
                for element_id, element_row in existing_elements.items():
                    if element_id not in retained_element_ids:
                        element_row.deleted_at = now()
                version.parse_summary = summary
                version.status = "ready"
                version.error_code = None
                version.error_message = None
                document.current_version_id = version.id
                document.status = "ready"
                if media_run:
                    media_run.stage = "persist"
                    media_run.progress = 90
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
                if media_run:
                    media_run.stage = "completed"
                    media_run.progress = 100
                    component_statuses = {
                        str((media_run.result or {}).get("transcription_status") or ""),
                        str((media_run.result or {}).get("vision_status") or ""),
                    }
                    media_run.status = "partial" if "failed" in component_statuses else "succeeded"
                    media_run.finished_at = now()
                    db.commit()

            job.status = "succeeded"
            job.progress = 100
            media_cache_hit = bool(media_run and (media_run.cache or {}).get("hit"))
            job.result = {
                "version_id": version.id,
                **summary,
                **({"media_cache_hit": media_cache_hit} if media_run else {}),
            }
            job.finished_at = now()
            db.commit()
            source = db.get(SourceConnector, document.source_id) if document.source_id else None
            source_knowledge_enabled = not (
                source is not None
                and source.source_type == "database"
                and (source.config or {}).get("knowledge_index_enabled") is False
            ) and not bool(summary.get("delegated_media_manifest"))
            if settings.knowledge_auto_process and source_knowledge_enabled and not media_cache_hit:
                knowledge_key = (
                    f"knowledge-reprocess:{version.id}:{job.id}"
                    if (job.input or {}).get("reprocess")
                    else f"knowledge:{version.id}"
                )
                process_job = db.scalar(select(Job).where(Job.idempotency_key == knowledge_key))
                if process_job is None:
                    process_job = Job(
                        tenant_id=version.tenant_id,
                        job_type="process_knowledge",
                        idempotency_key=knowledge_key,
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
            if media_run is not None:
                media_run = db.get(MediaProcessingRun, media_run.id)
                if media_run:
                    media_run.status = "cancelled" if isinstance(exc, MediaProcessingCancelled) else "failed"
                    media_run.stage = "cancelled" if isinstance(exc, MediaProcessingCancelled) else "failed"
                    media_run.error_code = "MEDIA_CANCELLED" if isinstance(exc, MediaProcessingCancelled) else "MEDIA_PROCESSING_FAILED"
                    media_run.error_message = message[:4000]
                    media_run.finished_at = now()
            if job is None or version is None:
                return {"status": "failed", "error": message}
            if isinstance(exc, MediaProcessingCancelled):
                version.status = "uploaded"
                version.error_code = None
                version.error_message = None
                if document:
                    document.status = "draft"
                job.status = "cancelled"
                job.error_code = "MEDIA_CANCELLED"
                job.error_message = "用户已取消媒体处理"
                job.finished_at = now()
                db.commit()
                return {"status": "cancelled"}
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


def _source_payload(source: SourceConnector) -> tuple[bytes, str, str, str, list[IngestedPayload]]:
    result = ingest_source(
        source_type=source.source_type,
        source_name=source.name,
        config=source.config or {},
        secret=decrypt_secret(source.secret_encrypted),
    )
    media_items = extract_media_payloads(
        result,
        maximum_items=int((source.config or {}).get("max_media_items_per_sync") or 100),
    )
    return result.body, result.filename, result.content_type, result.title, media_items


def _prepare_source_media_children(
    db,
    *,
    source: SourceConnector,
    parent_job: Job,
    items: list[IngestedPayload],
    parser_policy: ParserPolicy,
) -> tuple[list[dict[str, Any]], list[Job]]:
    """Persist media archive/email members as independently processable docs."""
    results: list[dict[str, Any]] = []
    parse_jobs: list[Job] = []
    for item in items:
        digest = hashlib.sha256(item.body).hexdigest()
        document = db.scalar(select(Document).where(
            Document.tenant_id == source.tenant_id,
            Document.source_id == source.id,
            Document.title == item.title,
            Document.deleted_at.is_(None),
        ))
        if document is None:
            document = Document(
                tenant_id=source.tenant_id,
                space_id=source.space_id,
                source_id=source.id,
                title=item.title,
                status="draft",
            )
            db.add(document); db.flush()
        existing = db.scalar(select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.sha256 == digest,
        ))
        if existing:
            results.append({
                "document_id": document.id, "version_id": existing.id,
                "filename": item.filename, "unchanged": True,
            })
            continue
        media_type = media_type_for(item.filename, item.content_type)
        if media_type not in {"image", "audio", "video"}:
            continue
        requested_override = (parent_job.input or {}).get("media_policy_override") or {}
        configured_override = (source.config or {}).get("media_policy_override") or {}
        media_version, media_snapshot = resolve_media_policy(
            db,
            tenant_id=source.tenant_id,
            media_type=media_type,
            explicit_policy_id=(parent_job.input or {}).get("media_policy_id") or source.media_policy_id,
            source_id=source.id,
            space_id=source.space_id,
            override=requested_override or configured_override,
        )
        require_cloud_confirmation(
            media_snapshot.get("config") or {},
            bool((parent_job.input or {}).get("cloud_processing_confirmed"))
            or bool((source.config or {}).get("media_cloud_processing_confirmed")),
        )
        version_number = (
            db.scalar(select(func.max(DocumentVersion.version_number)).where(
                DocumentVersion.document_id == document.id
            )) or 0
        ) + 1
        object_key = (
            f"{source.tenant_id}/{source.space_id}/{document.id}/{digest}/{item.filename}"
        )
        version = DocumentVersion(
            tenant_id=source.tenant_id,
            document_id=document.id,
            version_number=version_number,
            filename=item.filename,
            content_type=item.content_type,
            size=len(item.body),
            sha256=digest,
            object_key=object_key,
            parser_policy_id=parser_policy.id,
            media_policy_version_id=media_version.id if media_version else None,
            media_policy_snapshot=media_snapshot,
        )
        db.add(version); db.flush()
        with tempfile.TemporaryDirectory(prefix="semantica-source-media-") as temporary_directory:
            local_path = Path(temporary_directory) / item.filename
            local_path.write_bytes(item.body)
            object_storage.put_file(object_key, local_path, item.content_type)
        parse_job = Job(
            tenant_id=source.tenant_id,
            job_type="parse_document",
            idempotency_key=f"parse:{version.id}",
            input={"version_id": version.id, "source_media_attachment": item.metadata},
        )
        db.add(parse_job); db.flush(); parse_jobs.append(parse_job)
        results.append({
            "document_id": document.id, "version_id": version.id,
            "parse_job_id": parse_job.id, "filename": item.filename,
            "unchanged": False,
        })
    return results, parse_jobs


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
            payload, filename, content_type, title, media_items = _source_payload(source)
            digest = hashlib.sha256(payload).hexdigest()
            _step(db, job.id, "fetch", 1, "succeeded", {
                "bytes": len(payload), "sha256": digest,
                "media_attachment_count": len(media_items),
            })
            _progress(db, job, 40)

            _step(db, job.id, "persist", 2, "running")
            _progress(db, job, 50)

            document = db.scalar(
                select(Document).where(
                    Document.tenant_id == source.tenant_id,
                    Document.source_id == source.id,
                    Document.title == title,
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
                policy = db.scalar(
                    select(ParserPolicy).where(
                        ParserPolicy.tenant_id == source.tenant_id,
                        ParserPolicy.is_default.is_(True),
                        ParserPolicy.deleted_at.is_(None),
                    )
                )
                if policy is None:
                    raise ValueError("未配置可用的默认解析策略")
                media_results, media_parse_jobs = _prepare_source_media_children(
                    db, source=source, parent_job=job, items=media_items,
                    parser_policy=policy,
                )
                parent_parse_job = None
                if existing.status in {"failed", "uploaded"}:
                    parent_parse_job = Job(
                        tenant_id=source.tenant_id,
                        job_type="parse_document",
                        idempotency_key=f"source-parse-retry:{existing.id}:{int(time.time() * 1000)}",
                        input={"version_id": existing.id, "skip_archive_media": bool(media_items)},
                    )
                    db.add(parent_parse_job)
                    db.flush()
                db.commit()
                queued_jobs = ([parent_parse_job] if parent_parse_job is not None else []) + media_parse_jobs
                for media_parse_job in queued_jobs:
                    try:
                        parse_version_task.delay(media_parse_job.id)
                    except Exception as dispatch_error:
                        media_parse_job.status = "failed"
                        media_parse_job.error_code = "QUEUE_DISPATCH_FAILED"
                        media_parse_job.error_message = f"{type(dispatch_error).__name__}: {dispatch_error}"[:4000]
                        media_parse_job.finished_at = now()
                        db.commit()
                        if (source.config or {}).get("media_sync_failure_mode") == "fail":
                            raise
                _step(
                    db,
                    job.id,
                    "persist",
                    2,
                    "succeeded",
                    {
                        "version_id": existing.id,
                        "unchanged": not queued_jobs,
                        "media_items": len(media_results),
                    },
                )
                source.last_sync_at = now()
                source.last_sync_status = "fetched" if queued_jobs else "unchanged"
                _update_source_cursor(source, digest, source.last_sync_status)
                job.status = "succeeded"
                job.progress = 100
                job.result = {
                    "document_id": document.id,
                    "version_id": existing.id,
                    "parse_job_id": parent_parse_job.id if parent_parse_job is not None else None,
                    "unchanged": not queued_jobs,
                    "media_documents": media_results,
                    "media_parse_job_ids": [item.id for item in media_parse_jobs],
                    "document_count": 1 + len(media_results),
                }
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
            resolved_media_version_id = None
            resolved_media_snapshot: dict[str, Any] = {}
            source_media_type = media_type_for(filename, content_type)
            if source_media_type in {"image", "audio", "video"}:
                requested_override = (job.input or {}).get("media_policy_override") or {}
                configured_override = (source.config or {}).get("media_policy_override") or {}
                media_version, resolved_media_snapshot = resolve_media_policy(
                    db,
                    tenant_id=source.tenant_id,
                    media_type=source_media_type,
                    explicit_policy_id=(job.input or {}).get("media_policy_id") or source.media_policy_id,
                    source_id=source.id,
                    space_id=source.space_id,
                    override=requested_override or configured_override,
                )
                require_cloud_confirmation(
                    resolved_media_snapshot.get("config") or {},
                    bool((job.input or {}).get("cloud_processing_confirmed"))
                    or bool((source.config or {}).get("media_cloud_processing_confirmed")),
                )
                resolved_media_version_id = media_version.id if media_version else None
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
                media_policy_version_id=resolved_media_version_id,
                media_policy_snapshot=resolved_media_snapshot,
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
                input={"version_id": version.id, "skip_archive_media": bool(media_items)},
            )
            db.add(parse_job)
            db.flush()
            media_results, media_parse_jobs = _prepare_source_media_children(
                db, source=source, parent_job=job, items=media_items,
                parser_policy=policy,
            )
            job.result = {
                "document_id": document.id,
                "version_id": version.id,
                "parse_job_id": parse_job.id,
                "media_documents": media_results,
                "media_parse_job_ids": [item.id for item in media_parse_jobs],
                "document_count": 1 + len(media_results),
            }
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
            dispatched: list[str] = []
            for queued_parse_job in [parse_job, *media_parse_jobs]:
                try:
                    parse_version_task.delay(queued_parse_job.id)
                    dispatched.append(queued_parse_job.id)
                except Exception as dispatch_error:
                    dispatch_message = f"{type(dispatch_error).__name__}: {dispatch_error}"
                    queued_parse_job.status = "failed"
                    queued_parse_job.error_code = "QUEUE_DISPATCH_FAILED"
                    queued_parse_job.error_message = dispatch_message[:4000]
                    queued_parse_job.finished_at = now()
                    db.commit()
                    if queued_parse_job.id == parse_job.id or (source.config or {}).get("media_sync_failure_mode") == "fail":
                        version.status = "failed"
                        version.error_code = "QUEUE_DISPATCH_FAILED"
                        version.error_message = dispatch_message[:4000]
                        document.status = "failed"
                        db.commit()
                        raise RuntimeError(f"解析任务提交失败：{dispatch_error}") from dispatch_error
            _step(db, job.id, "dispatch", 3, "succeeded", {"parse_job_ids": dispatched})

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
        if job.status == "cancelled":
            return {"status": "cancelled"}
        version_id = (job.input or {}).get("version_id")
        version = db.get(DocumentVersion, version_id)
        document = db.get(Document, version.document_id) if version else None
        if (
            version is None
            or document is None
            or version.deleted_at is not None
            or document.deleted_at is not None
            or version.tenant_id != job.tenant_id
        ):
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
        source = db.get(SourceConnector, document.source_id) if document.source_id else None
        (
            is_database_snapshot,
            database_profile_model_enabled,
            generic_semantic_extraction_enabled,
        ) = _database_processing_modes(source)
        # Database rows already have deterministic schema and ontology mapping.
        # Running a general-purpose LLM once per row is both slower and less
        # reliable than the mapping-backed materializer.  Administrators can
        # explicitly opt in for free-text database columns when needed.
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
                            source_span={"start": item.start_index, "end": item.end_index, **(element.element_metadata or {}), **item.metadata},
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
                        row.source_span = {"start": item.start_index, "end": item.end_index, **(element.element_metadata or {}), **item.metadata}
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
            model_analysis_enabled = bool(profile_config.get("enable_model_analysis", True))
            if is_database_snapshot and not database_profile_model_enabled:
                model_analysis_enabled = False
            model_status = "disabled" if not model_analysis_enabled else "not_configured"
            model_error = None
            model_profile: dict[str, Any] = {}
            if is_database_snapshot and not model_analysis_enabled:
                structured_objects = sorted({
                    str((element.element_metadata or {}).get("object_id") or "")
                    for element in elements
                    if (element.element_metadata or {}).get("object_id")
                })
                record_count = sum(element.element_type == "record" for element in elements)
                model_profile = {
                    "summary": (
                        f"{source.name} 的结构化数据同步快照，包含 {len(structured_objects)} 个数据对象、"
                        f"{record_count} 条记录；业务实体和关系由已激活本体映射确定性生成。"
                    ),
                    "classification": "结构化业务数据",
                    "document_type": "数据库快照",
                    "tags": ["结构化数据", "数据库快照"],
                    "keywords": [item.rsplit(".", 1)[-1] for item in structured_objects[:12]],
                    "main_objects": structured_objects[:20],
                    "time_range": {},
                    "quality_issues": [],
                }
            if model_analysis_enabled and profile_model:
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
            if generic_semantic_extraction_enabled and not api_key:
                raise ValueError("语义抽取模型未配置 API Key")
            entity_count = relation_count = event_count = 0
            selected_chunks = (
                chunks[: extraction_policy.max_chunks]
                if generic_semantic_extraction_enabled
                else []
            )
            reused_chunk_count = 0
            model_chunk_count = 0
            extraction_errors: list[dict[str, Any]] = []
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
                try:
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
                except Exception as exc:
                    safe_message = str(exc).replace(api_key, "***")
                    extraction_errors.append({
                        "chunk_id": chunk.chunk_id,
                        "chunk_ordinal": chunk.ordinal,
                        "error_type": type(exc).__name__,
                        "message": safe_message[:500],
                    })
                    _progress(db, job, 20 + round(30 * (index + 1) / max(1, len(selected_chunks))))
                    continue
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
            run.status = "partial" if extraction_errors else "succeeded"
            run.metrics = {
                "chunks": len(selected_chunks),
                "model_chunks": model_chunk_count,
                "reused_chunks": reused_chunk_count,
                "failed_chunks": len(extraction_errors),
                "entities": entity_count,
                "relations": relation_count,
                "events": event_count,
            }
            if not generic_semantic_extraction_enabled:
                run.metrics.update({
                    "skipped": True,
                    "reason": "database_snapshot_uses_deterministic_mapping",
                    "available_chunks": len(chunks),
                })
            if extraction_errors:
                run.metrics["errors"] = extraction_errors
                run.error_message = (
                    f"{len(extraction_errors)} 个片段语义抽取失败；文档仍已进入全文和向量索引，可重试知识加工"
                )
            run.finished_at = now()
            db.commit()
            _step(db, job.id, "semantic_extract", 3, run.status, run.metrics)
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
                "semantic_extraction_status": run.status,
                "semantic_extraction_failed_chunks": len(extraction_errors),
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
            if extraction_errors:
                job.result["warnings"] = [
                    f"{len(extraction_errors)} 个片段的模型语义抽取失败；全文、向量和已成功抽取的图谱知识均已发布"
                ]
            job.finished_at = now()
            curation_batch_id = (job.input or {}).get("curation_batch_id")
            curation_batch = db.get(CurationBatch, curation_batch_id) if curation_batch_id else None
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
            curation_batch_id = (job.input or {}).get("curation_batch_id")
            curation_batch = db.get(CurationBatch, curation_batch_id) if curation_batch_id else None
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
