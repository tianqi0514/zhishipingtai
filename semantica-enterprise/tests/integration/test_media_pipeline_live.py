from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from packages.semantica_adapter.media_pipeline import process_media_file


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg unavailable")
def test_real_video_probe_scene_and_bounded_frame_extraction(tmp_path: Path) -> None:
    video = tmp_path / "nexusone-fixture.mp4"
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=4",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=16000",
            "-t", "3", "-c:v", "libx264", "-preset", "ultrafast",
            "-threads", "1", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-y", str(video),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        pytest.skip(f"fixture codec unavailable: {result.stderr[-300:]}")

    stages: list[tuple[str, int, int]] = []
    output = process_media_file(
        video,
        policy={
            "processing_mode": "local",
            "frame": {
                "mode": "fixed_interval", "interval_seconds": 1,
                "max_frames": 3, "perceptual_hash_distance": 0,
            },
            "ocr": {"enabled": False},
            "asr": {"enabled": False},
            "vision": {"enabled": False},
        },
        working_directory=tmp_path / "work",
        progress=lambda stage, completed, total: stages.append((stage, completed, total)),
    )

    assert output["type"] == "video"
    assert output["probe"]["duration_seconds"] == pytest.approx(3, abs=0.2)
    assert 1 <= output["frame_count"] <= 3
    assert output["scene_count"] == 1
    assert output["transcription_status"] == "not_configured"
    assert {item["selection_reason"] for item in output["frames"]} <= {
        "forced_boundary", "interval_sample"
    }
    assert any(stage == "frame_extraction" for stage, _, _ in stages)
