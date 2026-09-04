from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import get_current_user, require_admin, require_space_permission
from apps.api.schemas import (
    MediaFrameEstimateRequest,
    MediaPolicyCreate,
    MediaPolicyUpdate,
    MediaReprocessRequest,
)
from apps.api.utils import serialize_row
from apps.worker.tasks import parse_version_task
from packages.platform.audit import audit
from packages.platform.config import get_settings
from packages.platform.database import get_db
from packages.platform.media import (
    estimate_frame_count,
    media_policy_snapshot,
    media_type_for,
    normalize_media_policy,
    probe_media,
    require_cloud_confirmation,
    timecode,
    uses_cloud_vision,
)
from packages.platform.media_policy import ensure_policy_version, resolve_media_policy
from packages.platform.model_routing import resolve_model_for_scene
from packages.platform.models import (
    ContentElement,
    Document,
    DocumentVersion,
    Job,
    KnowledgeSpace,
    MediaAudioSegment,
    MediaFrame,
    MediaParsingPolicy,
    MediaParsingPolicyVersion,
    MediaProcessingRun,
    MediaScene,
    ModelConfig,
    SourceConnector,
    User,
)
from packages.platform.storage import object_storage
from packages.semantica_adapter.file_safety import UnsafeFileError, validate_file_identity


router = APIRouter()
settings = get_settings()


def _tenant_row(db: Session, model, row_id: str, tenant_id: str, label: str):
    row = db.get(model, row_id)
    if row is None or row.deleted_at is not None or row.tenant_id != tenant_id:
        raise HTTPException(404, f"{label}不存在")
    return row


def _policy_data(db: Session, row: MediaParsingPolicy) -> dict[str, Any]:
    data = serialize_row(row)
    data["usage"] = {
        "spaces": db.scalar(
            select(func.count()).select_from(KnowledgeSpace).where(
                KnowledgeSpace.media_policy_id == row.id, KnowledgeSpace.deleted_at.is_(None)
            )
        ) or 0,
        "sources": db.scalar(
            select(func.count()).select_from(SourceConnector).where(
                SourceConnector.media_policy_id == row.id, SourceConnector.deleted_at.is_(None)
            )
        ) or 0,
        "versions": db.scalar(
            select(func.count()).select_from(MediaParsingPolicyVersion).where(
                MediaParsingPolicyVersion.policy_id == row.id,
                MediaParsingPolicyVersion.deleted_at.is_(None),
            )
        ) or 0,
        "documents": db.scalar(
            select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.media_policy_version_id.in_(
                    select(MediaParsingPolicyVersion.id).where(
                        MediaParsingPolicyVersion.policy_id == row.id
                    )
                )
            )
        ) or 0,
    }
    return data


def _new_policy_version(
    db: Session, row: MediaParsingPolicy, actor_id: str
) -> MediaParsingPolicyVersion:
    snapshot = media_policy_snapshot(
        policy_id=row.id,
        policy_version_id=None,
        policy_name=row.name,
        version_number=row.current_version,
        applicable_media_types=row.applicable_media_types or [],
        config=row.config or {},
    )
    version = MediaParsingPolicyVersion(
        tenant_id=row.tenant_id,
        policy_id=row.id,
        version_number=row.current_version,
        snapshot=snapshot,
        config_hash=snapshot["config_hash"],
        created_by=actor_id,
    )
    db.add(version)
    db.flush()
    snapshot["policy_version_id"] = version.id
    version.snapshot = snapshot
    return version


