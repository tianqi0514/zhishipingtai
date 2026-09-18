from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    @field_validator("time_scope", "applicable_scope", mode="before")
    @classmethod
    def normalize_scope(cls, value: Any) -> dict[str, Any]:
        if value is None or value == "":
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return {"description": value.strip()}
        raise ValueError("范围必须是对象或简短文本")


class RelationHintCandidate(StrictModel):
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=300)
    object: str = Field(min_length=1, max_length=500)
    time_scope: dict[str, Any] = Field(default_factory=dict)
    applicable_scope: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)
    needs_confirmation: bool = False

    @field_validator("time_scope", "applicable_scope", mode="before")
    @classmethod
    def normalize_scope(cls, value: Any) -> dict[str, Any]:
        return ClaimCandidate.normalize_scope(value)


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

    @field_validator("time_scope", "applicable_scope", mode="before")
    @classmethod
    def normalize_scope(cls, value: Any) -> dict[str, Any]:
        return ClaimCandidate.normalize_scope(value)


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
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("style", mode="before")
    @classmethod
    def normalize_style(cls, value: Any) -> dict[str, Any]:
        if value is None or value == "":
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return {"description": value.strip()}
        raise ValueError("样稿文风必须是对象或简短文本")


class JointWritingExtraction(StrictModel):
    entities: list[EntityMentionCandidate] = Field(default_factory=list, max_length=200)
    claims: list[ClaimCandidate] = Field(default_factory=list, max_length=300)
    relations: list[RelationHintCandidate] = Field(default_factory=list, max_length=300)
    metrics: list[MetricMentionCandidate] = Field(default_factory=list, max_length=200)
    sample_profile: SampleProfileCandidate | None = None
    ambiguities: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def bound_candidate_payload(cls, value: Any) -> Any:
        """Bound complete model output without inventing or rewriting facts.

        Private models may ignore the requested candidate limit. Keeping a
        deterministic prefix is safer than repeatedly asking the model to
        rewrite values: signed Evidence remains intact and governance receives
        an explicit warning that more candidates can be added from the source.
        """

        if not isinstance(value, dict):
            return value
        bounded = dict(value)
        limits = {"entities": 4, "claims": 4, "relations": 3, "metrics": 5}
        original_counts: dict[str, int] = {}
        for key, limit in limits.items():
            items = bounded.get(key)
            if isinstance(items, list):
                original_counts[key] = len(items)
                bounded[key] = list(items[:limit])
        total = sum(len(bounded.get(key) or []) for key in limits)
        # The prompt asks the model to order important items first. Remove
        # remaining overflow from the least authoritative category first while
        # retaining one candidate from each non-empty category where possible.
        while total > 12:
            removed = False
            for key in ("entities", "relations", "claims", "metrics"):
                items = bounded.get(key)
                if isinstance(items, list) and len(items) > 1:
                    items.pop()
                    total -= 1
                    removed = True
                    if total <= 12:
                        break
            if not removed:
                break
        retained_counts = {key: len(bounded.get(key) or []) for key in limits}
        if any(retained_counts[key] < original_counts.get(key, 0) for key in limits):
            notes = list(bounded.get("ambiguities") or [])
            if len(notes) < 3:
                notes.append(
                    "模型返回候选超过单个 Evidence 上限；已按写作优先级保留前12项，"
                    "其余内容仍可从原始 Evidence 人工补充。"
                )
            bounded["ambiguities"] = notes[:3]
        return bounded

    @field_validator("ambiguities", mode="before")
    @classmethod
    def normalize_ambiguities(cls, value: Any) -> list[str]:
        """Preserve explanatory ambiguity objects without weakening facts.

        Some OpenAI-compatible models consistently express an ambiguity as a
        small JSON object (field/reason/evidence_ids) despite the requested
        string contract.  Ambiguities are advisory text and are not promoted
        to facts or relations, so serialize only that display field while the
        authoritative extraction models remain strict and ``extra=forbid``.
        """

        if value is None or value == "":
            return []
        if not isinstance(value, list):
            raise ValueError("ambiguities 必须是数组")
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = json.dumps(item, ensure_ascii=False, sort_keys=True)
            else:
                raise ValueError("ambiguities 仅支持文本或说明对象")
            if text:
                normalized.append(text)
        return normalized

    @model_validator(mode="after")
    def sample_does_not_mix_business_facts(self) -> "JointWritingExtraction":
        candidate_count = (
            len(self.entities) + len(self.claims) + len(self.relations) + len(self.metrics)
        )
        if candidate_count > 12:
            raise ValueError("单个 Evidence 的写作知识候选合计不能超过12项")
        if len(self.ambiguities) > 3:
            raise ValueError("单个 Evidence 的歧义说明不能超过3项")
        return self


