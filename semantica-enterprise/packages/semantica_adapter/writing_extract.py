from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .extract import _effective_temperature
from .llm_transport import apply_model_transport_options


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WritingEvidenceInput(StrictModel):
    evidence_id: str = Field(min_length=8, max_length=100)
    text: str = Field(min_length=1, max_length=20_000)
    locator: dict[str, Any] = Field(default_factory=dict)


class EntityMentionCandidate(StrictModel):
    mention_text: str = Field(min_length=1, max_length=500)
    canonical_name: str = Field(min_length=1, max_length=500)
    entity_type: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)
    needs_confirmation: bool = False


class ClaimCandidate(StrictModel):
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=300)
    object_value: str | int | float | bool | None
    claim_type: Literal["assertion", "requirement", "prediction", "recommendation", "opinion"]
    value_type: Literal["string", "number", "boolean", "entity", "date", "datetime"] = "string"
    unit: str | None = Field(default=None, max_length=64)
    time_scope: dict[str, Any] = Field(default_factory=dict)
    applicable_scope: dict[str, Any] = Field(default_factory=dict)
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)
    needs_confirmation: bool = False


class RelationHintCandidate(StrictModel):
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=300)
    object: str = Field(min_length=1, max_length=500)
    time_scope: dict[str, Any] = Field(default_factory=dict)
    applicable_scope: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)
    needs_confirmation: bool = False


class MetricMentionCandidate(StrictModel):
    name: str = Field(min_length=1, max_length=300)
    value: str | int | float
    value_type: Literal["integer", "number", "string"] = "number"
    unit: str | None = Field(default=None, max_length=64)
    time_scope: dict[str, Any] = Field(default_factory=dict)
    applicable_scope: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)
    needs_confirmation: bool = False


