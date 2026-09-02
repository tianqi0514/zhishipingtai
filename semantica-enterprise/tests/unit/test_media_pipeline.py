from __future__ import annotations

from copy import deepcopy
import threading
import time

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.platform.database import Base
from packages.platform.media import (
    DEFAULT_MEDIA_POLICY,
    MediaPolicyError,
    estimate_frame_count,
    fixed_frame_timestamps,
    hamming_distance,
    media_stage_fingerprints,
    media_policy_snapshot,
    normalize_media_policy,
    scene_frame_timestamps,
    timecode,
)
from packages.platform.media_policy import resolve_media_policy
from packages.platform.models import KnowledgeSpace, MediaParsingPolicy, ModelConfig, Tenant
from packages.platform.config import Settings
from packages.platform.storage import ObjectStorage
from packages.semantica_adapter.vision import StructuredVisionResult, _json_object
from packages.semantica_adapter.transcription import _speech_annotations
from apps.api.schemas import SourceCreate
from apps.worker.tasks import _media_model, _media_model_fingerprint
from packages.semantica_adapter import media_pipeline
from packages.semantica_adapter.media_pipeline import MediaNoSpeechError, process_media_file


def test_policy_normalization_accepts_four_modes_and_rejects_unknown_fields() -> None:
    for mode in ("fixed_interval", "fixed_fps", "scene", "smart"):
        value = deepcopy(DEFAULT_MEDIA_POLICY)
        value["frame"]["mode"] = mode
        assert normalize_media_policy(value)["frame"]["mode"] == mode
    with pytest.raises(MediaPolicyError, match="未知媒体策略字段"):
        normalize_media_policy({"fake_progress": True})
    with pytest.raises(MediaPolicyError, match="0.5 到 600"):
        normalize_media_policy({"frame": {"interval_seconds": 0.1}})
    assert normalize_media_policy(
        {"frame": {"mode": "fixed_fps", "frames_per_second": 0.5}}
    )["frame"]["fps"] == 0.5
    with pytest.raises(MediaPolicyError, match="不能同时设置"):
        normalize_media_policy({"frame": {"fps": 1, "frames_per_second": 0.5}})


def test_cloud_vision_requires_explicit_consent_and_local_mode_disables_it() -> None:
    with pytest.raises(MediaPolicyError, match="允许云处理"):
        normalize_media_policy({"cloud_processing_allowed": False, "vision": {"enabled": True}})
    local = normalize_media_policy({"processing_mode": "local", "vision": {"enabled": True}})
    assert local["vision"]["enabled"] is False
    with pytest.raises(MediaPolicyError, match="disabled"):
        normalize_media_policy({"cloud_confirmation_mode": "disabled", "vision": {"enabled": True}})


def test_estimation_and_frame_plans_are_bounded_and_stable() -> None:
    interval = normalize_media_policy({"frame": {"mode": "fixed_interval", "interval_seconds": 10, "max_frames": 5}})
    estimate = estimate_frame_count(100, interval)
    assert estimate == {
        "mode": "fixed_interval", "duration_seconds": 100.0,
        "range_start_seconds": 0.0, "range_end_seconds": 100.0,
        "estimated_frames": 5, "raw_estimate": 10, "limited": True,
        "max_frames": 5, "cloud_frames": 5,
    }
    timestamps = fixed_frame_timestamps(100, interval)
    assert timestamps[0] == 0
    assert timestamps[-1] == pytest.approx(99.95)
    assert len(timestamps) == 5
    cropped = normalize_media_policy({"frame": {"mode": "fixed_interval", "interval_seconds": 5, "start_seconds": 10, "end_seconds": 30}})
    assert fixed_frame_timestamps(60, cropped) == [10.0, 15.0, 20.0, 29.95]
    assert estimate_frame_count(60, cropped)["raw_estimate"] == 4
    scenes = [{"scene_index": 0, "time_start": 0, "time_end": 5}, {"scene_index": 1, "time_start": 5, "time_end": 12}]
    scene_policy = normalize_media_policy({"frame": {"mode": "scene", "scene_positions": ["start", "middle"], "max_frames": 10}})
    assert scene_frame_timestamps(scenes, 12, scene_policy) == [0.0, 2.5, 5.0, 8.5, 11.95]


def test_policy_snapshot_hash_ignores_database_ids_but_tracks_functional_config() -> None:
    first = media_policy_snapshot(
        policy_id="policy-a", policy_version_id="version-a", policy_name="策略", version_number=1,
        applicable_media_types=["video"], config=DEFAULT_MEDIA_POLICY,
    )
    second = media_policy_snapshot(
        policy_id="policy-a", policy_version_id="version-b", policy_name="策略", version_number=2,
        applicable_media_types=["video"], config=DEFAULT_MEDIA_POLICY,
    )
    changed = media_policy_snapshot(
        policy_id="policy-a", policy_version_id="version-c", policy_name="策略", version_number=3,
        applicable_media_types=["video"], config={"frame": {"interval_seconds": 12}},
    )
    assert first["config_hash"] == second["config_hash"]
    assert first["config_hash"] != changed["config_hash"]