def writing_extraction_prompt(
    evidence: list[WritingEvidenceInput],
    *,
    material_role: str,
) -> str:
    sample_only = material_role == "sample_style"
    # A sample profile needs whole-document structure, but page/locator JSON
    # repeated for every paragraph can consume the model's context window.
    # Evidence ids still preserve provenance; the platform resolves their
    # locators after extraction.
    signed_evidence = [
        ({"evidence_id": item.evidence_id, "text": item.text} if sample_only else item.model_dump())
        for item in evidence
    ]
    boundary = (
        "本批次是样稿。只提取 sample_profile；entities、claims、relations、metrics 必须为空。"
        if sample_only
        else "本批次是业务材料。sample_profile 必须为 null。"
    )
    sample_profile_contract = (
        '{"document_type":"文档类型","target_audience":"目标读者",'
        '"chapters":[{"title":"一级标题","responsibility":"本章写什么","level":1,"citation_required":true}],'
        '"style":{"register":"正式程度","heading_numbering":"标题编号方式"},'
        '"table_patterns":["表格用途"],"attachment_patterns":["附件用途"],'
        '"evidence_ids":["已签发ID"]}'
        if sample_only else "null"
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
9. 单个 Evidence 的 entities、claims、relations、metrics 四个数组合计最多12项；同时 entities 最多4项、claims 最多4项、relations 最多3项、metrics 最多5项。必须优先保留数值指标、关键陈述和核心业务对象。
10. metrics 只列原文明确出现的数值指标；数值已经进入 metrics 时不要在 claims 中重复。relations 只列实体到实体的关系，不得把数值扩写成关系。
11. 为避免结构化响应截断，必须输出紧凑单行 JSON。以下有默认值的字段在值为空时应省略：aliases、time_scope、applicable_scope、qualifiers、needs_confirmation、unit、value_type。
12. ambiguities 最多3项，每项不超过120个汉字，只说明会影响治理的主体、口径、单位或时间歧义。

字段契约（字段名必须逐字一致；禁止使用 id、name、type、source_ids 等替代字段）：
{{
  "entities":[{{"mention_text":"原文提及","canonical_name":"标准候选名","entity_type":"组织|人员|部门|制度|项目|产品|供应商|地点|资源|事件|其他","evidence_ids":["已签发ID"],"confidence":0.95}}],
  "claims":[{{"subject":"主体","predicate":"谓词","object_value":"字符串或数值或布尔值或null","claim_type":"assertion|requirement|prediction|recommendation|opinion","evidence_ids":["已签发ID"],"confidence":0.95}}],
  "relations":[{{"subject":"主体实体名","predicate":"关系","object":"客体实体名","evidence_ids":["已签发ID"],"confidence":0.95}}],
  "metrics":[{{"name":"指标名","value":320,"value_type":"integer|number|string","unit":"人","evidence_ids":["已签发ID"],"confidence":0.95}}],
  "sample_profile":{sample_profile_contract},
  "ambiguities":[]
}}
除第11条列出的默认字段外，不得省略字段。

材料用途：{material_role}
签发 Evidence：
{json.dumps(signed_evidence, ensure_ascii=False)}"""


def _validate_evidence_ids(result: JointWritingExtraction, allowed_ids: set[str]) -> None:
    referenced: list[str] = []
    for item in [*result.entities, *result.claims, *result.relations, *result.metrics]:
        referenced.extend(item.evidence_ids)
    if result.sample_profile is not None:
        referenced.extend(result.sample_profile.evidence_ids)
    invalid = sorted(set(referenced) - allowed_ids)
    if invalid:
        raise ValueError(f"模型返回了未签发的 Evidence ID：{', '.join(invalid[:5])}")


def writing_output_token_budget(
    evidence: list[WritingEvidenceInput], configured_max_tokens: int,
) -> int:
    """Return a bounded response budget proportional to signed source text."""

    source_chars = sum(len(item.text) for item in evidence)
    # Dense annual-report tables can express dozens of separately evidenced
    # metrics inside one parser-owned span.  The former 2k ceiling truncated
    # otherwise valid strict JSON for those spans and made a full document
    # target fail after every other batch had succeeded.  Keep the budget
    # proportional to the signed source text, while allowing a bounded 4k
    # response for genuinely dense evidence.
    ceiling = max(512, min(int(configured_max_tokens), 8192))
    # Evidence ids are UUID-sized and are repeated across Entity, Claim,
    # Relation and Metric rows.  Dense annual-report prose can therefore need
    # materially more output tokens than its short Chinese source text even
    # though the extraction remains bounded to one signed Evidence span.
    return min(ceiling, max(768, source_chars * 10 + 1536))


def _parse_model_json(provider: Any, content: str | None, finish_reason: str | None) -> dict[str, Any]:
    """Parse one model response, repairing syntax only after a complete reply.

    Repair is intentionally limited to JSON syntax.  A response stopped by a
    token limit is never repaired because that could turn an incomplete
    extraction into an apparently successful one.  The repaired object still
    passes the exact top-level contract, Pydantic ``extra=forbid`` validation,
    and signed Evidence checks before any candidate is persisted.
    """

    raw_content = content or ""
    try:
        parsed = provider._parse_json(raw_content)
    except Exception:
        if str(finish_reason or "").casefold() in {"length", "max_tokens"}:
            raise ValueError("模型结构化响应因输出上限截断，不能安全修复")
        from json_repair import loads as repair_json_loads

        parsed = repair_json_loads(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError("模型结构化响应必须是 JSON 对象")
    expected = {"entities", "claims", "relations", "metrics", "sample_profile", "ambiguities"}
    missing = sorted(expected - set(parsed))
    if missing:
        raise ValueError(f"模型结构化响应缺少顶层键：{', '.join(missing)}")
    return parsed


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
        if material_role == "sample_style":
            # Leave a safety margin for 16k-context local models. A concise
            # structure/style profile does not require the full 2k ceiling.
            output_budget = min(output_budget, 1792)
        effective_temperature = _effective_temperature(model, temperature)

        def provider_generator(value: str) -> dict[str, Any]:
            # Current vLLM/OpenAI-compatible deployments support JSON Object
            # mode even though not every third-party gateway does.  Prefer it
            # for the strict writing contract; fall back only when the server
            # explicitly rejects that request option.
            if base_url and getattr(provider, "client", None) is not None:
                try:
                    response = provider.client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": value}],
                        response_format={"type": "json_object"},
                        temperature=effective_temperature,
                        max_tokens=output_budget,
                    )
                    choice = response.choices[0]
                    return _parse_model_json(
                        provider,
                        choice.message.content,
                        getattr(choice, "finish_reason", None),
                    )
                except Exception as exc:
                    status_code = getattr(exc, "status_code", None)
                    message = str(exc).casefold()
                    unsupported_json_mode = status_code == 400 and any(
                        marker in message
                        for marker in ("response_format", "json_object", "unsupported")
                    )
                    if not unsupported_json_mode:
                        raise
            return provider.generate_structured(
                value,
                temperature=effective_temperature,
                max_tokens=output_budget,
            )

        generator = provider_generator
    # OpenAI-compatible private models do not all honour ``response_format``.
    # A successful HTTP response can therefore still contain truncated or
    # malformed JSON.  Transport retries cannot repair that response, so make
    # one bounded schema-aware retry with an explicit correction instruction.
    # The operation is side-effect free; persistence only happens after this
    # function returns a fully validated object.
    attempts = max(1, min(int(max_retries) + 1, 3))
    current_prompt = prompt
    for attempt in range(attempts):
        try:
            raw = generator(current_prompt)
            result = JointWritingExtraction.model_validate(raw)
            _validate_evidence_ids(result, {item.evidence_id for item in normalized})
            if material_role == "sample_style":
                if result.entities or result.claims or result.relations or result.metrics:
                    raise ValueError("样稿抽取不得产生本次业务实体、主张、事实或关系")
                if result.sample_profile is None:
                    raise ValueError("样稿抽取缺少结构与文体画像")
                if not result.sample_profile.evidence_ids:
                    raise ValueError("样稿结构与文体画像缺少来源依据")
            elif result.sample_profile is not None:
                raise ValueError("业务材料抽取不得混入样稿画像")
            return result
        except Exception as exc:
            message = str(exc).casefold()
            invalid_structured_output = isinstance(exc, ValueError) or any(
                marker in message
                for marker in (
                    "invalid json", "failed to parse json", "validation error",
                    "未签发", "不得产生", "缺少结构与文体画像", "不得混入",
                )
            )
            if not invalid_structured_output or attempt + 1 >= attempts:
                raise
            current_prompt = (
                prompt
                + "\n\n纠错重试：上一轮输出不是可校验的完整 JSON。"
                + "请从原始 Evidence 重新抽取，只输出一个完整、紧凑、单行 JSON 对象；"
                + "不得输出 Markdown、解释或未签发的 Evidence ID，且不得省略顶层键。"
            )
    raise AssertionError("unreachable")


def candidate_key(*parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
