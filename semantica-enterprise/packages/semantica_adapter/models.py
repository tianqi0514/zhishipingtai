from __future__ import annotations

import base64
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from packages.semantica_adapter.extract import _effective_temperature


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

    if not api_key:
        raise ValueError("API Key 未配置")
    settings = config or {}
    timeout = float(settings.get("timeout", 20))
    max_retries = max(0, min(int(settings.get("max_retries", settings.get("retry", 2))), 10))
    temperature = _effective_temperature(model_name, float(settings.get("temperature", 0)))
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
        # A real 1x1 PNG keeps connection testing inexpensive while exercising
        # the configured model's image-capable chat endpoint.
        pixel = base64.b64encode(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00"
            b"\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        ).decode("ascii")
        response = client.chat.completions.create(
            model=model_name,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "只回答图像的主色。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{pixel}"}},
                ],
            }],
            max_tokens=8,
            temperature=temperature,
        )
    elif model_kind == "llm":
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "只回答：连接成功"}],
            max_tokens=8,
            temperature=temperature,
        )
    else:
        raise ValueError(f"不支持的模型类型：{model_kind}")
    if not response.choices:
        raise RuntimeError("模型未返回回答")
    return {"status": "ok", "model": model_name, "provider": provider, "request": model_kind}
