#!/usr/bin/env python3
"""Bounded real-media resource acceptance for the Docker runtime.

This is deliberately separate from pytest: it creates duration fixtures with
real ffmpeg, performs actual frame extraction for 1/5/30 minute videos, calls
the configured local ASR runtime for a one-minute speech track, validates a
60-minute audio probe/cancellation boundary, and runs two media pipelines in
parallel.  Cloud model failure/retry is covered by model_protocol_live.py.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from packages.platform.media import probe_media
from packages.semantica_adapter.media_pipeline import process_media_file
from packages.semantica_adapter.transcription import TranscriptionCancelled, transcribe_media


ROOT = Path(__file__).resolve().parents[2]
SPEECH = ROOT / "tests/fixtures/media-generated/chinese-meeting.wav"


def run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=900)
    if completed.returncode:
        raise RuntimeError((completed.stderr or completed.stdout)[-1200:])


def make_video(path: Path, seconds: int) -> None:
    run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=1", "-t", str(seconds),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", "-y", str(path),
    ])


def make_audio(path: Path, seconds: int, *, speech: bool) -> None:
    if speech:
        run([
            "ffmpeg", "-nostdin", "-v", "error", "-stream_loop", "-1",
            "-i", str(SPEECH), "-t", str(seconds), "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "32k", "-y", str(path),
        ])
    else:
        run([
            "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi",
            "-i", "anullsrc=r=16000:cl=mono", "-t", str(seconds),
            "-c:a", "libmp3lame", "-b:a", "16k", "-y", str(path),
        ])


def sample_video(path: Path, work: Path, interval: float, max_frames: int) -> dict[str, Any]:
    started = time.perf_counter()
    result = process_media_file(
        path,
        policy={
            "max_duration_seconds": 4000,
            "video": {"extract_audio_track": False, "asr_enabled": False},
            "frame": {
                "mode": "fixed_interval", "interval_seconds": interval,
                "max_frames": max_frames, "max_image_edge": 640,
                "perceptual_hash_enabled": False,
            },
            "ocr": {"enabled": False}, "asr": {"enabled": False},
            "vision": {"enabled": False},
        },
        working_directory=work,
    )
    return {
        "duration_seconds": result["probe"]["duration_seconds"],
        "frames": result["frame_count"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "bytes": path.stat().st_size,
    }


def main() -> None:
    if not SPEECH.exists():
        raise RuntimeError("请先生成媒体验收 fixture")
    report: dict[str, Any] = {"videos": {}, "audio": {}, "concurrency": {}}
    with tempfile.TemporaryDirectory(prefix="media-resource-") as temporary:
        root = Path(temporary)
        definitions = [("1m", 60, 10.0, 8), ("5m", 300, 30.0, 12), ("30m", 1800, 120.0, 18)]
        video_paths: dict[str, Path] = {}
        for name, seconds, interval, limit in definitions:
            path = root / f"video-{name}.mp4"
            make_video(path, seconds)
            video_paths[name] = path
            report["videos"][name] = sample_video(path, root / f"work-{name}", interval, limit)

        speech = root / "speech-1m.mp3"
        make_audio(speech, 60, speech=True)
        started = time.perf_counter()
        transcription = transcribe_media(
            speech, "audio", api_key="local-runtime", model="sensevoice",
            base_url="http://asr-runtime:8001/v1", timeout=600, max_retries=1,
            language="zh", segment_seconds=30, segment_overlap_seconds=0.5,
        )
        report["audio"]["1m_real_asr"] = {
            "duration_seconds": transcription["duration"],
            "segments": len(transcription["segments"]),
            "text_chars": len(transcription["transcript"]),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "model": transcription["model"],
        }

        long_audio = root / "silence-60m.mp3"
        make_audio(long_audio, 3600, speech=False)
        long_probe = probe_media(long_audio)
        cancelled = False
        started = time.perf_counter()
        try:
            transcribe_media(
                long_audio, "audio", api_key="local-runtime", model="sensevoice",
                base_url="http://asr-runtime:8001/v1", timeout=600,
                segment_seconds=30, cancelled=lambda: True,
            )
        except TranscriptionCancelled:
            cancelled = True
        report["audio"]["60m_boundary"] = {
            "duration_seconds": long_probe["duration_seconds"],
            "bytes": long_audio.stat().st_size,
            "cancelled_before_model_call": cancelled,
            "normalise_and_cancel_seconds": round(time.perf_counter() - started, 3),
        }

        concurrent_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    sample_video, video_paths["1m"], root / f"parallel-{index}", 15.0, 6
                )
                for index in range(2)
            ]
            outcomes = [future.result() for future in futures]
        report["concurrency"] = {
            "jobs": len(outcomes),
            "all_succeeded": all(item["frames"] > 0 for item in outcomes),
            "elapsed_seconds": round(time.perf_counter() - concurrent_started, 3),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
