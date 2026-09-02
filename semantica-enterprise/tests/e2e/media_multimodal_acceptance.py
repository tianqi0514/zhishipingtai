#!/usr/bin/env python3
"""Live multimodal acceptance against the Docker deployment.

This script performs real API, queue, local SenseVoice/FunASR, OCR, Kimi
vision, persistence, media streaming, search and cache checks.  It never reads
or prints model credentials.  The created, clearly named acceptance space is
left available for browser inspection.
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from minio import Minio


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "media-generated"
API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")


class Platform:
    def __init__(self) -> None:
        self.http = httpx.Client(base_url=API, timeout=httpx.Timeout(60, read=900))
        login = self.call("POST", "/auth/login", json={"username": USERNAME, "password": PASSWORD})
        self.http.headers["Authorization"] = f"Bearer {login['access_token']}"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.http.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:1200]}")
        if not response.content:
            return {}
        return response.json()

    def wait_job(self, job_id: str, *, timeout: int = 1800) -> dict[str, Any]:
        deadline, last = time.monotonic() + timeout, {}
        while time.monotonic() < deadline:
            last = self.call("GET", f"/jobs/{job_id}")
            if last.get("status") in {"succeeded", "failed"}:
                if last["status"] != "succeeded":
                    raise RuntimeError(json.dumps(last, ensure_ascii=False)[:3000])
                return last
            time.sleep(1.5)
        raise TimeoutError(f"job did not finish: {last}")

    def wait_knowledge(self, version_id: str, *, timeout: int = 1800) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            candidate = next(
                (
                    job for job in self.call("GET", "/jobs")
                    if job.get("job_type") == "process_knowledge"
                    and (job.get("input") or {}).get("version_id") == version_id
                ),
                None,
            )
            if candidate:
                return self.wait_job(candidate["id"], timeout=max(1, int(deadline - time.monotonic())))
            time.sleep(1)
        raise TimeoutError(f"knowledge job was not created for {version_id}")

    def upload(
        self, *, space_id: str, policy_id: str, path: Path,
        wait_for_knowledge: bool = True,
    ) -> dict[str, Any]:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as stream:
            uploaded = self.call(
                "POST", "/documents/upload",
                data={
                    "space_id": space_id,
                    "media_policy_id": policy_id,
                    "cloud_processing_confirmed": "true",
                    "frame_budget_confirmed": "true",
                },
                files={"file": (path.name, stream, content_type)},
            )
        self.wait_job(uploaded["job"]["id"])
        if wait_for_knowledge:
            self.wait_knowledge(uploaded["version"]["id"])
        return uploaded


def require_model(models: list[dict[str, Any]], kind: str, provider: str | None = None) -> dict[str, Any]:
    candidates = [
        row for row in models
        if row.get("model_kind") == kind and row.get("enabled")
        and (provider is None or row.get("provider") == provider)
    ]
    if not candidates:
        raise RuntimeError(f"缺少启用的 {kind} 模型配置")
    return next((row for row in candidates if row.get("is_default")), candidates[0])


def main() -> None:
    manifest_path = FIXTURES / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("请先运行 tests/fixtures/generate_media_acceptance.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    platform = Platform()
    models = platform.call("GET", "/model-configs")
    asr_model = require_model(models, "asr", "openai_compatible")
    vision_model = require_model(models, "vision")

    model_checks = {
        "asr": platform.call("POST", f"/model-configs/{asr_model['id']}/test"),
        "vision": platform.call("POST", f"/model-configs/{vision_model['id']}/test"),
    }
    if any(item.get("status") != "success" for item in model_checks.values()):
        raise RuntimeError(f"模型连接测试失败：{model_checks}")

    suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    resume_space_id = os.getenv("MEDIA_RESUME_SPACE_ID", "").strip()
    policy = platform.call(
        "POST", "/media-policies",
        json={
            "name": f"多模态真实验收策略 {suffix}",
            "description": "真实 ASR、OCR、Kimi 视觉与时间引用验收",
            "applicable_media_types": ["image", "audio", "video"],
            "config": {
                "processing_mode": "hybrid",
                "cloud_processing_allowed": True,
                "cloud_confirmation_mode": "per_upload",
                "failure_mode": "fail",
                "video": {"extract_audio_track": True, "asr_enabled": True, "scene_detection_enabled": True},
                "frame": {
                    "mode": "scene", "scene_threshold": 0.2,
                    "minimum_scene_seconds": 0.5, "maximum_scene_seconds": 30,
                    "scene_positions": ["middle"], "frames_per_scene": 1,
                    "include_first": True, "include_last": True,
                    "max_frames": 5, "max_image_edge": 1280,
                    "perceptual_hash_enabled": True, "perceptual_hash_distance": 2,
                },
                "ocr": {"enabled": True, "language": "chi_sim+eng", "minimum_confidence": 20},
                "asr": {
                    "enabled": True, "model_config_id": asr_model["id"], "language": "zh",
                    "minimum_speech_seconds": 0.2, "silence_policy": "metadata_only",
                },
                "vision": {
                    "enabled": True, "model_config_id": vision_model["id"], "execution": "cloud",
                    "batch_size": 1, "concurrency": 1, "timeout_seconds": 180,
                    "max_tokens": 1000, "prompt_version": "media-visible-facts-v1",
                },
            },
            "enabled": True,
        },
    )
    if resume_space_id:
        # Resume only skips fixture creation/upload. It is intentionally an
        # explicit operator action and still revalidates every downstream API.
        space = next(row for row in platform.call("GET", "/spaces") if row["id"] == resume_space_id)
        platform.call("DELETE", f"/media-policies/{policy['id']}")
        policy = platform.call("GET", f"/media-policies/{space['media_policy_id']}")
    else:
        validation = platform.call("POST", f"/media-policies/{policy['id']}/validate")
        if not validation.get("ok"):
            raise RuntimeError(f"媒体策略校验失败：{validation}")
        cloned = platform.call("POST", f"/media-policies/{policy['id']}/clone")
        platform.call("PUT", f"/media-policies/{cloned['id']}", json={"description": "CRUD 验证副本"})
        platform.call("DELETE", f"/media-policies/{cloned['id']}")
        space = platform.call(
            "POST", "/spaces",
            json={
                "code": f"media-acceptance-{suffix}",
                "name": f"音视频验收数据集 {suffix}",
                "description": "可在文档资产中查看真实音视频解析、时间线和引用",
                "media_policy_id": policy["id"],
            },
        )
    video_path = FIXTURES / manifest["video"]["file"]
    with video_path.open("rb") as stream:
        probe = platform.call(
            "POST", "/media/probe",
            data={"media_policy_id": policy["id"]},
            files={"file": (video_path.name, stream, "video/mp4")},
        )
    if probe.get("media_type") != "video" or not probe.get("frame_estimate"):
        raise RuntimeError(f"视频探测不完整：{probe}")

    if resume_space_id:
        documents = platform.call("GET", f"/documents?space_id={space['id']}")
        audio_document = next(row for row in documents if row["title"] == "chinese-meeting.wav")
        video_document = next(row for row in documents if row["title"] == video_path.name)
        audio = {"document": audio_document}
        video = {"document": video_document}
        platform.wait_knowledge(audio_document["current_version_id"])
    else:
        audio = platform.upload(space_id=space["id"], policy_id=policy["id"], path=FIXTURES / "chinese-meeting.wav")
        video = platform.upload(
            space_id=space["id"], policy_id=policy["id"], path=video_path,
            wait_for_knowledge=False,
        )
    audio_transcript = platform.call("GET", f"/documents/{audio['document']['id']}/transcript")
    video_profile = platform.call("GET", f"/documents/{video['document']['id']}/media-profile")
    timeline = platform.call("GET", f"/documents/{video['document']['id']}/timeline")
    scenes = platform.call("GET", f"/documents/{video['document']['id']}/scenes")
    frames = platform.call("GET", f"/documents/{video['document']['id']}/frames")
    runs = platform.call("GET", f"/documents/{video['document']['id']}/processing-runs")

    transcript_text = " ".join(row.get("text") or "" for row in audio_transcript)
    if not any(term.casefold() in transcript_text.casefold() for term in ("NexusOne", "知识平台")):
        raise RuntimeError(f"真实中文 ASR 未识别到验收事实：{transcript_text[:500]}")
    if not timeline or not scenes or not frames:
        raise RuntimeError("视频未形成完整时间线、场景和关键帧")
    if not any(frame.get("vision_status") == "succeeded" for frame in frames):
        raise RuntimeError("Kimi 视觉没有产生成功的结构化关键帧结果")
    if int((video_profile.get("cloud_processing") or {}).get("frame_count") or 0) < 1:
        raise RuntimeError("云端关键帧调用审计缺失")
    verified_run = next(
        (row for row in runs if row.get("status") in {"succeeded", "partial"}), None
    )
    if verified_run is None:
        raise RuntimeError("媒体处理历史中没有可用的成功或部分成功结果")

    media = platform.http.get(
        f"/documents/{video['document']['id']}/media-content",
        headers={"Range": "bytes=0-99"},
    )
    if media.status_code != 206 or len(media.content) != 100 or "Content-Range" not in media.headers:
        raise RuntimeError("视频 Range 播放协议未通过")

    search = platform.call(
        "POST", "/search",
        json={
            "query": "NexusOne 的产品定位和视频中的核心能力是什么",
            "space_ids": [space["id"]], "top_k": 10,
            "use_keyword": True, "use_vector": True, "use_graph": False, "use_reranker": False,
        },
    )
    media_hits = [row for row in search.get("items") or [] if row.get("media_type") in {"audio", "video"}]
    if not media_hits or not any(row.get("start_seconds") is not None for row in media_hits):
        raise RuntimeError("媒体检索未返回可定位的时间引用")

    cached = platform.call(
        "POST", f"/documents/{video['document']['id']}/reprocess-media",
        json={"media_policy_id": policy["id"], "cloud_processing_confirmed": True, "bypass_cache": False},
    )
    platform.wait_job(cached["job"]["id"])
    cached_runs = platform.call("GET", f"/documents/{video['document']['id']}/processing-runs")
    if resume_space_id and not (cached_runs[0].get("cache") or {}).get("hit"):
        # A resumed dataset may have been produced before the cache fingerprint
        # contract changed.  Establish the current fingerprint once, then prove
        # that the immediately repeated operation is a real cache hit.
        cached = platform.call(
            "POST", f"/documents/{video['document']['id']}/reprocess-media",
            json={"media_policy_id": policy["id"], "cloud_processing_confirmed": True, "bypass_cache": False},
        )
        platform.wait_job(cached["job"]["id"])
        cached_runs = platform.call("GET", f"/documents/{video['document']['id']}/processing-runs")
    if not (cached_runs[0].get("cache") or {}).get("hit"):
        raise RuntimeError("相同媒体、策略和模型重处理没有命中已验证缓存")

    # Verify the same pipeline through a real S3/MinIO data source.  Run this
    # script inside the application Docker network so MinIO stays unexposed.
    minio_access_key = os.getenv("MEDIA_TEST_MINIO_ACCESS_KEY") or os.getenv("OBJECT_STORE_ACCESS_KEY")
    minio_secret = os.getenv("MEDIA_TEST_MINIO_SECRET") or os.getenv("OBJECT_STORE_SECRET_KEY")
    if not minio_access_key or not minio_secret:
        raise RuntimeError("媒体数据源验收需要容器内对象存储凭据环境变量")
    minio = Minio(
        os.getenv("MEDIA_TEST_MINIO_ENDPOINT", "minio:9000"),
        access_key=minio_access_key,
        secret_key=minio_secret,
        secure=False,
    )
    bucket = f"media-acceptance-{suffix.lower()}"
    minio.make_bucket(bucket)
    object_name = f"training/{video_path.name}"
    media_bytes = video_path.read_bytes()
    minio.put_object(
        bucket, object_name, BytesIO(media_bytes), len(media_bytes), content_type="video/mp4"
    )
    source = platform.call(
        "POST", "/sources",
        json={
            "space_id": space["id"], "name": f"MinIO 音视频验收源 {suffix}",
            "source_type": "s3", "media_policy_id": policy["id"], "enabled": True,
            "secret": minio_secret,
            "config": {
                "endpoint": os.getenv("MEDIA_TEST_MINIO_ENDPOINT", "minio:9000"),
                "bucket": bucket, "prefix": "training/",
                "access_key": minio_access_key,
                "secure": False, "max_files": 10, "max_media_items_per_sync": 5,
                "media_allow_sync_override": True, "media_cloud_processing_confirmed": True,
                "media_sync_failure_mode": "fail",
            },
        },
    )
    source_test = platform.call(
        "POST", "/sources/test",
        json={"source_id": source["id"], "source_type": "s3", "config": source["config"]},
    )
    if source_test.get("status") != "success":
        raise RuntimeError(f"MinIO 数据源连接失败：{source_test}")
    source_sync = platform.wait_job(platform.call("POST", f"/sources/{source['id']}/sync")["id"])
    child_jobs = (source_sync.get("result") or {}).get("media_parse_job_ids") or []
    for child_job in child_jobs:
        platform.wait_job(child_job)
    child_documents = (source_sync.get("result") or {}).get("media_documents") or []
    if not child_documents:
        raise RuntimeError("MinIO 视频没有拆分为独立媒体文档")
    source_profile = platform.call(
        "GET", f"/documents/{child_documents[0]['document_id']}/media-profile"
    )
    if source_profile.get("media_type") != "video":
        raise RuntimeError("数据源媒体文档没有进入视频解析链")

    # A same-tenant user without a space grant must not be able to read the
    # media profile, timeline, bytes or frame assets, nor trigger reprocessing.
    boundary_username = f"media-boundary-{secrets.token_hex(4)}"
    boundary_password = f"MediaBoundary@{secrets.token_hex(8)}"
    boundary_user = platform.call(
        "POST", "/users",
        json={
            "username": boundary_username,
            "password": boundary_password,
            "display_name": "媒体权限边界测试用户",
            "is_admin": False,
            "enabled": True,
            "role_ids": [],
        },
    )
    try:
        restricted = httpx.Client(base_url=API, timeout=60)
        login = restricted.post(
            "/auth/login", json={"username": boundary_username, "password": boundary_password}
        )
        login.raise_for_status()
        restricted.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        protected_requests = [
            restricted.get(f"/documents/{video['document']['id']}/media-profile"),
            restricted.get(f"/documents/{video['document']['id']}/timeline"),
            restricted.get(
                f"/documents/{video['document']['id']}/media-content",
                headers={"Range": "bytes=0-9"},
            ),
            restricted.get(f"/media/frames/{frames[0]['id']}/content"),
            restricted.post(
                f"/documents/{video['document']['id']}/reprocess-media",
                json={"media_policy_id": policy["id"], "cloud_processing_confirmed": True},
            ),
        ]
        if any(response.status_code != 403 for response in protected_requests):
            raise RuntimeError(
                "媒体空间隔离失败：" + ",".join(str(item.status_code) for item in protected_requests)
            )
    finally:
        platform.call("DELETE", f"/users/{boundary_user['id']}")

    result = {
        "dataset": space["name"],
        "space_id": space["id"],
        "policy_version": policy.get("current_version"),
        "models": {"asr": asr_model["name"], "vision": vision_model["name"]},
        "probe": {
            "duration_seconds": probe["probe"].get("duration_seconds"),
            "estimated_frames": probe["frame_estimate"].get("estimated_frames"),
        },
        "audio_segments": len(audio_transcript),
        "video": {"timeline": len(timeline), "scenes": len(scenes), "frames": len(frames)},
        "cloud_frames": video_profile["cloud_processing"]["frame_count"],
        "search_media_hits": len(media_hits),
        "range_status": media.status_code,
        "cache_hit": True,
        "media_permission_isolation": True,
        "source": {
            "id": source["id"], "type": source["source_type"],
            "media_documents": len(child_documents), "parse_jobs": len(child_jobs),
        },
        "documents": [audio["document"]["id"], video["document"]["id"]],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
