from __future__ import annotations

import hashlib
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from packages.platform.media import (
    detect_scenes,
    fixed_frame_timestamps,
    hamming_distance,
    media_type_for,
    normalize_media_policy,
    probe_media,
    scene_frame_timestamps,
)

from .transcription import TranscriptionCancelled, transcribe_media
from .vision import describe_image_structured


ProgressCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]
ArtifactWriter = Callable[[Path, Path, int], tuple[str, str]]
ArtifactReader = Callable[[str, Path], None]


class MediaProcessingCancelled(RuntimeError):
    pass


class MediaNoSpeechError(RuntimeError):
    """Raised when a policy explicitly rejects silent/empty audio."""

    pass


def _check_cancelled(cancelled: CancelCallback | None) -> None:
    if cancelled and cancelled():
        raise MediaProcessingCancelled("媒体处理已取消")


def _report(callback: ProgressCallback | None, stage: str, completed: int, total: int) -> None:
    if callback:
        callback(stage, completed, max(total, 1))


def _extract_frame(path: Path, output: Path, timestamp: float, max_edge: int, timeout: float) -> None:
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(path),
            "-frames:v", "1", "-vf", f"scale='min({max_edge},iw)':-2", "-q:v", "3", "-y", str(output),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0 or not output.exists() or not output.stat().st_size:
        raise RuntimeError(f"关键帧提取失败：{(result.stderr or '未知错误')[-600:]}")


def _prepare_image(path: Path, output: Path, max_edge: int) -> None:
    with Image.open(path) as image:
        frame = image.convert("RGB")
        frame.thumbnail((max_edge, max_edge))
        frame.save(output, "JPEG", quality=90, optimize=True)


def _thumbnail(source: Path, target: Path) -> None:
    with Image.open(source) as image:
        preview = image.convert("RGB")
        preview.thumbnail((360, 240))
        preview.save(target, "JPEG", quality=82, optimize=True)


def _average_hash(path: Path) -> str:
    with Image.open(path) as image:
        resized = image.convert("L").resize((8, 8))
        pixels = list(resized.get_flattened_data() if hasattr(resized, "get_flattened_data") else resized.getdata())
    average = sum(pixels) / max(len(pixels), 1)
    bits = 0
    for value in pixels:
        bits = (bits << 1) | int(value >= average)
    return f"{bits:016x}"


def _ocr(path: Path, language: str, minimum_confidence: float) -> dict[str, Any]:
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:
        raise RuntimeError("OCR 运行依赖未安装") from exc
    # pytesseract accepts a filesystem path as ``str`` but some releases do
    # not recognise ``pathlib.Path`` and raise "Unsupported image object".
    data = pytesseract.image_to_data(str(path), lang=language, output_type=Output.DICT)
    words: list[str] = []
    confidences: list[float] = []
    for text, confidence in zip(data.get("text") or [], data.get("conf") or []):
        try:
            score = float(confidence)
        except (TypeError, ValueError):
            continue
        value = str(text or "").strip()
        if value and score >= minimum_confidence:
            words.append(value); confidences.append(score)
    return {
        "text": " ".join(words),
        "confidence": round(sum(confidences) / len(confidences), 2) if confidences else None,
        "word_count": len(words),
    }


def _scene_for_timestamp(scenes: list[dict[str, Any]], timestamp: float) -> int | None:
    for scene in scenes:
        if scene["time_start"] <= timestamp <= scene["time_end"]:
            return int(scene["scene_index"])
    return None


