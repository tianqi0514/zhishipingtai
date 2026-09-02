from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import re
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


MEDIA_SUFFIX_TYPES = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".tif": "image", ".tiff": "image", ".bmp": "image", ".gif": "image",
    ".mp3": "audio", ".wav": "audio", ".flac": "audio", ".aac": "audio",
    ".m4a": "audio", ".ogg": "audio",
    ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video",
    ".webm": "video",
}


DEFAULT_MEDIA_POLICY: dict[str, Any] = {
    "processing_mode": "hybrid",
    "cloud_processing_allowed": True,
    "cloud_confirmation_mode": "per_upload",
    "max_duration_seconds": 14_400,
    "max_file_size_bytes": 2 * 1024 * 1024 * 1024,
    "task_timeout_seconds": 14_400,
    "max_retries": 2,
    "concurrency": 1,
    "temporary_file_ttl_seconds": 3_600,
    "failure_mode": "partial",
    "video": {
        "extract_audio_track": True,
        "asr_enabled": True,
        "scene_detection_enabled": True,
    },
    "frame": {
        "mode": "smart",
        "start_seconds": 0.0,
        "end_seconds": None,
        "interval_seconds": 30.0,
        "minimum_interval_seconds": 0.5,
        "maximum_interval_seconds": 45.0,
        "fps": 0.1,
        "scene_threshold": 0.32,
        "minimum_scene_seconds": 1.0,
        "maximum_scene_seconds": 600.0,
        "scene_positions": ["middle"],
        "frames_per_scene": 1,
        "smart_max_interval_seconds": 45.0,
        "include_first": True,
        "include_last": True,
        "max_frames": 120,
        "max_image_edge": 1280,
        "jpeg_quality": 90,
        "perceptual_hash_distance": 5,
        "perceptual_hash_enabled": True,
    },
    "ocr": {"enabled": True, "language": "chi_sim+eng", "minimum_confidence": 35.0},
    "asr": {
        "enabled": True,
        "model_config_id": None,
        "language": "zh",
        "auto_detect_language": True,
        "vad": True,
        "punctuation": True,
        "maximum_segment_seconds": 30,
        "segment_overlap_seconds": 0.5,
        "speaker_diarization": False,
        "extract_audio_events": True,
        "generate_summary": True,
        "generate_chapters": True,
        "minimum_speech_seconds": 0.2,
        "silence_policy": "metadata_only",
    },
    "vision": {
        "enabled": True,
        "model_config_id": None,
        "execution": "cloud",
        "batch_size": 1,
        "concurrency": 1,
        "timeout_seconds": 120,
        "max_tokens": 2048,
        "prompt_version": "media-visible-facts-v1",
        "generate_scene_summary": True,
        "generate_video_summary": True,
    },
    "cache": {"enabled": True},
}


class MediaPolicyError(ValueError):
    pass


def media_type_for(filename: str, content_type: str | None = None) -> str | None:
    suffix_type = MEDIA_SUFFIX_TYPES.get(Path(filename).suffix.lower())
    mime = (content_type or mimetypes.guess_type(filename)[0] or "").lower()
    mime_type = mime.split("/", 1)[0] if "/" in mime else None
    if suffix_type and mime_type in {None, "", "application", suffix_type}:
        return suffix_type
    return suffix_type or (mime_type if mime_type in {"image", "audio", "video"} else None)


