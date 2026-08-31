from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI


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
    with tempfile.TemporaryDirectory(prefix="semantica-asr-") as temporary_directory:
        input_path = path
        if media_type == "video":
            input_path = Path(temporary_directory) / "audio.mp3"
            result = subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn",
                    "-ac", "1", "-ar", "16000", "-b:a", "64k", "-y", str(input_path),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if result.returncode != 0 or not input_path.exists():
                raise RuntimeError(f"视频音轨提取失败：{(result.stderr or '未知错误')[-800:]}")
        kwargs: dict[str, Any] = {
            "model": model,
            "response_format": "verbose_json",
        }
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt
        with input_path.open("rb") as stream:
            response = client.audio.transcriptions.create(file=stream, **kwargs)
    data = response.model_dump() if hasattr(response, "model_dump") else dict(response)
    segments = [
        {
            "start": item.get("start"),
            "end": item.get("end"),
            "text": item.get("text", ""),
        }
        for item in (data.get("segments") or [])
        if isinstance(item, dict)
    ]
    return {
        "transcript": str(data.get("text") or ""),
        "segments": segments,
        "language": data.get("language") or language,
        "duration": data.get("duration"),
        "model": model,
        "transcription_status": "succeeded",
    }