def _bounded_scenes(
    scenes: list[dict[str, Any]], frame_config: dict[str, Any], duration: float
) -> list[dict[str, Any]]:
    start = min(float(frame_config["start_seconds"]), duration)
    end = min(float(frame_config["end_seconds"]) if frame_config["end_seconds"] is not None else duration, duration)
    maximum = float(frame_config["maximum_scene_seconds"])
    bounded: list[dict[str, Any]] = []
    for source in scenes or [{"time_start": start, "time_end": end}]:
        scene_start = max(start, float(source.get("time_start") or 0))
        scene_end = min(end, float(source.get("time_end") or 0))
        cursor = scene_start
        while scene_end > cursor:
            part_end = min(scene_end, cursor + maximum)
            if part_end - cursor >= float(frame_config["minimum_scene_seconds"]) or not bounded:
                bounded.append({
                    "scene_index": len(bounded), "time_start": round(cursor, 3),
                    "time_end": round(part_end, 3), "detection_method": "ffmpeg_scene",
                    "detection_score": source.get("detection_score"),
                })
            cursor = part_end
    return bounded


def _selection_reason(timestamp: float, scenes: list[dict[str, Any]], frame_config: dict[str, Any]) -> str:
    if abs(timestamp - float(frame_config["start_seconds"])) < 0.1 or (
        frame_config["end_seconds"] is not None and abs(timestamp - float(frame_config["end_seconds"])) < 0.1
    ):
        return "forced_boundary"
    scene_index = _scene_for_timestamp(scenes, timestamp)
    if scene_index is not None and frame_config["mode"] in {"scene", "smart"}:
        scene = next(item for item in scenes if int(item["scene_index"]) == scene_index)
        distances = {
            "scene_start": abs(timestamp - float(scene["time_start"])),
            "scene_middle": abs(timestamp - (float(scene["time_start"]) + float(scene["time_end"])) / 2),
            "scene_end": abs(timestamp - max(float(scene["time_start"]), float(scene["time_end"]) - 0.05)),
        }
        return min(distances, key=distances.get)
    return "interval_sample"


def _scene_summary(
    scene: dict[str, Any], frames: list[dict[str, Any]], segments: list[dict[str, Any]]
) -> tuple[str, dict[str, Any]]:
    scene_frames = [item for item in frames if item.get("scene_index") == scene["scene_index"]]
    scene_segments = [
        item for item in segments
        if float(item.get("end") or 0) >= scene["time_start"]
        and float(item.get("start") or 0) <= scene["time_end"]
    ]
    parts: list[str] = []
    transcript = " ".join(str(item.get("text") or "").strip() for item in scene_segments).strip()
    if transcript:
        parts.append(f"语音：{transcript}")
    ocr = " ".join(str(item.get("ocr_text") or "").strip() for item in scene_frames).strip()
    if ocr:
        parts.append(f"画面文字：{ocr}")
    visible = [
        str((item.get("vision_result") or {}).get("scene_summary") or "").strip()
        for item in scene_frames
    ]
    visible = list(dict.fromkeys(value for value in visible if value))
    if visible:
        parts.append("画面：" + "；".join(visible))
    evidence = {
        "frame_indexes": [item["frame_index"] for item in scene_frames],
        "segment_indexes": [item["segment_index"] for item in scene_segments],
    }
    return "\n".join(parts), evidence


