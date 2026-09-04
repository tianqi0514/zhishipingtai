from __future__ import annotations

import base64
import io
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from packages.semantica_adapter.extract import _effective_temperature
from packages.semantica_adapter.llm_transport import model_request_extra_body


def test_model_connection(
    *,
    model_kind: str,
    provider: str,
    model_name: str,
    api_key: str | None,
    base_url: str | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if provider in {"huggingface", "bge", "fastembed"}:
        if model_kind == "embedding":
            from packages.semantica_adapter.embedding import SemanticEmbedder

            embedder = SemanticEmbedder(
                model_name,
                {**(config or {}), "method": "fastembed" if provider == "fastembed" else "sentence_transformers"},
            )
            vector = embedder.embed_query("知识平台连通性测试")
            if vector is None or len(vector) == 0:
                raise RuntimeError("模型未返回向量")
            dimension = len(vector)
            return {
                "status": "ok",
                "dimension": dimension,
                "local": True,
                "method": embedder.method,
                "message": f"本地模型实测成功（{dimension} 维，无需 API Key）",
            }
        from semantica.llms import HuggingFaceLLM

        llm = HuggingFaceLLM(model_name=model_name)
        if not llm.is_available():
            raise RuntimeError("本地 HuggingFace 模型不可用")
        return {"status": "ok", "local": True}

    if not api_key and (config or {}).get("local_runtime"):
        api_key = "local-runtime"
    if not api_key:
        raise ValueError("API Key 未配置")
    settings = config or {}
    timeout = float(settings.get("timeout", 20))
    max_retries = max(0, min(int(settings.get("max_retries", settings.get("retry", 2))), 10))
    temperature = _effective_temperature(model_name, float(settings.get("temperature", 0)))
    extra_body = model_request_extra_body(settings.get("parameters"))
    if model_kind == "reranker":
        if not base_url:
            raise ValueError("重排模型未配置 API 地址")
        endpoint = str(settings.get("endpoint_path") or "/rerank")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        with httpx.Client(timeout=timeout) as http:
            for attempt in range(max_retries + 1):
                response = http.post(
                    f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}",
                    headers=headers,
                    json={
                        "model": model_name,
                        "query": "知识平台",
                        "documents": ["知识平台连通性测试", "天气信息"],
                        "top_n": 2,
                    },
                )
                if response.status_code not in {408, 409, 429} and response.status_code < 500:
                    break
                if attempt == max_retries:
                    break
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 0.25 * (2**attempt)
                time.sleep(min(max(delay, 0), 2))
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("results") or payload.get("data") or []
        if not rows or not isinstance(rows[0].get("relevance_score", rows[0].get("score")), (int, float)):
            raise RuntimeError("重排模型未返回可识别分数")
        return {"status": "ok", "model": model_name, "provider": provider, "request": "rerank"}

    client = OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        timeout=timeout,
        max_retries=max_retries,
    )
    if model_kind == "embedding":
        response = client.embeddings.create(model=model_name, input=["知识平台连通性测试"])
        dimension = len(response.data[0].embedding) if response.data else 0
        if not dimension:
            raise RuntimeError("向量模型未返回向量")
        return {"status": "ok", "model": model_name, "provider": provider, "dimension": dimension}
    if model_kind == "asr":
        with tempfile.TemporaryDirectory(prefix="semantica-model-test-") as temporary_directory:
            sample = Path(temporary_directory) / "silence.wav"
            with wave.open(str(sample), "wb") as audio:
                audio.setparams((1, 2, 16000, 1600, "NONE", "not compressed"))
                audio.writeframes(b"\x00\x00" * 1600)
            with sample.open("rb") as stream:
                response = client.audio.transcriptions.create(file=stream, model=model_name)
        if response is None:
            raise RuntimeError("语音模型未返回响应")
        return {"status": "ok", "model": model_name, "provider": provider, "request": "transcription"}
    if model_kind == "vision":
        # Exercise visual recognition, not merely endpoint availability.
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), (255, 0, 0)).save(buffer, format="PNG")
        pixel = base64.b64encode(buffer.getvalue()).decode("ascii")
        request: dict[str, Any] = dict(
            model=model_name,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "识别这张纯色图片，只回答主色名称。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{pixel}"}},
                ],
            }],
            # Reasoning-capable multimodal models can spend a small token
            # budget before emitting the visible answer.
            max_tokens=256,
            temperature=temperature,
        )
        if extra_body:
            request["extra_body"] = extra_body
        response = client.chat.completions.create(**request)
    elif model_kind == "llm":
        request = dict(
            model=model_name,
            messages=[{"role": "user", "content": "只回答：连接成功"}],
            max_tokens=8,
            temperature=temperature,
        )
        if extra_body:
            request["extra_body"] = extra_body
        response = client.chat.completions.create(**request)
    else:
        raise ValueError(f"不支持的模型类型：{model_kind}")
    if not response.choices:
        raise RuntimeError("模型未返回回答")
    if model_kind == "vision":
        answer = str(response.choices[0].message.content or "").strip().casefold()
        if "红" not in answer and "red" not in answer:
            raise RuntimeError("视觉模型返回了响应，但未正确识别测试图片")
    return {"status": "ok", "model": model_name, "provider": provider, "request": model_kind}