class SampleChapterCandidate(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    responsibility: str = Field(default="", max_length=2000)
    level: int = Field(default=1, ge=1, le=6)
    citation_required: bool = True


class SampleProfileCandidate(StrictModel):
    document_type: str = Field(default="", max_length=200)
    target_audience: str = Field(default="", max_length=500)
    chapters: list[SampleChapterCandidate] = Field(default_factory=list, max_length=100)
    style: dict[str, Any] = Field(default_factory=dict)
    table_patterns: list[str] = Field(default_factory=list, max_length=30)
    attachment_patterns: list[str] = Field(default_factory=list, max_length=30)


class JointWritingExtraction(StrictModel):
    entities: list[EntityMentionCandidate] = Field(default_factory=list, max_length=200)
    claims: list[ClaimCandidate] = Field(default_factory=list, max_length=300)
    relations: list[RelationHintCandidate] = Field(default_factory=list, max_length=300)
    metrics: list[MetricMentionCandidate] = Field(default_factory=list, max_length=200)
    sample_profile: SampleProfileCandidate | None = None
    ambiguities: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def sample_does_not_mix_business_facts(self) -> "JointWritingExtraction":
        return self


def writing_extraction_prompt(
    evidence: list[WritingEvidenceInput],
    *,
    material_role: str,
) -> str:
    signed_evidence = [item.model_dump() for item in evidence]
    sample_only = material_role == "sample_style"
    boundary = (
        "本批次是样稿。只提取 sample_profile；entities、claims、relations、metrics 必须为空。"
        if sample_only
        else "本批次是业务材料。sample_profile 必须为 null。"
    )
    return f"""你是组织专业写作知识抽取器。只输出一个 JSON 对象，不要 Markdown，不要解释。

目标：一次联合识别实体提及、材料主张、数值指标和关系线索；不能把模型推断当作材料主张。
{boundary}

强制规则：
1. 每个结果必须引用下面签发的 evidence_id；禁止创造、改写或省略 ID。
2. 只抽取原文明确表达的内容。没有依据时返回空数组。
3. Claim 表示“材料声称什么”，不是已核验事实；不得输出 verified 状态。
4. 精确数值保留原值、单位、时间和适用范围；不能自行计算派生值。
5. 主体、客体、单位或时间不清时设置 needs_confirmation=true，并在 ambiguities 中说明。
6. aliases 只保存原文实际出现或同一批次明确说明的别名。
7. 输出严格满足以下顶层键，不能增加字段：entities、claims、relations、metrics、sample_profile、ambiguities。
8. 输出紧凑 JSON；空值或默认值字段可以省略，同义实体和同义陈述必须合并，禁止复述原文。
9. 每批最多输出30个实体、40个Claim、30条关系、30个指标；只保留对专业写作有用的原子知识。
10. metrics 只列原文明确出现的数值指标；relations 只列实体到实体的关系，不得把普通数值Claim重复扩写成关系。

材料用途：{material_role}
签发 Evidence：
{json.dumps(signed_evidence, ensure_ascii=False)}"""


def _validate_evidence_ids(result: JointWritingExtraction, allowed_ids: set[str]) -> None:
    referenced: list[str] = []
    for item in [*result.entities, *result.claims, *result.relations, *result.metrics]:
        referenced.extend(item.evidence_ids)
    invalid = sorted(set(referenced) - allowed_ids)
    if invalid:
        raise ValueError(f"模型返回了未签发的 Evidence ID：{', '.join(invalid[:5])}")


def writing_output_token_budget(
    evidence: list[WritingEvidenceInput], configured_max_tokens: int,
) -> int:
    """Return a bounded response budget proportional to signed source text."""

    source_chars = sum(len(item.text) for item in evidence)
    return max(1024, min(int(configured_max_tokens), 2048, source_chars * 3 + 512))


def extract_writing_knowledge(
    evidence: list[WritingEvidenceInput | dict[str, Any]],
    *,
    material_role: str,
    api_key: str,
    model: str,
    base_url: str | None,
    temperature: float = 0.0,
    timeout: float = 90,
    max_retries: int = 2,
    max_tokens: int = 4096,
    request_parameters: dict[str, Any] | None = None,
    generator: Callable[[str], dict[str, Any]] | None = None,
) -> JointWritingExtraction:
    """Run one strict joint extraction while preserving signed provenance."""

    normalized = [
        item if isinstance(item, WritingEvidenceInput) else WritingEvidenceInput.model_validate(item)
        for item in evidence
    ]
    if not normalized:
        return JointWritingExtraction()
    prompt = writing_extraction_prompt(normalized, material_role=material_role)
    if generator is None:
        from semantica.semantic_extract.providers import OpenAIProvider

        provider = apply_model_transport_options(
            OpenAIProvider(api_key=api_key, model=model, base_url=base_url),
            timeout=timeout,
            max_retries=max_retries,
            request_parameters=request_parameters,
        )
        # A locally hosted OpenAI-compatible model may otherwise keep
        # producing JSON until its server-side default (often 8k+ tokens).
        # Bound the response by both operator configuration and source size:
        # extraction output may be larger than the source, but never needs an
        # unbounded answer.  This is a transport guard, not a truncation of
        # persisted evidence.
        output_budget = writing_output_token_budget(normalized, max_tokens)
        generator = lambda value: provider.generate_structured(
            value,
            temperature=_effective_temperature(model, temperature),
            max_tokens=output_budget,
        )
    raw = generator(prompt)
    result = JointWritingExtraction.model_validate(raw)
    _validate_evidence_ids(result, {item.evidence_id for item in normalized})
    if material_role == "sample_style":
        if result.entities or result.claims or result.relations or result.metrics:
            raise ValueError("样稿抽取不得产生本次业务实体、主张、事实或关系")
        if result.sample_profile is None:
            raise ValueError("样稿抽取缺少结构与文体画像")
    elif result.sample_profile is not None:
        raise ValueError("业务材料抽取不得混入样稿画像")
    return result


def candidate_key(*parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
