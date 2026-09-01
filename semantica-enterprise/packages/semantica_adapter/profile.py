from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .extract import _effective_temperature
from .llm_transport import apply_model_transport_options


@dataclass(frozen=True)
class DeterministicProfile:
    language: str
    quality_score: float
    completeness_score: float
    readability_score: float
    structure_score: float
    media_confidence: float | None
    duplicate_ratio: float
    quality_issues: list[str]
    recommended_actions: list[str]
    metrics: dict[str, Any]


def _score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _language(text: str) -> str:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if chinese > latin * 0.2:
        return "zh-CN" if latin < chinese else "zh-CN/en"
    return "en" if latin else "unknown"


def build_deterministic_profile(elements: Iterable[Any]) -> DeterministicProfile:
    rows = list(elements)
    texts = [str(getattr(item, "text", "") or "") for item in rows]
    full_text = "\n".join(texts)
    text_length = len(full_text)
    empty_count = sum(not text.strip() for text in texts)
    control_count = sum(
        1 for char in full_text if char not in {"\n", "\r", "\t"} and ord(char) < 32
    )
    normalized = [re.sub(r"\s+", " ", text).strip().casefold() for text in texts if text.strip()]
    counts = Counter(normalized)
    duplicate_items = sum(count - 1 for count in counts.values() if count > 1)
    duplicate_ratio = duplicate_items / max(len(normalized), 1)
    page_numbers = sorted(
        {int(item.page_number) for item in rows if getattr(item, "page_number", None) is not None}
    )
    missing_pages = []
    if page_numbers:
        missing_pages = sorted(set(range(page_numbers[0], page_numbers[-1] + 1)) - set(page_numbers))
    types = Counter(str(getattr(item, "element_type", "text")) for item in rows)
    media_states: list[float] = []
    for item in rows:
        metadata = getattr(item, "element_metadata", None) or getattr(item, "metadata", {}) or {}
        ocr = metadata.get("ocr") if isinstance(metadata, dict) else None
        if isinstance(ocr, dict) and ocr.get("confidence") is not None:
            media_states.append(float(ocr["confidence"]) * (100 if float(ocr["confidence"]) <= 1 else 1))
        status = metadata.get("transcription_status") if isinstance(metadata, dict) else None
        if status == "succeeded":
            media_states.append(100.0)
        elif status in {"failed", "not_configured"}:
            media_states.append(0.0)
    media_confidence = _score(sum(media_states) / len(media_states)) if media_states else None

    completeness = 100.0
    if not text_length:
        completeness = 0.0
    else:
        completeness -= min(45.0, 100.0 * empty_count / max(len(rows), 1))
        completeness -= min(25.0, len(missing_pages) * 5.0)
        if types.get("audio", 0) + types.get("video", 0) and not types.get("transcript", 0):
            completeness -= 30.0
    sentences = [item.strip() for item in re.split(r"[。！？!?\n]+", full_text) if item.strip()]
    average_sentence = sum(map(len, sentences)) / max(len(sentences), 1)
    control_ratio = control_count / max(text_length, 1)
    readability = 100.0 - min(65.0, control_ratio * 2000)
    if average_sentence > 120:
        readability -= min(25.0, (average_sentence - 120) / 8)
    if text_length < 40:
        readability -= 20.0
    structural_paths = {str(getattr(item, "structural_path", "")) for item in rows}
    structure = 45.0 + min(35.0, 8.0 * len(types)) + min(20.0, 100.0 * len(structural_paths) / max(len(rows), 1))
    quality = (
        0.38 * _score(completeness)
        + 0.27 * _score(readability)
        + 0.25 * _score(structure)
        + 0.10 * (media_confidence if media_confidence is not None else 100.0)
    )
    quality -= duplicate_ratio * 25.0
    issues: list[str] = []
    actions: list[str] = []
    if not text_length:
        issues.append("没有可用正文")
        actions.append("检查文件是否损坏或调整解析策略")
    if missing_pages:
        issues.append(f"页码不连续：缺少 {missing_pages[:20]}")
        actions.append("检查原文件缺页或分页解析结果")
    if control_ratio >= 0.005:
        issues.append("正文包含较多异常控制字符")
        actions.append("尝试启用 Docling/OCR 重新解析")
    if duplicate_ratio >= 0.2:
        issues.append(f"重复内容比例较高：{duplicate_ratio:.0%}")
        actions.append("启用重复片段清理策略")
    if types.get("audio", 0) + types.get("video", 0) and not types.get("transcript", 0):
        issues.append("音视频尚未生成转写")
        actions.append("配置并启用默认 ASR 模型后重试解析")
    if media_confidence is not None and media_confidence < 60:
        issues.append("OCR/ASR 可信度偏低或未完成")
        actions.append("检查媒介质量、语言设置和模型配置")
    return DeterministicProfile(
        language=_language(full_text),
        quality_score=_score(quality),
        completeness_score=_score(completeness),
        readability_score=_score(readability),
        structure_score=_score(structure),
        media_confidence=media_confidence,
        duplicate_ratio=round(duplicate_ratio, 4),
        quality_issues=issues,
        recommended_actions=actions,
        metrics={
            "element_count": len(rows),
            "text_length": text_length,
            "empty_elements": empty_count,
            "control_character_ratio": round(control_ratio, 6),
            "average_sentence_length": round(average_sentence, 2),
            "missing_pages": missing_pages,
            "element_types": dict(types),
        },
    )


def analyze_profile_with_model(
    text: str,
    *,
    model: str,
    api_key: str,
    base_url: str | None,
    taxonomy: list[str] | None = None,
    summary_length: int = 240,
    tag_count: int = 8,
    timeout: float = 60,
    max_retries: int = 2,
    generator: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prompt = f"""你是组织级知识治理器。只输出 JSON 对象，不要 Markdown，不得补充原文不存在的事实。
字段：summary（不超过{summary_length}字）、classification、document_type、tags（不超过{tag_count}个）、keywords（不超过{tag_count}个）、main_objects、time_range（对象，包含 start/end）、quality_issues（数组）。
分类体系：{json.dumps(taxonomy or ['产品资料','制度规范','项目材料','经营分析','会议材料','技术资料','合同法务','其他'], ensure_ascii=False)}
正文：\n{text[:30000]}"""
    if generator is None:
        from semantica.semantic_extract.providers import OpenAIProvider

        provider = apply_model_transport_options(
            OpenAIProvider(api_key=api_key, model=model, base_url=base_url),
            timeout=timeout,
            max_retries=max_retries,
        )
        generator = lambda value: provider.generate_structured(
            value, temperature=_effective_temperature(model, 0.1)
        )
    result = generator(prompt)
    if not isinstance(result, dict):
        raise ValueError("模型治理结果不是 JSON 对象")
    return {
        "summary": str(result.get("summary") or "")[: max(50, summary_length * 2)],
        "classification": str(result.get("classification") or "未分类")[:300],
        "document_type": str(result.get("document_type") or "其他")[:100],
        "tags": [str(item)[:100] for item in (result.get("tags") or [])[:tag_count]],
        "keywords": [str(item)[:100] for item in (result.get("keywords") or [])[:tag_count]],
        "main_objects": [str(item)[:200] for item in (result.get("main_objects") or [])[:20]],
        "time_range": result.get("time_range") if isinstance(result.get("time_range"), dict) else {},
        "quality_issues": [str(item)[:500] for item in (result.get("quality_issues") or [])[:20]],
    }
