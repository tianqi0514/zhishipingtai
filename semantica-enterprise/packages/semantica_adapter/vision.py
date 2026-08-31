from __future__ import annotations

import base64
import mimetypes
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _video_duration(path: Path, timeout: float) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"视频时长读取失败：{(result.stderr or '未知错误')[-800:]}")
    return max(0.0, float(result.stdout.strip() or 0.0))


def _extract_keyframes(
    path: Path,
    destination: Path,
    *,
    count: int,
    timeout: float,
) -> list[tuple[float, Path]]:
    duration = _video_duration(path, timeout)
    timestamps = [0.0] if duration <= 0 else [duration * (index + 1) / (count + 1) for index in range(count)]
    frames: list[tuple[float, Path]] = []
    for index, timestamp in enumerate(timestamps):
        output = destination / f"frame-{index:02d}.jpg"
        result = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{timestamp:.3f}",
                "-i", str(path), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2",
                "-q:v", "3", "-y", str(output),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0 and output.exists() and output.stat().st_size:
            frames.append((round(timestamp, 3), output))
    if not frames:
        raise RuntimeError("视频没有提取到可用关键帧")
    return frames


def describe_visual(
    path: Path,
    media_type: str,
    *,
    api_key: str,
    model: str,
    base_url: str | None,
    timeout: float = 120,
    max_retries: int = 2,
    prompt: str | None = None,
    max_tokens: int = 700,
    keyframe_count: int = 3,
) -> dict[str, Any]:
    """Describe an image or sampled video frames through an OpenAI-compatible API.

    Only the configured model identifier and traceable frame timestamps are
    returned. Credentials and provider responses are never persisted.
    """
    client = OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        timeout=timeout,
        max_retries=max(0, min(int(max_retries), 10)),
    )
    instruction = prompt or (
        "请客观描述图像中的可见内容、文字、表格、图表和关键关系。"
        "不要猜测不可见信息，输出适合知识检索的简洁中文描述。"
    )
    with tempfile.TemporaryDirectory(prefix="semantica-vision-") as temporary_directory:
        if media_type == "video":
            frames = _extract_keyframes(
                path,
                Path(temporary_directory),
                count=max(1, min(int(keyframe_count), 8)),
                timeout=timeout,
            )
        else:
            frames = [(0.0, path)]
        content: list[dict[str, Any]] = [{"type": "text", "text": instruction}]
        for timestamp, frame in frames:
            if media_type == "video":
                content.append({"type": "text", "text": f"关键帧时间：{timestamp:.3f} 秒"})
            content.append({"type": "image_url", "image_url": {"url": _data_url(frame), "detail": "auto"}})
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=max(64, min(int(max_tokens), 4000)),
            temperature=0,
        )
    description = str(response.choices[0].message.content or "").strip()
    if not description:
        raise RuntimeError("视觉模型未返回描述")
    return {
        "vision_description": description,
        "vision_status": "succeeded",
        "vision_model": model,
        "keyframes": [{"time_start": timestamp, "time_end": timestamp} for timestamp, _ in frames],
    }