def test_stage_fingerprints_only_invalidate_downstream_dependencies() -> None:
    base = normalize_media_policy({"vision": {"prompt_version": "v1"}})
    initial = media_stage_fingerprints(
        "media-sha", base, asr_model={"id": "asr-a"}, vision_model={"id": "vision-a"}
    )
    prompt_changed = media_stage_fingerprints(
        "media-sha",
        {**base, "vision": {**base["vision"], "prompt_version": "v2"}},
        asr_model={"id": "asr-a"}, vision_model={"id": "vision-a"},
    )
    assert initial["probe"] == prompt_changed["probe"]
    assert initial["asr"] == prompt_changed["asr"]
    assert initial["frames"] == prompt_changed["frames"]
    assert initial["ocr"] == prompt_changed["ocr"]
    assert initial["vision"] != prompt_changed["vision"]
    assert initial["timeline"] != prompt_changed["timeline"]

    frames_changed = media_stage_fingerprints(
        "media-sha",
        {**base, "frame": {**base["frame"], "interval_seconds": 9}},
        asr_model={"id": "asr-a"}, vision_model={"id": "vision-a"},
    )
    assert initial["asr"] == frames_changed["asr"]
    assert initial["frames"] != frames_changed["frames"]
    assert initial["ocr"] != frames_changed["ocr"]
    assert initial["vision"] != frames_changed["vision"]


def test_policy_resolution_precedence_and_immutable_override_snapshot() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(code="media", name="媒体租户")
        db.add(tenant); db.flush()
        default = MediaParsingPolicy(
            tenant_id=tenant.id, name="系统默认", applicable_media_types=["video"],
            config=DEFAULT_MEDIA_POLICY, enabled=True, is_default=True,
        )
        explicit = MediaParsingPolicy(
            tenant_id=tenant.id, name="视频策略", applicable_media_types=["video"],
            config={"frame": {"mode": "fixed_interval", "interval_seconds": 20}}, enabled=True,
        )
        db.add_all([default, explicit]); db.flush()
        space = KnowledgeSpace(tenant_id=tenant.id, code="video", name="视频", media_policy_id=explicit.id)
        db.add(space); db.flush()
        version, snapshot = resolve_media_policy(
            db, tenant_id=tenant.id, media_type="video", space_id=space.id,
            override={"frame": {"interval_seconds": 9}},
        )
        assert version is not None and version.policy_id == explicit.id
        assert snapshot["config"]["frame"]["interval_seconds"] == 9
        assert explicit.config["frame"]["interval_seconds"] == 20


def test_vision_contract_and_time_helpers() -> None:
    payload = _json_object("```json\n{\"summary\":\"产品界面\",\"visible_text\":[],\"objects\":[],\"actions\":[],\"relations\":[],\"uncertainties\":[]}\n```")
    assert StructuredVisionResult.model_validate(payload).scene_summary == "产品界面"
    assert timecode(65.8) == "01:05"
    assert timecode(3661) == "01:01:01"
    assert hamming_distance("0000000000000000", "0000000000000001") == 1


def test_sensevoice_annotations_are_cleaned_and_retained_as_events() -> None:
    text, events = _speech_annotations("<|zh|><|HAPPY|><|Speech|>传神智库支持音视频知识")
    assert text == "传神智库支持音视频知识"
    assert events == ["HAPPY", "Speech"]


def test_object_storage_streams_bounded_local_ranges(tmp_path) -> None:
    storage = ObjectStorage()
    storage.settings = Settings(use_local_object_store=True, local_storage_path=tmp_path)
    target = tmp_path / "tenant/media/test.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"0123456789")
    assert storage.stat_size("tenant/media/test.bin") == 10
    assert b"".join(storage.iter_bytes("tenant/media/test.bin", offset=2, length=4, chunk_size=2)) == b"2345"
    assert b"".join(storage.iter_bytes("tenant/media/test.bin", offset=8)) == b"89"


def test_source_media_configuration_uses_strict_policy_validation() -> None:
    payload = SourceCreate(
        space_id="space",
        name="media-source",
        source_type="rest",
        config={
            "url": "https://example.com/media.mp4",
            "response_mode": "binary",
            "max_media_items_per_sync": 25,
            "media_sync_failure_mode": "partial",
            "media_allow_sync_override": True,
            "media_policy_override": {
                "max_duration_seconds": 600,
                "frame": {"mode": "fixed_interval", "interval_seconds": 15},
            },
        },
    )
    assert payload.config["max_media_items_per_sync"] == 25
    with pytest.raises(ValueError, match="媒体同步失败处理"):
        SourceCreate(
            space_id="space", name="bad", source_type="rest",
            config={"url": "https://example.com/x", "media_sync_failure_mode": "ignore"},
        )
    with pytest.raises(ValueError, match="未知媒体策略字段"):
        SourceCreate(
            space_id="space", name="bad", source_type="rest",
            config={"url": "https://example.com/x", "media_policy_override": {"fake": True}},
        )