def _number(config: dict[str, Any], key: str, minimum: float, maximum: float) -> float:
    try:
        value = float(config[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaPolicyError(f"{key} 必须是数字") from exc
    if not minimum <= value <= maximum:
        raise MediaPolicyError(f"{key} 必须在 {minimum} 到 {maximum} 之间")
    return value


def normalize_media_policy(config: dict[str, Any] | None) -> dict[str, Any]:
    """Merge and strictly validate a media policy without retaining unknown keys."""
    raw = deepcopy(config or {})
    # Public/API wording uses ``frames_per_second`` while older persisted
    # policies used ``fps``.  Accept both, reject conflicting values, and keep
    # one canonical internal field so sampling plans cannot apply both.
    supplied_frame_alias = raw.get("frame")
    if isinstance(supplied_frame_alias, dict) and "frames_per_second" in supplied_frame_alias:
        if (
            "fps" in supplied_frame_alias
            and float(supplied_frame_alias["fps"]) != float(supplied_frame_alias["frames_per_second"])
        ):
            raise MediaPolicyError("frame.fps 与 frames_per_second 不能同时设置为不同值")
        supplied_frame_alias["fps"] = supplied_frame_alias.pop("frames_per_second")
    allowed_root = {
        "processing_mode", "cloud_processing_allowed", "max_duration_seconds",
        "cloud_confirmation_mode",
        "max_file_size_bytes", "task_timeout_seconds", "max_retries", "concurrency",
        "temporary_file_ttl_seconds", "failure_mode", "video", "frame", "ocr", "asr", "vision", "cache",
    }
    unknown = sorted(set(raw) - allowed_root)
    if unknown:
        raise MediaPolicyError(f"未知媒体策略字段：{', '.join(unknown)}")
    result = deepcopy(DEFAULT_MEDIA_POLICY)
    for key in ("processing_mode", "cloud_processing_allowed", "cloud_confirmation_mode", "max_duration_seconds", "max_file_size_bytes", "task_timeout_seconds", "max_retries", "concurrency", "temporary_file_ttl_seconds", "failure_mode"):
        if key in raw:
            result[key] = raw[key]
    for section in ("video", "frame", "ocr", "asr", "vision", "cache"):
        supplied = raw.get(section, {})
        if not isinstance(supplied, dict):
            raise MediaPolicyError(f"{section} 必须是对象")
        unknown_section = sorted(set(supplied) - set(result[section]))
        if unknown_section:
            raise MediaPolicyError(f"{section} 包含未知字段：{', '.join(unknown_section)}")
        result[section].update(supplied)

    if result["processing_mode"] not in {"local", "cloud", "hybrid"}:
        raise MediaPolicyError("processing_mode 仅支持 local、cloud 或 hybrid")
    if result["failure_mode"] not in {"partial", "fail"}:
        raise MediaPolicyError("failure_mode 仅支持 partial 或 fail")
    if result["cloud_confirmation_mode"] not in {"per_upload", "always_allow", "disabled"}:
        raise MediaPolicyError("cloud_confirmation_mode 配置不合法")
    result["max_duration_seconds"] = int(_number(result, "max_duration_seconds", 1, 86_400))
    result["max_file_size_bytes"] = int(_number(result, "max_file_size_bytes", 1_024, 20 * 1024 * 1024 * 1024))
    result["task_timeout_seconds"] = int(_number(result, "task_timeout_seconds", 30, 86_400))
    result["max_retries"] = int(_number(result, "max_retries", 0, 10))
    result["concurrency"] = int(_number(result, "concurrency", 1, 16))
    result["temporary_file_ttl_seconds"] = int(_number(result, "temporary_file_ttl_seconds", 0, 86_400))
    if not isinstance(result["cloud_processing_allowed"], bool):
        raise MediaPolicyError("cloud_processing_allowed 必须是布尔值")

    frame = result["frame"]
    if frame["mode"] not in {"fixed_interval", "fixed_fps", "scene", "smart"}:
        raise MediaPolicyError("frame.mode 仅支持 fixed_interval、fixed_fps、scene 或 smart")
    frame["start_seconds"] = _number(frame, "start_seconds", 0, 86_400)
    if frame["end_seconds"] is not None:
        frame["end_seconds"] = _number(frame, "end_seconds", 0, 86_400)
        if frame["end_seconds"] <= frame["start_seconds"]:
            raise MediaPolicyError("frame.end_seconds 必须大于 start_seconds")
    frame["interval_seconds"] = _number(frame, "interval_seconds", 0.5, 600)
    frame["minimum_interval_seconds"] = _number(frame, "minimum_interval_seconds", 0.1, 600)
    frame["maximum_interval_seconds"] = _number(frame, "maximum_interval_seconds", 0.5, 3600)
    if frame["maximum_interval_seconds"] < frame["minimum_interval_seconds"]:
        raise MediaPolicyError("最大抽帧间隔不能小于最小抽帧间隔")
    frame["fps"] = _number(frame, "fps", 0.01, 2)
    frame["scene_threshold"] = _number(frame, "scene_threshold", 0.01, 0.99)
    frame["minimum_scene_seconds"] = _number(frame, "minimum_scene_seconds", 0, 600)
    frame["maximum_scene_seconds"] = _number(frame, "maximum_scene_seconds", 1, 3_600)
    if frame["maximum_scene_seconds"] < frame["minimum_scene_seconds"]:
        raise MediaPolicyError("maximum_scene_seconds 不能小于 minimum_scene_seconds")
    frame["smart_max_interval_seconds"] = _number(frame, "smart_max_interval_seconds", 0.5, 600)
    frame["max_frames"] = int(_number(frame, "max_frames", 1, 10_000))
    frame["frames_per_scene"] = int(_number(frame, "frames_per_scene", 1, 3))
    frame["max_image_edge"] = int(_number(frame, "max_image_edge", 256, 4096))
    frame["jpeg_quality"] = int(_number(frame, "jpeg_quality", 40, 100))
    frame["perceptual_hash_distance"] = int(_number(frame, "perceptual_hash_distance", 0, 64))
    positions = frame.get("scene_positions")
    if not isinstance(positions, list) or not positions or any(x not in {"start", "middle", "end"} for x in positions):
        raise MediaPolicyError("scene_positions 必须从 start、middle、end 中选择")
    frame["scene_positions"] = list(dict.fromkeys(positions))
    # Older saved policies only persisted ``scene_positions``.  Preserve the
    # user's explicit positions instead of silently truncating them to the new
    # frames_per_scene default during migration/rehydration.
    supplied_frame = raw.get("frame") or {}
    if "scene_positions" in supplied_frame and "frames_per_scene" not in supplied_frame:
        frame["frames_per_scene"] = len(frame["scene_positions"])
    for key in ("include_first", "include_last", "perceptual_hash_enabled"):
        if not isinstance(frame[key], bool):
            raise MediaPolicyError(f"frame.{key} 必须是布尔值")

    for key in ("extract_audio_track", "asr_enabled", "scene_detection_enabled"):
        if not isinstance(result["video"][key], bool):
            raise MediaPolicyError(f"video.{key} 必须是布尔值")
    for section in ("ocr", "asr", "vision", "cache"):
        if not isinstance(result[section]["enabled"], bool):
            raise MediaPolicyError(f"{section}.enabled 必须是布尔值")
    result["ocr"]["minimum_confidence"] = _number(result["ocr"], "minimum_confidence", 0, 100)
    result["asr"]["maximum_segment_seconds"] = int(
        _number(result["asr"], "maximum_segment_seconds", 1, 600)
    )
    result["asr"]["segment_overlap_seconds"] = _number(result["asr"], "segment_overlap_seconds", 0, 10)
    if result["asr"]["segment_overlap_seconds"] >= result["asr"]["maximum_segment_seconds"]:
        raise MediaPolicyError("ASR 分段重叠必须小于分段时长")
    result["asr"]["minimum_speech_seconds"] = _number(result["asr"], "minimum_speech_seconds", 0, 30)
    if result["asr"]["silence_policy"] not in {"metadata_only", "empty_transcript", "fail"}:
        raise MediaPolicyError("silence_policy 配置不合法")
    for key in ("auto_detect_language", "vad", "punctuation", "speaker_diarization", "extract_audio_events", "generate_summary", "generate_chapters"):
        if not isinstance(result["asr"][key], bool):
            raise MediaPolicyError(f"asr.{key} 必须是布尔值")
    if result["vision"]["execution"] not in {"local", "cloud"}:
        raise MediaPolicyError("vision.execution 仅支持 local 或 cloud")
    result["vision"]["batch_size"] = int(_number(result["vision"], "batch_size", 1, 20))
    result["vision"]["concurrency"] = int(_number(result["vision"], "concurrency", 1, 8))
    result["vision"]["timeout_seconds"] = int(
        _number(result["vision"], "timeout_seconds", 5, 900)
    )
    result["vision"]["max_tokens"] = int(_number(result["vision"], "max_tokens", 64, 4000))
    if result["vision"]["enabled"] and result["processing_mode"] == "local" and result["vision"]["execution"] == "cloud":
        # Local-only is a real, intentional degradation rather than a hidden cloud call.
        result["vision"]["enabled"] = False
    if result["vision"]["enabled"] and result["vision"]["execution"] == "cloud" and not result["cloud_processing_allowed"]:
        raise MediaPolicyError("启用云端视觉时必须允许云处理")
    if result["vision"]["enabled"] and result["vision"]["execution"] == "cloud" and result["cloud_confirmation_mode"] == "disabled":
        raise MediaPolicyError("云处理确认模式为 disabled 时不能启用云端视觉")
    return result


def media_policy_snapshot(
    *,
    policy_id: str | None,
    policy_version_id: str | None,
    policy_name: str,
    version_number: int,
    applicable_media_types: list[str],
    config: dict[str, Any],
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_media_policy(_deep_merge(config, override or {}))
    snapshot = {
        "policy_id": policy_id,
        "policy_version_id": policy_version_id,
        "policy_name": policy_name,
        "version_number": version_number,
        "applicable_media_types": sorted(set(applicable_media_types)),
        "config": normalized,
    }
    snapshot["config_hash"] = fingerprint(
        {"applicable_media_types": snapshot["applicable_media_types"], "config": normalized}
    )
    return snapshot


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def media_stage_fingerprints(
    media_checksum: str,
    config: dict[str, Any],
    *,
    asr_model: dict[str, Any] | None = None,
    vision_model: dict[str, Any] | None = None,
    adapter_version: str = "media-pipeline-v2",
) -> dict[str, str]:
    """Build dependency-aware fingerprints for partial media reprocessing.

    A vision prompt change must not invalidate ASR, while a frame-plan change
    must invalidate frame-derived OCR/Vision but retain the audio transcript.
    The fingerprints deliberately exclude credentials.
    """
    normalized = normalize_media_policy(config)
    probe = fingerprint({"checksum": media_checksum, "adapter": adapter_version, "stage": "probe"})
    asr = fingerprint({
        "probe": probe,
        "video_audio": {
            "extract_audio_track": normalized["video"]["extract_audio_track"],
            "asr_enabled": normalized["video"]["asr_enabled"],
        },
        "asr": normalized["asr"],
        "model": asr_model,
    })
    scenes = fingerprint({
        "probe": probe,
        "scene_detection_enabled": normalized["video"]["scene_detection_enabled"],
        "frame": {
            key: normalized["frame"][key]
            for key in (
                "mode", "start_seconds", "end_seconds", "scene_threshold",
                "minimum_scene_seconds", "maximum_scene_seconds",
            )
        },
    })
    frames = fingerprint({
        "probe": probe,
        "scenes": scenes,
        "frame": normalized["frame"],
    })
    ocr = fingerprint({"frames": frames, "ocr": normalized["ocr"]})
    vision = fingerprint({
        "frames": frames,
        "vision": normalized["vision"],
        "model": vision_model,
    })
    timeline = fingerprint({
        "asr": asr,
        "ocr": ocr,
        "vision": vision,
        "generate_summary": normalized["asr"]["generate_summary"],
        "generate_chapters": normalized["asr"]["generate_chapters"],
        "generate_video_summary": normalized["vision"]["generate_video_summary"],
    })
    return {
        "probe": probe,
        "asr": asr,
        "scenes": scenes,
        "frames": frames,
        "ocr": ocr,
        "vision": vision,
        "timeline": timeline,
    }


def uses_cloud_vision(config: dict[str, Any]) -> bool:
    normalized = normalize_media_policy(config)
    return bool(
        normalized["vision"]["enabled"]
        and normalized["vision"]["execution"] == "cloud"
    )


def require_cloud_confirmation(config: dict[str, Any], confirmed: bool) -> None:
    """Enforce per-upload consent server-side before any cloud work is queued."""
    normalized = normalize_media_policy(config)
    if (
        uses_cloud_vision(normalized)
        and normalized["cloud_confirmation_mode"] == "per_upload"
        and not confirmed
    ):
        raise MediaPolicyError("本次处理会将选定关键帧发送到云端，请先确认云端处理")


def estimate_frame_count(duration_seconds: float, config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_media_policy(config)
    frame = normalized["frame"]
    media_duration = max(0.0, float(duration_seconds))
    start = min(frame["start_seconds"], media_duration)
    end = min(float(frame["end_seconds"]) if frame["end_seconds"] is not None else media_duration, media_duration)
    duration = max(0.0, end - start)
    if frame["mode"] == "fixed_interval":
        step = max(frame["minimum_interval_seconds"], min(frame["interval_seconds"], frame["maximum_interval_seconds"]))
        raw = math.ceil(duration / step)
    elif frame["mode"] == "fixed_fps":
        step = max(frame["minimum_interval_seconds"], min(1.0 / frame["fps"], frame["maximum_interval_seconds"]))
        raw = math.ceil(duration / step)
    elif frame["mode"] == "scene":
        raw = max(1, math.ceil(duration / max(frame["minimum_scene_seconds"], 1))) * frame["frames_per_scene"]
    else:
        # Before scene detection, smart mode can only provide a conservative bound.
        raw = math.ceil(duration / frame["smart_max_interval_seconds"]) + 2
    estimated = min(max(1, raw), frame["max_frames"])
    return {
        "mode": frame["mode"],
        "duration_seconds": round(duration, 3),
        "range_start_seconds": round(start, 3),
        "range_end_seconds": round(end, 3),
        "estimated_frames": estimated,
        "raw_estimate": raw,
        "limited": raw > frame["max_frames"],
        "max_frames": frame["max_frames"],
        "cloud_frames": estimated if normalized["vision"]["enabled"] else 0,
    }


def fixed_frame_timestamps(duration_seconds: float, config: dict[str, Any]) -> list[float]:
    normalized = normalize_media_policy(config)
    frame = normalized["frame"]
    media_duration = max(0.0, float(duration_seconds))
    start = min(frame["start_seconds"], media_duration)
    end = min(float(frame["end_seconds"]) if frame["end_seconds"] is not None else media_duration, media_duration)
    duration = max(0.0, end - start)
    if frame["mode"] == "fixed_fps":
        step = 1.0 / frame["fps"]
    else:
        step = frame["interval_seconds"] if frame["mode"] == "fixed_interval" else frame["smart_max_interval_seconds"]
    step = max(frame["minimum_interval_seconds"], min(step, frame["maximum_interval_seconds"]))
    count = max(1, math.ceil(duration / step))
    timestamps = [start + index * step for index in range(count)]
    if frame["include_first"]:
        timestamps[0] = start
    if frame["include_last"] and duration > 0:
        timestamps[-1] = max(start, end - 0.05)
    return limit_timestamps(timestamps, media_duration, frame["max_frames"])


def limit_timestamps(values: Iterable[float], duration: float, maximum: int) -> list[float]:
    unique = sorted({round(max(0.0, min(float(value), max(0.0, duration))), 3) for value in values})
    if len(unique) <= maximum:
        return unique
    if maximum == 1:
        return [unique[0]]
    indices = {round(index * (len(unique) - 1) / (maximum - 1)) for index in range(maximum)}
    return [unique[index] for index in sorted(indices)]


def scene_frame_timestamps(
    scenes: Iterable[dict[str, Any]], duration: float, config: dict[str, Any]
) -> list[float]:
    normalized = normalize_media_policy(config)
    frame = normalized["frame"]
    media_duration = max(0.0, float(duration))
    range_start = min(frame["start_seconds"], media_duration)
    range_end = min(float(frame["end_seconds"]) if frame["end_seconds"] is not None else media_duration, media_duration)
    timestamps: list[float] = []
    for scene in scenes:
        start = max(range_start, float(scene["time_start"])); end = min(range_end, float(scene["time_end"]))
        if end <= start:
            continue
        positions = frame["scene_positions"][:frame["frames_per_scene"]]
        if len(positions) < frame["frames_per_scene"]:
            positions = {
                1: ["middle"],
                2: ["start", "end"],
                3: ["start", "middle", "end"],
            }[frame["frames_per_scene"]]
        for position in positions:
            timestamps.append({"start": start, "middle": (start + end) / 2, "end": max(start, end - 0.05)}[position])
    if frame["mode"] == "smart":
        timestamps.extend(fixed_frame_timestamps(media_duration, {**normalized, "frame": {**frame, "mode": "smart"}}))
    if frame["include_first"]:
        timestamps.append(range_start)
    if frame["include_last"] and range_end > range_start:
        timestamps.append(max(range_start, range_end - 0.05))
    return limit_timestamps(timestamps, media_duration, frame["max_frames"])


def probe_media(path: Path, timeout: float = 30) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json", "-show_format",
            "-show_streams", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"媒体探测失败：{(result.stderr or '未知错误')[-800:]}")
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float((payload.get("format") or {}).get("duration") or (video or audio or {}).get("duration") or 0)
    return {
        "duration_seconds": round(max(0.0, duration), 3),
        "format_name": (payload.get("format") or {}).get("format_name"),
        "size_bytes": int((payload.get("format") or {}).get("size") or path.stat().st_size),
        "has_video": video is not None,
        "has_audio": audio is not None,
        "width": int((video or {}).get("width") or 0) or None,
        "height": int((video or {}).get("height") or 0) or None,
        "video_codec": (video or {}).get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "sample_rate": int((audio or {}).get("sample_rate") or 0) or None,
    }


def detect_scenes(path: Path, threshold: float, minimum_scene_seconds: float, timeout: float = 300) -> list[dict[str, Any]]:
    """Detect real visual cuts with ffmpeg's scene score; no model or fake timing."""
    probe = probe_media(path, min(timeout, 30))
    duration = float(probe["duration_seconds"])
    if not probe["has_video"] or duration <= 0:
        return []
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-filter:v",
            f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"场景检测失败：{(result.stderr or '未知错误')[-800:]}")
    cuts = [0.0]
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", result.stderr or ""):
        value = float(match.group(1))
        if value - cuts[-1] >= minimum_scene_seconds:
            cuts.append(value)
    if duration - cuts[-1] < minimum_scene_seconds and len(cuts) > 1:
        cuts.pop()
    cuts.append(duration)
    return [
        {"scene_index": index, "time_start": round(start, 3), "time_end": round(end, 3)}
        for index, (start, end) in enumerate(zip(cuts, cuts[1:]))
        if end > start
    ]


def timecode(seconds: float | None) -> str:
    value = max(0, int(float(seconds or 0)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def hamming_distance(first: str, second: str) -> int:
    if len(first) != len(second):
        raise ValueError("感知哈希长度必须一致")
    return (int(first, 16) ^ int(second, 16)).bit_count()