def process_media_file(
    path: Path,
    *,
    policy: dict[str, Any],
    working_directory: Path,
    asr_options: dict[str, Any] | None = None,
    vision_options: dict[str, Any] | None = None,
    progress: ProgressCallback | None = None,
    cancelled: CancelCallback | None = None,
    artifact_writer: ArtifactWriter | None = None,
    artifact_reader: ArtifactReader | None = None,
    reuse_result: dict[str, Any] | None = None,
    reuse_stages: set[str] | None = None,
    target_scene_index: int | None = None,
) -> dict[str, Any]:
    """Execute deterministic local preprocessing plus configured ASR/Vision.

    Secrets are accepted only in the ephemeral options dictionaries and never
    copied into the returned result.
    """
    config = normalize_media_policy(policy)
    reused = reuse_result or {}
    reusable = set(reuse_stages or set())
    media_type = media_type_for(path.name)
    if media_type not in {"image", "audio", "video"}:
        raise ValueError("不是受支持的媒体文件")
    _check_cancelled(cancelled)
    probe = deepcopy(reused.get("probe") or reused.get("metadata") or {}) if "probe" in reusable else probe_media(path)
    if not probe:
        probe = probe_media(path)
    _report(progress, "media_probe", 1, 1)
    if probe["duration_seconds"] > config["max_duration_seconds"]:
        raise ValueError("媒体时长超过策略允许上限")
    if path.stat().st_size > config["max_file_size_bytes"]:
        raise ValueError("媒体文件大小超过策略允许上限")

    warnings: list[str] = []
    segments: list[dict[str, Any]] = []
    audio_events: list[dict[str, Any]] = []
    transcription_status = "not_configured"
    asr_model_version = None
    transcription_time_seconds = None
    video_audio_enabled = media_type != "video" or (
        config["video"]["extract_audio_track"] and config["video"]["asr_enabled"]
    )
    if "asr" in reusable:
        segments = deepcopy(reused.get("segments") or [])
        audio_events = deepcopy(reused.get("audio_events") or [])
        transcription_status = str(reused.get("transcription_status") or "succeeded")
        _report(progress, "asr", 1, 1)
    elif media_type in {"audio", "video"} and config["asr"]["enabled"] and video_audio_enabled and probe.get("has_audio"):
        if asr_options:
            _check_cancelled(cancelled)
            _report(progress, "asr", 0, 1)
            try:
                transcription = transcribe_media(
                    path,
                    media_type,
                    api_key=str(asr_options.get("api_key") or "local-runtime"),
                    model=str(asr_options["model"]),
                    base_url=asr_options.get("base_url"),
                    timeout=float(asr_options.get("timeout", 300)),
                    max_retries=int(asr_options.get("max_retries", 2)),
                    language=config["asr"].get("language"),
                    prompt=asr_options.get("prompt"),
                    segment_seconds=int(config["asr"]["maximum_segment_seconds"]),
                    segment_overlap_seconds=float(config["asr"]["segment_overlap_seconds"]),
                    progress=lambda completed, total: _report(progress, "asr", completed, total),
                    cancelled=cancelled,
                )
                for index, item in enumerate(transcription.get("segments") or []):
                    start = float(item.get("start") or 0)
                    end = float(item.get("end") or start)
                    if end < start:
                        end = start
                    segments.append({
                        "segment_index": index,
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "text": str(item.get("text") or "").strip(),
                        "language": transcription.get("language"),
                        "speaker": item.get("speaker"),
                        "confidence": item.get("confidence"),
                        "words": item.get("words") or [],
                        "events": item.get("events") or [],
                    })
                audio_events = list(transcription.get("audio_events") or [])
                asr_model_version = transcription.get("model_version")
                transcription_time_seconds = transcription.get("transcription_time_seconds")
                warnings.extend(str(value) for value in (transcription.get("warnings") or []) if value)
                if not segments and transcription.get("transcript"):
                    segments.append({
                        "segment_index": 0, "start": 0.0,
                        "end": float(probe["duration_seconds"]),
                        "text": str(transcription["transcript"]),
                        "language": transcription.get("language"),
                        "speaker": None, "confidence": None,
                    })
                transcription_status = "succeeded"
                speech_seconds = sum(
                    max(0.0, float(item.get("end") or 0) - float(item.get("start") or 0))
                    for item in segments
                    if str(item.get("text") or "").strip()
                )
                if speech_seconds < float(config["asr"]["minimum_speech_seconds"]):
                    segments = []
                    silence_policy = config["asr"]["silence_policy"]
                    if silence_policy == "fail":
                        raise MediaNoSpeechError("音频中未识别到达到最短时长的有效语音")
                    warnings.append(
                        "未识别到有效语音，已保留空转写"
                        if silence_policy == "empty_transcript"
                        else "未识别到有效语音，已仅保留媒体元数据"
                    )
                if config["asr"]["speaker_diarization"] and not any(item.get("speaker") for item in segments):
                    warnings.append("当前 ASR 运行时未返回说话人标签，已保留普通分段")
                _report(progress, "asr", 1, 1)
            except TranscriptionCancelled as exc:
                raise MediaProcessingCancelled(str(exc)) from exc
            except MediaNoSpeechError:
                raise
            except Exception as exc:
                transcription_status = "failed"
                warnings.append(f"ASR 部分失败：{type(exc).__name__}: {exc}"[:1000])
                if config["failure_mode"] == "fail":
                    raise
        else:
            warnings.append("未配置可用 ASR 模型，已保留媒体元数据")
    elif media_type in {"audio", "video"} and config["asr"]["enabled"] and not probe.get("has_audio"):
        warnings.append("媒体没有可用音轨，已跳过语音转写")

    scenes: list[dict[str, Any]] = []
    if media_type == "video" and probe.get("has_video"):
        _check_cancelled(cancelled)
        frame_config = config["frame"]
        if "scenes" in reusable:
            scenes = deepcopy(reused.get("scenes") or [])
        else:
            if frame_config["mode"] in {"scene", "smart"} and config["video"]["scene_detection_enabled"]:
                scenes = detect_scenes(
                    path,
                    frame_config["scene_threshold"],
                    frame_config["minimum_scene_seconds"],
                )
            scenes = _bounded_scenes(scenes, frame_config, probe["duration_seconds"])
        _report(progress, "scene_detection", 1, 1)

    frame_records: list[dict[str, Any]] = []
    if "frames" in reusable:
        frame_records = deepcopy(reused.get("frames") or [])
        _report(progress, "frame_extraction", len(frame_records), max(len(frame_records), 1))
    elif media_type == "image" or (media_type == "video" and probe.get("has_video")):
        frame_config = config["frame"]
        if media_type == "image":
            timestamps = [0.0]
        elif frame_config["mode"] in {"scene", "smart"}:
            timestamps = scene_frame_timestamps(scenes, probe["duration_seconds"], config)
        else:
            timestamps = fixed_frame_timestamps(probe["duration_seconds"], config)
        frame_root = working_directory / "frames"
        frame_root.mkdir(parents=True, exist_ok=True)
        accepted_hashes: list[tuple[str, int]] = []
        total = len(timestamps)
        for candidate_index, timestamp in enumerate(timestamps):
            _check_cancelled(cancelled)
            frame_path = frame_root / f"candidate-{candidate_index:05d}.jpg"
            actual_timestamp = timestamp
            if media_type == "image":
                _prepare_image(path, frame_path, frame_config["max_image_edge"])
            else:
                try:
                    _extract_frame(path, frame_path, actual_timestamp, frame_config["max_image_edge"], 120)
                except RuntimeError as first_error:
                    # Container duration is commonly a few frames longer than
                    # the final decodable video packet. Move back safely for
                    # the explicit tail frame before treating it as partial.
                    fallback = max(0.0, actual_timestamp - 0.5)
                    try:
                        _extract_frame(path, frame_path, fallback, frame_config["max_image_edge"], 120)
                        actual_timestamp = fallback
                        warnings.append(f"尾帧 {timestamp:.3f}s 不可解码，已回退到 {fallback:.3f}s")
                    except RuntimeError:
                        if config["failure_mode"] == "fail":
                            raise first_error
                        warnings.append(f"跳过不可解码关键帧 {timestamp:.3f}s")
                        _report(progress, "frame_extraction", candidate_index + 1, total)
                        continue
            perceptual_hash = _average_hash(frame_path)
            duplicate_of = None
            if frame_config["perceptual_hash_enabled"]:
                duplicate_of = next(
                    (index for value, index in accepted_hashes if hamming_distance(value, perceptual_hash) <= frame_config["perceptual_hash_distance"]),
                    None,
                )
            if duplicate_of is not None:
                _report(progress, "frame_extraction", candidate_index + 1, total)
                continue
            frame_index = len(frame_records)
            accepted_hashes.append((perceptual_hash, frame_index))
            thumbnail_path = frame_root / f"thumbnail-{frame_index:05d}.jpg"
            _thumbnail(frame_path, thumbnail_path)
            object_key, thumbnail_key = artifact_writer(frame_path, thumbnail_path, frame_index) if artifact_writer else (str(frame_path), str(thumbnail_path))
            with Image.open(frame_path) as image:
                width, height = image.size
            record = {
                "frame_index": frame_index,
                "timestamp": round(actual_timestamp, 3),
                "scene_index": _scene_for_timestamp(scenes, actual_timestamp),
                "object_key": object_key,
                "thumbnail_key": thumbnail_key,
                "width": width,
                "height": height,
                "format": "jpeg",
                "sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
                "perceptual_hash": perceptual_hash,
                "selection_reason": _selection_reason(actual_timestamp, scenes, frame_config) if media_type == "video" else "source_image",
                "ocr_status": "not_configured",
                "ocr_text": "",
                "ocr_confidence": None,
                "vision_status": "not_configured",
                "vision_result": {},
                "vision_model": None,
                "vision_usage": {},
                "_local_path": str(frame_path),
            }
            frame_records.append(record)
            _report(progress, "frame_extraction", candidate_index + 1, total)

    frame_root = working_directory / "frames"
    frame_root.mkdir(parents=True, exist_ok=True)
    warned_missing_vision = False
    pending_vision: list[tuple[int, dict[str, Any], Path]] = []
    for position, record in enumerate(frame_records):
        _check_cancelled(cancelled)
        is_target = target_scene_index is None or record.get("scene_index") == target_scene_index
        local_path = Path(str(record.get("_local_path") or "")) if record.get("_local_path") else None
        needs_ocr = bool(config["ocr"]["enabled"] and is_target and ("ocr" not in reusable or target_scene_index is not None))
        needs_vision = bool(config["vision"]["enabled"] and is_target and ("vision" not in reusable or target_scene_index is not None))
        if (needs_ocr or needs_vision) and (local_path is None or not local_path.exists()):
            if not artifact_reader or not record.get("object_key"):
                raise RuntimeError("阶段级重处理缺少可读取的关键帧制品")
            local_path = frame_root / f"reused-{int(record.get('frame_index') or position):05d}.jpg"
            artifact_reader(str(record["object_key"]), local_path)
        if not is_target:
            _report(progress, "ocr", position + 1, max(len(frame_records), 1))
            _report(progress, "vision", position + 1, max(len(frame_records), 1))
            continue
        if needs_ocr and local_path is not None:
            try:
                ocr = _ocr(local_path, config["ocr"]["language"], config["ocr"]["minimum_confidence"])
                record.update({"ocr_status": "succeeded", "ocr_text": ocr["text"], "ocr_confidence": ocr["confidence"]})
            except Exception as exc:
                record["ocr_status"] = "failed"
                warnings.append(f"第 {position + 1} 帧 OCR 失败：{type(exc).__name__}: {exc}"[:1000])
                if config["failure_mode"] == "fail":
                    raise
        elif not config["ocr"]["enabled"]:
            record.update({"ocr_status": "not_configured", "ocr_text": "", "ocr_confidence": None})
        _report(progress, "ocr", position + 1, max(len(frame_records), 1))

        if needs_vision:
            if vision_options and local_path is not None:
                pending_vision.append((position, record, local_path))
            elif not warned_missing_vision:
                warnings.append("未配置可用视觉模型，已完成本地抽帧和 OCR")
                warned_missing_vision = True
        elif not config["vision"]["enabled"]:
            record.update({"vision_status": "not_configured", "vision_result": {}, "vision_model": None, "vision_usage": {}})
        if not (needs_vision and vision_options and local_path is not None):
            _report(progress, "vision", position + 1, max(len(frame_records), 1))

    def run_vision(item: tuple[int, dict[str, Any], Path]) -> tuple[int, dict[str, Any], dict[str, Any]]:
        position, record, local_path = item
        visual = describe_image_structured(
            local_path,
            api_key=str((vision_options or {}).get("api_key") or ""),
            model=str((vision_options or {})["model"]),
            base_url=(vision_options or {}).get("base_url"),
            timeout=float((vision_options or {}).get("timeout", config["vision"]["timeout_seconds"])),
            max_retries=int((vision_options or {}).get("max_retries", 2)),
            max_tokens=int(config["vision"]["max_tokens"]),
            prompt=(vision_options or {}).get("prompt"),
        )
        return position, record, visual

    if pending_vision:
        # Each frame has an independent, strictly validated response.  The
        # bounded pool makes the persisted ``vision.concurrency`` setting real
        # without sharing database sessions or model clients across threads.
        workers = max(1, min(int(config["vision"].get("concurrency") or 1), len(pending_vision)))
        completed = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="media-vision") as executor:
            futures = {executor.submit(run_vision, item): item for item in pending_vision}
            for future in as_completed(futures):
                _check_cancelled(cancelled)
                position, record, _ = futures[future]
                try:
                    _, record, visual = future.result()
                    visual["result"]["evidence_frame_ids"] = [str(record.get("frame_index", position))]
                    record.update({
                        "vision_status": "succeeded", "vision_result": visual["result"],
                        "vision_model": visual["model"], "vision_usage": visual.get("usage") or {},
                        "vision_elapsed_ms": visual.get("elapsed_ms"),
                        "cloud_processing": config["vision"].get("execution") == "cloud",
                        "vision_called_at": datetime.now(timezone.utc).isoformat(),
                        "cloud_processing_reason": (
                            "configured_cloud_vision"
                            if config["vision"].get("execution") == "cloud"
                            else "local_vision"
                        ),
                    })
                except Exception as exc:
                    record["vision_status"] = "failed"
                    warnings.append(f"第 {position + 1} 帧视觉理解失败：{type(exc).__name__}: {exc}"[:1000])
                    if config["failure_mode"] == "fail":
                        for other in futures:
                            other.cancel()
                        raise
                completed += 1
                _report(progress, "vision", completed, len(pending_vision))

    for record in frame_records:
        record.pop("_local_path", None)

    for scene in scenes:
        summary, evidence = _scene_summary(scene, frame_records, segments)
        scene["summary"] = summary
        scene["evidence"] = evidence
    transcript = " ".join(item["text"] for item in segments if item["text"]).strip()
    vision_descriptions = list(dict.fromkeys(
        str((item.get("vision_result") or {}).get("scene_summary") or "").strip()
        for item in frame_records
        if str((item.get("vision_result") or {}).get("scene_summary") or "").strip()
    ))
    return {
        "type": media_type,
        "metadata": probe,
        "probe": probe,
        "transcript": transcript,
        "segments": segments,
        "audio_events": audio_events,
        "model": str(asr_options.get("model")) if asr_options else None,
        "model_version": asr_model_version,
        "transcription_time_seconds": transcription_time_seconds,
        "transcription_status": transcription_status,
        "scenes": scenes,
        "frames": frame_records,
        "vision_description": "\n".join(vision_descriptions),
        "vision_status": (
            "succeeded" if any(item["vision_status"] == "succeeded" for item in frame_records)
            else "failed" if any(item["vision_status"] == "failed" for item in frame_records)
            else "not_configured"
        ),
        "vision_model": str(vision_options.get("model")) if vision_options else None,
        "cloud_frame_count": sum(1 for item in frame_records if item.get("cloud_processing") and item.get("vision_status") == "succeeded"),
        "generate_summary": bool(config["asr"]["generate_summary"] or config["vision"]["generate_video_summary"]),
        "generate_chapters": bool(config["asr"]["generate_chapters"]),
        "warnings": list(dict.fromkeys(warnings)),
        "frame_count": len(frame_records),
        "scene_count": len(scenes),
        "segment_count": len(segments),
    }