def test_explicit_missing_media_model_never_falls_back_to_default() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(code="models", name="模型租户")
        db.add(tenant); db.flush()
        default = ModelConfig(
            tenant_id=tenant.id, name="默认视觉", model_kind="vision",
            provider="openai_compatible", model_name="vision-default",
            enabled=True, is_default=True,
        )
        disabled = ModelConfig(
            tenant_id=tenant.id, name="已停用视觉", model_kind="vision",
            provider="openai_compatible", model_name="vision-disabled",
            enabled=False, is_default=False,
        )
        db.add_all([default, disabled]); db.flush()
        assert _media_model(db, tenant.id, "vision", None).id == default.id
        assert _media_model(db, tenant.id, "vision", disabled.id) is None
        assert _media_model(db, tenant.id, "vision", "missing-model") is None


def test_model_cache_fingerprint_ignores_connection_test_audit_timestamps() -> None:
    model = ModelConfig(
        tenant_id="tenant", name="视觉", model_kind="vision",
        provider="kimi", model_name="kimi-k3", base_url="https://example.invalid/v1",
        config={"timeout": 60}, enabled=True,
    )
    before = _media_model_fingerprint(model)
    model.updated_at = media_pipeline.datetime.now(media_pipeline.timezone.utc)
    model.last_test_at = media_pipeline.datetime.now(media_pipeline.timezone.utc)
    assert _media_model_fingerprint(model) == before


def test_silence_fail_policy_is_not_downgraded_by_partial_mode(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "silence.wav"
    audio.write_bytes(b"RIFF-fixture")
    monkeypatch.setattr(
        media_pipeline,
        "probe_media",
        lambda _path: {
            "duration_seconds": 4.0, "has_audio": True, "has_video": False,
            "width": None, "height": None,
        },
    )
    monkeypatch.setattr(
        media_pipeline,
        "transcribe_media",
        lambda *_args, **_kwargs: {"transcript": "", "segments": [], "audio_events": []},
    )
    with pytest.raises(MediaNoSpeechError, match="有效语音"):
        process_media_file(
            audio,
            policy={
                "failure_mode": "partial",
                "asr": {"enabled": True, "silence_policy": "fail", "minimum_speech_seconds": 0.2},
                "ocr": {"enabled": False},
                "vision": {"enabled": False},
            },
            working_directory=tmp_path / "work",
            asr_options={"model": "fixture", "base_url": "http://fixture"},
        )


def test_vision_concurrency_setting_bounds_real_frame_calls(tmp_path, monkeypatch) -> None:
    video = tmp_path / "frames.mp4"
    video.write_bytes(b"video-fixture")
    monkeypatch.setattr(
        media_pipeline,
        "probe_media",
        lambda _path: {
            "duration_seconds": 12.0, "has_audio": False, "has_video": True,
            "width": 640, "height": 360, "size_bytes": video.stat().st_size,
        },
    )

    def extract_frame(_source, output, timestamp, _max_edge, _timeout):
        Image.new("RGB", (64, 36), (int(timestamp * 10) % 255, 40, 80)).save(output, "JPEG")

    monkeypatch.setattr(media_pipeline, "_extract_frame", extract_frame)
    active = 0
    maximum = 0
    lock = threading.Lock()

    def describe(_path, **_kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.08)
        with lock:
            active -= 1
        return {
            "result": StructuredVisionResult(scene_summary="fixture").model_dump(),
            "model": "vision-fixture", "usage": {}, "elapsed_ms": 80,
        }

    monkeypatch.setattr(media_pipeline, "describe_image_structured", describe)
    result = process_media_file(
        video,
        policy={
            "cloud_processing_allowed": True,
            "failure_mode": "fail",
            "video": {"extract_audio_track": False, "asr_enabled": False},
            "frame": {
                "mode": "fixed_interval", "interval_seconds": 3,
                "include_first": True, "include_last": True,
                "max_frames": 4, "perceptual_hash_enabled": False,
            },
            "ocr": {"enabled": False},
            "asr": {"enabled": False},
            "vision": {"enabled": True, "execution": "cloud", "concurrency": 2},
        },
        working_directory=tmp_path / "work-concurrent",
        vision_options={"model": "vision-fixture", "api_key": "fixture"},
    )
    assert result["frame_count"] == 4
    assert maximum == 2
    assert all(frame["vision_status"] == "succeeded" for frame in result["frames"])
