from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from .llm_transport import apply_model_transport_options


@dataclass(frozen=True)
class ExtractionOutput:
    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    events: list[dict[str, Any]]


@dataclass(frozen=True)
class ExtractionBatch:
    """A bounded model request assembled from adjacent retrieval chunks.

    Retrieval chunks remain unchanged.  The batch only reduces model round
    trips; extracted items are mapped back to one of the original chunks so
    provenance and clickable citations keep their original precision.
    """

    ordinal: int
    chunks: tuple[Any, ...]
    text: str
    chunk_key: str


def build_extraction_batches(
    chunks: list[Any],
    *,
    target_chars: int = 2400,
    max_chunks: int = 12,
) -> list[ExtractionBatch]:
    """Merge adjacent small chunks into deterministic, bounded LLM requests."""
    target_chars = max(400, min(int(target_chars), 12000))
    max_chunks = max(1, min(int(max_chunks), 50))
    batches: list[ExtractionBatch] = []
    pending: list[Any] = []
    pending_chars = 0

    def flush() -> None:
        nonlocal pending, pending_chars
        if not pending:
            return
        text = "\n\n".join(str(chunk.text).strip() for chunk in pending if str(chunk.text).strip())
        source = "|".join(str(chunk.chunk_id) for chunk in pending)
        batches.append(
            ExtractionBatch(
                ordinal=len(batches),
                chunks=tuple(pending),
                text=text,
                chunk_key=hashlib.sha256(f"extraction-batch-v1:{source}".encode()).hexdigest(),
            )
        )
        pending = []
        pending_chars = 0

    for chunk in chunks:
        text = str(chunk.text or "").strip()
        if not text:
            continue
        separator_chars = 2 if pending else 0
        if pending and (
            len(pending) >= max_chunks
            or pending_chars + separator_chars + len(text) > target_chars
        ):
            flush()
            separator_chars = 0
        pending.append(chunk)
        pending_chars += separator_chars + len(text)
        if len(text) >= target_chars:
            flush()
    flush()
    return batches


def source_chunk_for_extraction(batch: ExtractionBatch, *evidence: Any) -> Any | None:
    """Resolve a model item to the original chunk containing its evidence.

    Unsupported items are deliberately rejected instead of being attached to
    an arbitrary chunk.  This keeps model batching from weakening provenance.
    """
    candidates = []
    for value in evidence:
        normalized = " ".join(str(value or "").casefold().split())
        if len(normalized) >= 2 and normalized not in candidates:
            candidates.append(normalized)
    best = None
    best_score = 0
    for chunk in batch.chunks:
        chunk_text = " ".join(str(chunk.text or "").casefold().split())
        score = sum(min(len(value), 500) for value in candidates if value in chunk_text)
        if score > best_score:
            best = chunk
            best_score = score
    return best


def _confidence(value: Any, default: float = 0.7) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in (value or []) if isinstance(item, dict)]


def _effective_temperature(model: str, configured: float) -> float:
    # Kimi K3 currently exposes an OpenAI-compatible endpoint but only accepts
    # temperature=1. Keep this provider quirk inside the adapter so policies
    # remain portable across models.
    if model.strip().casefold() == "kimi-k3":
        return 1.0
    return configured


def extract_semantics(
    text: str,
    *,
    chunk_key: str,
    api_key: str,
    model: str,
    base_url: str | None,
    entity_types: list[str] | None = None,
    relation_types: list[str] | None = None,
    temperature: float = 0.1,
    timeout: float = 60,
    max_retries: int = 2,
    request_parameters: dict[str, Any] | None = None,
    generator: Callable[[str], dict[str, Any]] | None = None,
) -> ExtractionOutput:
    """Extract validated Chinese entities, relations and events using Semantica's LLM provider."""
    prompt = f"""你是组织知识抽取器。只输出一个 JSON 对象，不要 Markdown。
格式：{{"entities":[{{"text":"原文名称","type":"类型","confidence":0.9,"attributes":{{}}}}],
"relations":[{{"subject":"实体名","predicate":"关系","object":"实体名或值","confidence":0.9,"evidence":"原文证据"}}],
"events":[{{"type":"事件类型","trigger":"触发词","participants":["参与者"],"time":null,"confidence":0.9,"evidence":"原文证据"}}]}}
实体类型优先使用：{json.dumps(entity_types or ['组织','人物','产品','地点','时间','指标','制度'], ensure_ascii=False)}
关系类型优先使用：{json.dumps(relation_types or [], ensure_ascii=False)}
禁止臆测；没有内容时返回空数组。文本：
{text}"""
    if generator is None:
        from semantica.semantic_extract.providers import OpenAIProvider

        provider = apply_model_transport_options(
            OpenAIProvider(api_key=api_key, model=model, base_url=base_url),
            timeout=timeout,
            max_retries=max_retries,
            request_parameters=request_parameters,
        )
        generator = lambda value: provider.generate_structured(
            value,
            temperature=_effective_temperature(model, temperature),
        )
    raw = generator(prompt)
    if not isinstance(raw, dict):
        raise ValueError("模型抽取结果不是 JSON 对象")

    entities: list[dict[str, Any]] = []
    for index, item in enumerate(_items(raw.get("entities"))):
        name = str(item.get("text") or item.get("name") or "").strip()
        if not name:
            continue
        mention_id = hashlib.sha256(f"{chunk_key}:entity:{index}:{name}".encode()).hexdigest()
        entities.append(
            {
                "mention_id": mention_id,
                "text": name[:500],
                "entity_type": str(item.get("type") or "其他")[:100],
                "confidence": _confidence(item.get("confidence")),
                "attributes": item.get("attributes") if isinstance(item.get("attributes"), dict) else {},
            }
        )

    relations: list[dict[str, Any]] = []
    for item in _items(raw.get("relations")):
        subject = str(item.get("subject") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        object_name = str(item.get("object") or "").strip()
        if subject and predicate and object_name:
            relations.append(
                {
                    "subject_name": subject[:500],
                    "predicate": predicate[:200],
                    "object_name": object_name[:500],
                    "confidence": _confidence(item.get("confidence")),
                    "evidence": str(item.get("evidence") or "")[:4000],
                    "attributes": item.get("attributes") if isinstance(item.get("attributes"), dict) else {},
                }
            )

    events: list[dict[str, Any]] = []
    for item in _items(raw.get("events")):
        event_type = str(item.get("type") or "").strip()
        trigger = str(item.get("trigger") or "").strip()
        if event_type and trigger:
            participants = item.get("participants") if isinstance(item.get("participants"), list) else []
            events.append(
                {
                    "event_type": event_type[:200],
                    "trigger": trigger[:500],
                    "participants": [str(value)[:500] for value in participants],
                    "event_time": str(item.get("time"))[:200] if item.get("time") else None,
                    "confidence": _confidence(item.get("confidence")),
                    "evidence": str(item.get("evidence") or "")[:4000],
                }
            )
    return ExtractionOutput(entities=entities, relations=relations, events=events)
