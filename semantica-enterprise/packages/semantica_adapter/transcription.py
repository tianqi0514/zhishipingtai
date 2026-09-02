from __future__ import annotations

import subprocess
import tempfile
import re
import json
import math
import time
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI


_SENSEVOICE_TAG = re.compile(r"<\|([^|<>]+)\|>")
_LANGUAGE_TAGS = {"zh", "en", "ja", "ko", "yue", "auto", "nospeech"}


class TranscriptionCancelled(RuntimeError):
    pass


def _speech_annotations(text: str) -> tuple[str, list[str]]:
    tags = [value.strip() for value in _SENSEVOICE_TAG.findall(text or "") if value.strip()]
    clean = _SENSEVOICE_TAG.sub("", text or "").strip()
    events = [value for value in tags if value.casefold() not in _LANGUAGE_TAGS]
    return clean, list(dict.fromkeys(events))


def transcribe_media(
    path: Path,
    media_type: str,
    *,
    api_key: str,
    model: str,
    base_url: str | None,
    timeout: float = 300,
    max_retries: int = 2,
    language: str | None = None,
    prompt: str | None = None,
    segment_seconds: int = 30,
    segment_overlap_seconds: float = 0.5,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Transcribe audio/video with an OpenAI-compatible ASR endpoint.

    Video demuxing is local and deterministic; model credentials never enter
    the parsed element metadata.
    """
    client = OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        timeout=timeout,
        max_retries=max(0, min(int(max_retries), 10)),
    )
    started = time.perf_counter()
    segments: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    transcript_parts: list[str] = []
    detected_language = language
    with tempfile.TemporaryDirectory(prefix="semantica-asr-") as temporary_directory:
        root = Path(temporary_directory)
        input_path = root / "normalized.wav"
        result = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(input_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0 or not input_path.exists() or not input_path.stat().st_size:
            raise RuntimeError(f"音轨标准化失败：{(result.stderr or '未知错误')[-800:]}")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(input_path)],
            capture_output=True, text=True, timeout=min(timeout, 60), check=False,
        )
        if probe.returncode:
            raise RuntimeError(f"音频时长探测失败：{(probe.stderr or '未知错误')[-800:]}")
        duration = max(0.0, float((json.loads(probe.stdout or "{}").get("format") or {}).get("duration") or 0))
        segment_seconds = max(1, min(int(segment_seconds), 600))
        overlap = max(0.0, min(float(segment_overlap_seconds), segment_seconds - 0.01))
        step = max(0.01, segment_seconds - overlap)
        starts = [index * step for index in range(max(1, math.ceil(max(duration - overlap, 0.01) / step)))]
        kwargs: dict[str, Any] = {"model": model, "response_format": "verbose_json"}
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt
        for chunk_index, start in enumerate(starts):
            if cancelled and cancelled():
                raise TranscriptionCancelled("语音转写已取消")
            length = min(float(segment_seconds), max(0.01, duration - start))
            chunk_path = root / f"segment-{chunk_index:05d}.wav"
            split = subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}",
                    "-t", f"{length:.3f}", "-i", str(input_path), "-c", "copy", "-y", str(chunk_path),
                ],
                capture_output=True, text=True, timeout=min(timeout, 120), check=False,
            )
            if split.returncode or not chunk_path.exists() or not chunk_path.stat().st_size:
                raise RuntimeError(f"音频分段失败：{(split.stderr or '未知错误')[-800:]}")
            with chunk_path.open("rb") as stream:
                response = client.audio.transcriptions.create(file=stream, **kwargs)
            data = response.model_dump() if hasattr(response, "model_dump") else dict(response)
            detected_language = data.get("language") or detected_language
            response_text, response_events = _speech_annotations(str(data.get("text") or ""))
            accepted = 0
            for item in (data.get("segments") or []):
                if not isinstance(item, dict):
                    continue
                local_start = float(item.get("start") or 0); local_end = float(item.get("end") or local_start)
                # Overlap exists for acoustic context. Keep each time range only
                # once by assigning it to the later chunk only after its overlap.
                if chunk_index and (local_start + local_end) / 2 < overlap:
                    continue
                clean_text, annotations = _speech_annotations(str(item.get("text") or ""))
                absolute_start, absolute_end = start + local_start, start + local_end
                segments.append({
                    "start": round(absolute_start, 3), "end": round(absolute_end, 3), "text": clean_text,
                    "raw_text": item.get("text", ""), "speaker": item.get("speaker"),
                    "confidence": item.get("confidence"), "words": item.get("words") or [],
                    "events": annotations,
                })
                transcript_parts.append(clean_text); accepted += 1
                events.extend({"name": name, "start": round(absolute_start, 3), "end": round(absolute_end, 3)} for name in annotations)
            if not accepted and response_text:
                segments.append({
                    "start": round(start, 3), "end": round(start + length, 3), "text": response_text,
                    "raw_text": str(data.get("text") or ""), "speaker": None, "confidence": None,
                    "words": [], "events": response_events,
                })
                transcript_parts.append(response_text)
                events.extend({"name": name, "start": round(start, 3), "end": round(start + length, 3)} for name in response_events)
            if progress:
                progress(chunk_index + 1, len(starts))
    transcript = " ".join(value for value in transcript_parts if value).strip()
    return {
        "transcript": transcript,
        "segments": segments,
        "audio_events": events,
        "language": detected_language,
        "duration": duration,
        "model": model,
        "model_version": None,
        "transcription_time_seconds": round(time.perf_counter() - started, 3),
        "warnings": [],
        "transcription_status": "succeeded",
    }