@router.get("/media-policies")
def list_media_policies(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return [
        _policy_data(db, row)
        for row in db.scalars(
            select(MediaParsingPolicy).where(
                MediaParsingPolicy.tenant_id == user.tenant_id,
                MediaParsingPolicy.deleted_at.is_(None),
            ).order_by(MediaParsingPolicy.is_default.desc(), MediaParsingPolicy.name)
        )
    ]


@router.post("/media-policies")
def create_media_policy(
    payload: MediaPolicyCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.is_default:
        db.query(MediaParsingPolicy).filter(
            MediaParsingPolicy.tenant_id == admin.tenant_id,
            MediaParsingPolicy.deleted_at.is_(None),
        ).update({"is_default": False})
    row = MediaParsingPolicy(tenant_id=admin.tenant_id, **payload.model_dump())
    db.add(row)
    try:
        db.flush()
        _new_policy_version(db, row, admin.id)
        audit(db, admin.tenant_id, admin.id, "media_policy.create", "media_policy", row.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "媒体解析策略名称已存在") from exc
    return _policy_data(db, row)


@router.get("/media-policies/{row_id}")
def get_media_policy(
    row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return _policy_data(db, _tenant_row(db, MediaParsingPolicy, row_id, user.tenant_id, "媒体解析策略"))


@router.put("/media-policies/{row_id}")
def update_media_policy(
    row_id: str,
    payload: MediaPolicyUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _tenant_row(db, MediaParsingPolicy, row_id, admin.tenant_id, "媒体解析策略")
    values = payload.model_dump(exclude_unset=True)
    if values.get("is_default"):
        db.query(MediaParsingPolicy).filter(
            MediaParsingPolicy.tenant_id == admin.tenant_id,
            MediaParsingPolicy.id != row.id,
            MediaParsingPolicy.deleted_at.is_(None),
        ).update({"is_default": False})
    next_name = values.get("name", row.name)
    next_types = values.get("applicable_media_types", row.applicable_media_types)
    next_config = values.get("config", row.config)
    candidate = media_policy_snapshot(
        policy_id=row.id,
        policy_version_id=None,
        policy_name=next_name,
        version_number=row.current_version + 1,
        applicable_media_types=next_types or [],
        config=next_config or {},
    )
    current = ensure_policy_version(db, row, actor_id=admin.id)
    functional_change = candidate["config_hash"] != current.config_hash
    for key in ("name", "description", "applicable_media_types", "config", "enabled", "is_default"):
        if key in values:
            setattr(row, key, values[key])
    if functional_change:
        row.current_version += 1
        _new_policy_version(db, row, admin.id)
    audit(
        db, admin.tenant_id, admin.id, "media_policy.update", "media_policy", row.id,
        {"version_created": functional_change, "version": row.current_version},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "策略名称或版本与现有记录冲突") from exc
    return _policy_data(db, row)


@router.post("/media-policies/{row_id}/clone")
def clone_media_policy(
    row_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    source = _tenant_row(db, MediaParsingPolicy, row_id, admin.tenant_id, "媒体解析策略")
    base_name = f"{source.name} 副本"
    name, counter = base_name, 2
    names = set(db.scalars(select(MediaParsingPolicy.name).where(MediaParsingPolicy.tenant_id == admin.tenant_id)))
    while name in names:
        name, counter = f"{base_name} {counter}", counter + 1
    row = MediaParsingPolicy(
        tenant_id=admin.tenant_id,
        name=name,
        description=source.description,
        applicable_media_types=list(source.applicable_media_types or []),
        config=dict(source.config or {}),
        enabled=True,
        is_default=False,
    )
    db.add(row); db.flush(); _new_policy_version(db, row, admin.id)
    audit(db, admin.tenant_id, admin.id, "media_policy.clone", "media_policy", row.id, {"source_id": source.id})
    db.commit()
    return _policy_data(db, row)


@router.get("/media-policies/{row_id}/versions")
def list_media_policy_versions(
    row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    row = _tenant_row(db, MediaParsingPolicy, row_id, user.tenant_id, "媒体解析策略")
    return [
        serialize_row(item)
        for item in db.scalars(
            select(MediaParsingPolicyVersion).where(
                MediaParsingPolicyVersion.policy_id == row.id,
                MediaParsingPolicyVersion.deleted_at.is_(None),
            ).order_by(MediaParsingPolicyVersion.version_number.desc())
        )
    ]


@router.get("/media-policies/{row_id}/usage")
def get_media_policy_usage(
    row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    row = _tenant_row(db, MediaParsingPolicy, row_id, user.tenant_id, "媒体解析策略")
    return _policy_data(db, row)["usage"]


@router.post("/media-policies/{row_id}/validate")
def validate_media_policy(
    row_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    row = _tenant_row(db, MediaParsingPolicy, row_id, user.tenant_id, "媒体解析策略")
    try:
        config = normalize_media_policy(row.config or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    checks: list[dict[str, Any]] = []
    for section, kind in (("asr", "asr"), ("vision", "vision")):
        item = config[section]
        if not item["enabled"]:
            checks.append({"name": section, "status": "disabled", "message": "策略未启用"})
            continue
        configured_id = item.get("model_config_id")
        model = db.get(ModelConfig, configured_id) if configured_id else None
        if model is None and not configured_id:
            scene = "speech_recognition" if kind == "asr" else "vision_understanding"
            model = resolve_model_for_scene(db, user.tenant_id, scene).model
        valid = bool(model and model.tenant_id == user.tenant_id and model.enabled and model.deleted_at is None and model.model_kind == kind)
        checks.append({
            "name": section,
            "status": "ready" if valid else "not_configured",
            "message": f"将使用模型：{model.name}" if valid else f"未配置可用的{'语音识别' if kind == 'asr' else '视觉'}模型",
            "model_config_id": model.id if valid else None,
        })
    if config["vision"]["enabled"] and config["vision"]["execution"] == "cloud" and not config["cloud_processing_allowed"]:
        checks.append({"name": "cloud_permission", "status": "failed", "message": "云视觉已启用但策略禁止云处理"})
    checks.append({
        "name": "frame_budget",
        "status": "ready",
        "message": f"最长视频按当前配置最多抽取 {config['frame']['max_frames']} 帧",
        "estimate": estimate_frame_count(config["max_duration_seconds"], config),
    })
    ok = all(item["status"] in {"ready", "disabled"} for item in checks)
    audit(db, user.tenant_id, user.id, "media_policy.validate", "media_policy", row.id, {"ok": ok})
    db.commit()
    return {"ok": ok, "policy_id": row.id, "version": row.current_version, "checks": checks}


@router.delete("/media-policies/{row_id}")
def delete_media_policy(
    row_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    row = _tenant_row(db, MediaParsingPolicy, row_id, admin.tenant_id, "媒体解析策略")
    usage = _policy_data(db, row)["usage"]
    if usage["spaces"] or usage["sources"]:
        raise HTTPException(409, "该策略仍被知识空间或数据源使用，请先解除绑定")
    if row.is_default:
        raise HTTPException(409, "默认媒体解析策略不能删除，请先设置其他默认策略")
    row.enabled = False
    row.deleted_at = datetime.now(timezone.utc)
    audit(db, admin.tenant_id, admin.id, "media_policy.delete", "media_policy", row.id)
    db.commit()
    return {"ok": True, "historical_document_versions": usage["documents"]}


def _resolve_for_request(
    db: Session,
    user: User,
    media_type: str,
    policy_id: str | None,
    override: dict[str, Any],
) -> dict[str, Any]:
    try:
        _, snapshot = resolve_media_policy(
            db,
            tenant_id=user.tenant_id,
            media_type=media_type,
            explicit_policy_id=policy_id,
            override=override,
            actor_id=user.id,
        )
        return snapshot
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/media/estimate-frames")
def estimate_media_frames(
    payload: MediaFrameEstimateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    snapshot = _resolve_for_request(db, user, "video", payload.media_policy_id, payload.override)
    return {**estimate_frame_count(payload.duration_seconds, snapshot["config"]), "policy": {
        "id": snapshot["policy_id"], "name": snapshot["policy_name"],
        "version": snapshot["version_number"], "config_hash": snapshot["config_hash"],
    }}


@router.post("/media/probe")
async def probe_uploaded_media(
    file: UploadFile = File(...),
    media_policy_id: str | None = Form(None),
    media_policy_override: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filename = Path(file.filename or "media.bin").name
    media_type = media_type_for(filename, file.content_type)
    if media_type not in {"image", "audio", "video"}:
        raise HTTPException(415, "请选择图片、音频或视频文件")
    try:
        override = json.loads(media_policy_override) if media_policy_override else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "单次媒体策略覆盖不是有效 JSON") from exc
    limit = {
        "image": settings.max_image_upload_bytes,
        "audio": settings.max_audio_upload_bytes,
        "video": settings.max_video_upload_bytes,
    }[media_type]
    with tempfile.NamedTemporaryFile(prefix="media-probe-", suffix=Path(filename).suffix, delete=False) as temporary:
        path = Path(temporary.name)
        size = 0
        try:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "文件超过该媒介类型上传限制")
                temporary.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
    try:
        validate_file_identity(path, file.content_type)
        probe = probe_media(path)
        snapshot = _resolve_for_request(db, user, media_type, media_policy_id, override)
        if probe["duration_seconds"] > snapshot["config"]["max_duration_seconds"]:
            raise HTTPException(413, "媒体时长超过所选策略上限")
        estimate = estimate_frame_count(probe["duration_seconds"], snapshot["config"]) if media_type == "video" else None
        return {
            "filename": filename,
            "media_type": media_type,
            "probe": probe,
            "frame_estimate": estimate,
            "policy": {
                "id": snapshot["policy_id"], "name": snapshot["policy_name"],
                "version": snapshot["version_number"], "config_hash": snapshot["config_hash"],
                "processing_mode": snapshot["config"]["processing_mode"],
                "cloud_processing_allowed": snapshot["config"]["cloud_processing_allowed"],
                "cloud_confirmation_mode": snapshot["config"]["cloud_confirmation_mode"],
                "uses_cloud_vision": uses_cloud_vision(snapshot["config"]),
            },
        }
    except UnsafeFileError as exc:
        raise HTTPException(415, str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


def _document_version(
    db: Session, user: User, document_id: str
) -> tuple[Document, DocumentVersion]:
    document = _tenant_row(db, Document, document_id, user.tenant_id, "文档")
    require_space_permission(db, user, document.space_id, "read")
    if not document.current_version_id:
        raise HTTPException(404, "文档尚无可用版本")
    version = db.get(DocumentVersion, document.current_version_id)
    if version is None:
        raise HTTPException(404, "文档版本不存在")
    return document, version


def _latest_run(db: Session, version_id: str) -> MediaProcessingRun | None:
    return db.scalar(
        select(MediaProcessingRun).where(
            MediaProcessingRun.version_id == version_id,
            MediaProcessingRun.deleted_at.is_(None),
        ).order_by(MediaProcessingRun.created_at.desc()).limit(1)
    )


def _latest_result_run(db: Session, version_id: str) -> MediaProcessingRun | None:
    return db.scalar(
        select(MediaProcessingRun).where(
            MediaProcessingRun.version_id == version_id,
            MediaProcessingRun.status.in_(["succeeded", "partial"]),
            MediaProcessingRun.deleted_at.is_(None),
        ).order_by(MediaProcessingRun.finished_at.desc()).limit(1)
    ) or _latest_run(db, version_id)


def _version_for_user(
    db: Session, user: User, version_id: str
) -> tuple[Document, DocumentVersion]:
    version = db.get(DocumentVersion, version_id)
    if version is None or version.deleted_at is not None or version.tenant_id != user.tenant_id:
        raise HTTPException(404, "文档版本不存在")
    document = _tenant_row(db, Document, version.document_id, user.tenant_id, "文档")
    require_space_permission(db, user, document.space_id, "read")
    return document, version


def _timeline_rows(db: Session, version: DocumentVersion) -> list[dict[str, Any]]:
    types = ["transcript_segment", "speaker_turn", "audio_event", "keyframe_ocr", "visual_scene", "media_chapter", "media_summary"]
    rows = list(db.scalars(select(ContentElement).where(
        ContentElement.version_id == version.id,
        ContentElement.element_type.in_(types),
        ContentElement.deleted_at.is_(None),
    ).order_by(ContentElement.ordinal)))
    return [
        {
            **serialize_row(row),
            "time_start": (row.element_metadata or {}).get("time_start"),
            "time_end": (row.element_metadata or {}).get("time_end"),
            "time_label": f"{timecode((row.element_metadata or {}).get('time_start'))}–{timecode((row.element_metadata or {}).get('time_end'))}",
        }
        for row in rows
    ]


@router.get("/documents/{document_id}/media-profile")
def get_media_profile(
    document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    document, version = _document_version(db, user, document_id)
    media_type = media_type_for(version.filename, version.content_type)
    if media_type not in {"image", "audio", "video"}:
        raise HTTPException(404, "该文档不是媒体文件")
    run = _latest_run(db, version.id)
    content_run = _latest_result_run(db, version.id)
    model_run = run or content_run
    asr_model = db.get(ModelConfig, model_run.asr_model_config_id) if model_run and model_run.asr_model_config_id else None
    vision_model = db.get(ModelConfig, model_run.vision_model_config_id) if model_run and model_run.vision_model_config_id else None
    def model_summary(row: ModelConfig | None) -> dict[str, Any] | None:
        if row is None or row.tenant_id != user.tenant_id:
            return None
        return {"id": row.id, "name": row.name, "provider": row.provider, "model_name": row.model_name}
    return {
        "document": {"id": document.id, "title": document.title, "space_id": document.space_id},
        "version": serialize_row(version),
        "media_type": media_type,
        "content_url": f"/api/v1/documents/{document.id}/media-content",
        "run": serialize_row(run) if run else None,
        "content_run_id": content_run.id if content_run else None,
        "policy": (model_run.policy_snapshot if model_run else version.media_policy_snapshot) or {},
        "models": {"asr": model_summary(asr_model), "vision": model_summary(vision_model)},
        "cloud_processing": {
            "allowed": bool((((model_run.policy_snapshot if model_run else version.media_policy_snapshot) or {}).get("config") or {}).get("cloud_processing_allowed")),
            "frame_count": int(((content_run.result or {}).get("cloud_frame_count") or 0) if content_run else 0),
        },
        "counts": {
            "segments": db.scalar(select(func.count()).select_from(MediaAudioSegment).where(MediaAudioSegment.run_id == content_run.id)) if content_run else 0,
            "scenes": db.scalar(select(func.count()).select_from(MediaScene).where(MediaScene.run_id == content_run.id)) if content_run else 0,
            "frames": db.scalar(select(func.count()).select_from(MediaFrame).where(MediaFrame.run_id == content_run.id)) if content_run else 0,
        },
    }


@router.get("/documents/{document_id}/timeline")
def get_media_timeline(
    document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _, version = _document_version(db, user, document_id)
    return _timeline_rows(db, version)


@router.get("/document-versions/{version_id}/media-timeline")
def get_version_media_timeline(
    version_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _, version = _version_for_user(db, user, version_id)
    return _timeline_rows(db, version)


@router.get("/documents/{document_id}/transcript")
def get_media_transcript(
    document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _, version = _document_version(db, user, document_id)
    run = _latest_result_run(db, version.id)
    if run is None:
        return []
    return [serialize_row(row) for row in db.scalars(select(MediaAudioSegment).where(
        MediaAudioSegment.run_id == run.id, MediaAudioSegment.deleted_at.is_(None)
    ).order_by(MediaAudioSegment.segment_index))]


@router.get("/documents/{document_id}/scenes")
def get_media_scenes(
    document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _, version = _document_version(db, user, document_id); run = _latest_result_run(db, version.id)
    if run is None: return []
    return [serialize_row(row) for row in db.scalars(select(MediaScene).where(
        MediaScene.run_id == run.id, MediaScene.deleted_at.is_(None)
    ).order_by(MediaScene.scene_index))]


@router.get("/documents/{document_id}/frames")
def get_media_frames(
    document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _, version = _document_version(db, user, document_id); run = _latest_result_run(db, version.id)
    if run is None: return []
    return [
        {
            **serialize_row(row),
            # Keep the former summary alias for already deployed frontends;
            # scene_summary is the versioned structured contract.
            "vision_result": {
                **(row.vision_result or {}),
                "summary": (row.vision_result or {}).get("scene_summary", ""),
            },
            "thumbnail_url": f"/api/v1/media/frames/{row.id}/thumbnail",
            "content_url": f"/api/v1/media/frames/{row.id}/content",
        }
        for row in db.scalars(select(MediaFrame).where(
            MediaFrame.run_id == run.id, MediaFrame.deleted_at.is_(None)
        ).order_by(MediaFrame.frame_index))
    ]


@router.get("/documents/{document_id}/processing-runs")
def get_media_runs(
    document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    document = _tenant_row(db, Document, document_id, user.tenant_id, "文档")
    require_space_permission(db, user, document.space_id, "read")
    return [serialize_row(row) for row in db.scalars(select(MediaProcessingRun).where(
        MediaProcessingRun.document_id == document.id, MediaProcessingRun.deleted_at.is_(None)
    ).order_by(MediaProcessingRun.created_at.desc()))]


@router.post("/documents/{document_id}/reprocess-media")
def reprocess_document_media(
    document_id: str,
    payload: MediaReprocessRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document, version = _document_version(db, user, document_id)
    require_space_permission(db, user, document.space_id, "write")
    media_type = media_type_for(version.filename, version.content_type)
    if media_type not in {"image", "audio", "video"}:
        raise HTTPException(409, "该文档不是可重新处理的媒体文件")
    try:
        policy_version, snapshot = resolve_media_policy(
            db,
            tenant_id=user.tenant_id,
            media_type=media_type,
            explicit_policy_id=payload.media_policy_id,
            space_id=document.space_id,
            override=payload.override,
            actor_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        require_cloud_confirmation(snapshot["config"], payload.cloud_processing_confirmed)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    job = Job(
        tenant_id=user.tenant_id,
        job_type="parse_document",
        idempotency_key=f"media-reprocess:{version.id}:{int(time.time() * 1000)}",
        input={
            "version_id": version.id,
            "force_media": bool(payload.bypass_cache),
            "media_policy_version_id": policy_version.id if policy_version else None,
            "media_policy_snapshot": snapshot,
            "reprocess": True,
        },
    )
    db.add(job)
    audit(
        db,
        user.tenant_id,
        user.id,
        "media_document.reprocess",
        "document_version",
        version.id,
        {"policy_version_id": policy_version.id if policy_version else None, "bypass_cache": payload.bypass_cache},
    )
    db.commit()
    try:
        parse_version_task.delay(job.id)
    except Exception as exc:
        job.status = "failed"
        job.error_code = "QUEUE_DISPATCH_FAILED"
        job.error_message = f"{type(exc).__name__}: {exc}"[:4000]
        db.commit()
        raise HTTPException(503, "媒体重新处理任务暂时无法入队") from exc
    return {"job": serialize_row(job), "policy_snapshot": snapshot}


def _frame_for_user(db: Session, user: User, frame_id: str) -> MediaFrame:
    frame = _tenant_row(db, MediaFrame, frame_id, user.tenant_id, "关键帧")
    run = _tenant_row(db, MediaProcessingRun, frame.run_id, user.tenant_id, "媒体处理记录")
    require_space_permission(db, user, run.space_id, "read")
    return frame


def _scene_for_user(db: Session, user: User, scene_id: str) -> tuple[MediaScene, MediaProcessingRun]:
    scene = _tenant_row(db, MediaScene, scene_id, user.tenant_id, "媒体场景")
    run = _tenant_row(db, MediaProcessingRun, scene.run_id, user.tenant_id, "媒体处理记录")
    require_space_permission(db, user, run.space_id, "read")
    return scene, run


@router.get("/media-scenes/{scene_id}")
def get_media_scene(
    scene_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    scene, _ = _scene_for_user(db, user, scene_id)
    frames = list(db.scalars(select(MediaFrame).where(
        MediaFrame.scene_id == scene.id, MediaFrame.deleted_at.is_(None)
    ).order_by(MediaFrame.frame_index)))
    return {
        **serialize_row(scene),
        "duration_seconds": max(0.0, scene.time_end - scene.time_start),
        "frames": [{
            **serialize_row(frame),
            "thumbnail_url": f"/api/v1/media/frames/{frame.id}/thumbnail",
        } for frame in frames],
    }


@router.post("/media-scenes/{scene_id}/retry")
def retry_media_scene(
    scene_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    scene, run = _scene_for_user(db, user, scene_id)
    require_space_permission(db, user, run.space_id, "write")
    job = Job(
        tenant_id=user.tenant_id,
        job_type="parse_document",
        idempotency_key=f"media-scene-retry:{scene.id}:{int(time.time() * 1000)}",
        input={
            "version_id": run.version_id,
            "media_policy_version_id": run.policy_version_id,
            "media_policy_snapshot": run.policy_snapshot,
            "target_scene_id": scene.id,
            "reprocess": True,
        },
    )
    db.add(job)
    audit(db, user.tenant_id, user.id, "media_scene.retry", "media_scene", scene.id, {"run_id": run.id})
    db.commit()
    try:
        parse_version_task.delay(job.id)
    except Exception as exc:
        job.status = "failed"
        job.error_code = "QUEUE_DISPATCH_FAILED"
        job.error_message = f"{type(exc).__name__}: {exc}"[:4000]
        db.commit()
        raise HTTPException(503, "场景重试任务暂时无法入队") from exc
    return {"job": serialize_row(job), "scene_id": scene.id}


@router.get("/media-frames/{frame_id}")
def get_media_frame(
    frame_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    frame = _frame_for_user(db, user, frame_id)
    return {
        **serialize_row(frame),
        "thumbnail_url": f"/api/v1/media/frames/{frame.id}/thumbnail",
        "content_url": f"/api/v1/media/frames/{frame.id}/content",
    }


@router.get("/media/frames/{frame_id}/thumbnail")
def get_frame_thumbnail(
    frame_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    frame = _frame_for_user(db, user, frame_id)
    return Response(object_storage.get_bytes(frame.thumbnail_key), media_type="image/jpeg")


@router.get("/media/frames/{frame_id}/content")
def get_frame_content(
    frame_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    frame = _frame_for_user(db, user, frame_id)
    return Response(object_storage.get_bytes(frame.object_key), media_type="image/jpeg")


@router.get("/documents/{document_id}/media-content")
def get_media_content(
    document_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, version = _document_version(db, user, document_id)
    total = object_storage.stat_size(version.object_key)
    range_header = request.headers.get("range")
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(total)}
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match:
            raise HTTPException(416, "无效的媒体 Range", headers={"Content-Range": f"bytes */{total}"})
        first, last = match.groups()
        if not first and not last:
            raise HTTPException(416, "无效的媒体 Range", headers={"Content-Range": f"bytes */{total}"})
        if not first:
            suffix_length = min(int(last), total)
            start, end = total - suffix_length, total - 1
        else:
            start, end = int(first), int(last) if last else total - 1
        if start >= total or end < start:
            raise HTTPException(416, "媒体 Range 超出范围", headers={"Content-Range": f"bytes */{total}"})
        end = min(end, total - 1)
        length = end - start + 1
        headers.update({"Content-Range": f"bytes {start}-{end}/{total}", "Content-Length": str(length)})
        return StreamingResponse(
            object_storage.iter_bytes(version.object_key, offset=start, length=length),
            status_code=206,
            media_type=version.content_type,
            headers=headers,
        )
    return StreamingResponse(
        object_storage.iter_bytes(version.object_key),
        media_type=version.content_type,
        headers=headers,
    )


@router.post("/media/runs/{run_id}/cancel")
def cancel_media_run(
    run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    run = _tenant_row(db, MediaProcessingRun, run_id, user.tenant_id, "媒体处理记录")
    require_space_permission(db, user, run.space_id, "write")
    if run.status not in {"queued", "running"}:
        raise HTTPException(409, "该处理任务已结束")
    run.cancel_requested_at = datetime.now(timezone.utc)
    run.stage = "cancel_requested"
    if run.job_id:
        job = db.get(Job, run.job_id)
        if job and job.status in {"queued", "running"}:
            job.result = {**(job.result or {}), "cancel_requested": True}
    audit(db, user.tenant_id, user.id, "media_run.cancel", "media_processing_run", run.id)
    db.commit()
    return serialize_row(run)


@router.post("/media-processing-runs/{run_id}/cancel")
def cancel_media_processing_run_alias(
    run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return cancel_media_run(run_id, user, db)


@router.post("/media/runs/{run_id}/retry")
def retry_media_run(
    run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    run = _tenant_row(db, MediaProcessingRun, run_id, user.tenant_id, "媒体处理记录")
    require_space_permission(db, user, run.space_id, "write")
    if run.status in {"queued", "running"}:
        raise HTTPException(409, "媒体处理仍在进行")
    job = Job(
        tenant_id=user.tenant_id,
        job_type="parse_document",
        idempotency_key=f"media-retry:{run.version_id}:{int(time.time() * 1000)}",
        input={
            "version_id": run.version_id,
            "force_media": True,
            "retry_run_id": run.id,
            "media_policy_version_id": run.policy_version_id,
            "media_policy_snapshot": run.policy_snapshot,
            "reprocess": True,
        },
    )
    db.add(job)
    audit(db, user.tenant_id, user.id, "media_run.retry", "media_processing_run", run.id)
    db.commit()
    try:
        parse_version_task.delay(job.id)
    except Exception as exc:
        job.status = "failed"; job.error_code = "QUEUE_DISPATCH_FAILED"; job.error_message = f"{type(exc).__name__}: {exc}"[:4000]
        db.commit()
        raise HTTPException(503, "媒体重试任务暂时无法入队") from exc
    return serialize_row(job)


@router.post("/media-processing-runs/{run_id}/retry")
def retry_media_processing_run_alias(
    run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return retry_media_run(run_id, user, db)
