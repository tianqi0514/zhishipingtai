from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.platform.writing import FACT_STATUSES, SOURCE_TYPES, TRUSTED_BLOCK_TYPES


CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,99}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _code(value: str) -> str:
    normalized = value.strip().lower()
    if not CODE_PATTERN.fullmatch(normalized):
        raise ValueError("编码需以小写字母开头，仅可包含小写字母、数字、下划线和连字符")
    return normalized


class ScenarioPackageCreate(StrictModel):
    code: str
    name: str = Field(min_length=1, max_length=200)
    disaster_type: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=4000)
    enabled: bool = True

    _normalize_code = field_validator("code")(_code)


class ScenarioPackageUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: Literal["draft", "validating", "active", "retired", "failed"] | None = None
    enabled: bool | None = None


class ScenarioPackageVersionCreate(StrictModel):
    input_schema: dict[str, Any]
    ontology_mapping: dict[str, Any] = Field(default_factory=dict)
    rule_set_ids: list[str] = Field(default_factory=list)
    formula_ids: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    chapter_template: dict[str, Any]
    output_schema: dict[str, Any]
    review_rules: dict[str, Any] = Field(default_factory=dict)
    decision_gates: list[dict[str, Any]]
    comparison_dimensions: list[dict[str, Any]]
    config: dict[str, Any] = Field(default_factory=lambda: {"minimum_plan_count": 2, "default_plan_count": 3})
    activate: bool = False


class WritingProjectCreate(StrictModel):
    code: str
    name: str = Field(min_length=1, max_length=300)
    application_id: str | None = None
    scenario_package_version_id: str
    knowledge_product_release_id: str
    config: dict[str, Any] = Field(default_factory=dict)

    _normalize_code = field_validator("code")(_code)


class WritingProjectUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    status: Literal[
        "draft", "preparing", "ready", "reasoning", "writing", "reviewing", "published", "archived"
    ] | None = None
    config: dict[str, Any] | None = None


class WritingProjectMaterialCreate(StrictModel):
    document_id: str
    version_id: str | None = None
    material_role: Literal["policy_basis", "task_data", "reference", "attachment"] = "reference"
    usage_scope: Literal["task_only", "space_asset"] = "task_only"


class WritingProjectMaterialUpdate(StrictModel):
    material_role: Literal["policy_basis", "task_data", "reference", "attachment"] | None = None
    usage_scope: Literal["task_only", "space_asset"] | None = None


class ScenarioInputSetting(StrictModel):
    key: str
    label: str = Field(min_length=1, max_length=200)
    data_type: Literal["string", "number", "integer", "boolean", "object", "array"] = "string"
    unit: str | None = Field(default=None, max_length=64)
    required: bool = True
    confirmation_required: bool = True
    source_guidance: str = Field(default="", max_length=500)
    minimum: float | None = None
    maximum: float | None = None

    _normalize_key = field_validator("key")(_code)

    @model_validator(mode="after")
    def validate_range(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("输入项最小值不能大于最大值")
        return self


class SectionKnowledgeRequirement(StrictModel):
    ontology_version_id: str | None = None
    entity_types: list[str] = Field(default_factory=list, max_length=20)
    predicates: list[str] = Field(default_factory=list, max_length=20)
    rule_version_ids: list[str] = Field(default_factory=list, max_length=10)


class ScenarioSectionSetting(StrictModel):
    key: str
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=1000)
    generation_mode: Literal["agent", "toolbox", "mixed", "manual"] = "agent"
    required_inputs: list[str] = Field(default_factory=list)
    toolbox_outputs: list[str] = Field(default_factory=list)
    citation_required: bool = True
    knowledge: SectionKnowledgeRequirement = Field(default_factory=SectionKnowledgeRequirement)

    _normalize_key = field_validator("key")(_code)


class ScenarioToolboxSetting(StrictModel):
    reasoning_enabled: bool = True
    rule_set_ids: list[str] = Field(default_factory=list)
    calculation_enabled: bool = True
    formula_ids: list[str] = Field(default_factory=list)
    target_sections: dict[str, list[str]] = Field(default_factory=dict)


class ScenarioWritingPolicy(StrictModel):
    missing_input_action: Literal["block", "warn"] = "block"
    unverified_fact_action: Literal["block", "warn"] = "block"
    require_citations: bool = True
    allow_manual_override: bool = True


class ScenarioOutputSetting(StrictModel):
    title_pattern: str = Field(default="{project_name}", min_length=1, max_length=300)
    allowed_formats: list[Literal["docx", "pdf", "json", "xlsx", "geojson"]] = Field(
        default_factory=lambda: ["docx", "pdf"]
    )

    @field_validator("allowed_formats")
    @classmethod
    def formats_must_be_unique(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("至少启用一种导出格式")
        if len(value) != len(set(value)):
            raise ValueError("导出格式不能重复")
        return value


class ScenarioBusinessConfig(StrictModel):
    inputs: list[ScenarioInputSetting]
    sections: list[ScenarioSectionSetting]
    toolbox: ScenarioToolboxSetting = Field(default_factory=ScenarioToolboxSetting)
    writing_policy: ScenarioWritingPolicy = Field(default_factory=ScenarioWritingPolicy)
    output: ScenarioOutputSetting = Field(default_factory=ScenarioOutputSetting)
    decision_gates: list[dict[str, Any]]
    comparison_dimensions: list[dict[str, Any]] = Field(default_factory=list)
    minimum_plan_count: int = Field(default=2, ge=2, le=5)
    default_plan_count: int = Field(default=3, ge=2, le=5)
    activate: bool = False

    @model_validator(mode="after")
    def validate_business_references(self):
        input_keys = [item.key for item in self.inputs]
        section_keys = [item.key for item in self.sections]
        if not input_keys:
            raise ValueError("至少配置一个报告输入项")
        if not section_keys:
            raise ValueError("至少配置一个报告章节")
        if len(input_keys) != len(set(input_keys)):
            raise ValueError("输入项编码不能重复")
        if len(section_keys) != len(set(section_keys)):
            raise ValueError("章节编码不能重复")
        unknown_inputs = sorted(
            {key for section in self.sections for key in section.required_inputs} - set(input_keys)
        )
        if unknown_inputs:
            raise ValueError(f"章节引用了未配置的输入项：{', '.join(unknown_inputs)}")
        unknown_sections = sorted(
            {key for keys in self.toolbox.target_sections.values() for key in keys} - set(section_keys)
        )
        if unknown_sections:
            raise ValueError(f"推演工具引用了未配置的章节：{', '.join(unknown_sections)}")
        if self.default_plan_count < self.minimum_plan_count:
            raise ValueError("默认方案数量不能小于最少方案数量")
        return self


class WritingProjectReleaseRebase(StrictModel):
    knowledge_product_release_id: str
    reason: str = Field(min_length=2, max_length=1000)


class WritingMemberCreate(StrictModel):
    user_id: str
    role: Literal["viewer", "commenter", "editor", "reviewer", "publisher", "owner"] = "editor"


class ProjectFactCreate(StrictModel):
    fact_key: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=300)
    fact_type: Literal[
        "official_brief",
        "policy_document",
        "database_query",
        "deterministic_computation",
        "model_extraction",
        "semantica_inference",
        "mcp_tool",
        "manual_input",
        "manual_override",
        "historical",
    ]
    value: dict[str, Any]
    unit: str | None = Field(default=None, max_length=64)
    source_type: str
    source_id: str | None = Field(default=None, max_length=100)
    source_version: str | None = Field(default=None, max_length=100)
    source_locator: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0, le=1)
    verification_status: Literal["unverified", "verified", "rejected"] = "unverified"

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str) -> str:
        if value not in SOURCE_TYPES:
            raise ValueError("不支持的事实来源类型")
        return value


class FactConfirmation(StrictModel):
    decision: Literal["confirm", "reject", "override"]
    new_value: dict[str, Any] | None = None
    reason: str = Field(min_length=2, max_length=4000)

    @model_validator(mode="after")
    def require_override_value(self):
        if self.decision == "override" and self.new_value is None:
            raise ValueError("人工覆盖必须提供新值")
        return self


class ComputationRequest(StrictModel):
    definition_version_id: str | None = None
    operation: Literal[
        "resource_gap", "shelter_gap", "water_demand", "vehicle_trips", "ambulance_trips",
        "medical_pressure", "route_utility",
    ] | None = None
    inputs: dict[str, Any]
    parameters: dict[str, Any] = Field(default_factory=dict)
    rounding: dict[str, Any] = Field(default_factory=lambda: {"mode": "half_up", "digits": 0})
    input_fact_ids: list[str] = Field(default_factory=list)
    input_fact_map: dict[str, str] = Field(default_factory=dict)
    output_fact_key: str | None = Field(default=None, min_length=1, max_length=160)
    output_label: str | None = Field(default=None, min_length=1, max_length=300)
    output_unit: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def require_definition_or_operation(self):
        if not self.definition_version_id and not self.operation:
            raise ValueError("必须选择公式版本或受控公式")
        if self.output_fact_key and not self.output_label:
            raise ValueError("生成项目事实时必须提供结果名称")
        if set(self.input_fact_map.values()) - set(self.input_fact_ids):
            raise ValueError("输入事实映射只能引用 input_fact_ids 中的事实")
        return self


class AlternativePlanGenerate(StrictModel):
    count: int = Field(default=3, ge=2, le=3)
    inputs: dict[str, Any] = Field(default_factory=dict)


class AlternativePlanSelect(StrictModel):
    reason: str = Field(min_length=2, max_length=4000)


class WritingReasoningRequest(StrictModel):
    mode: Literal["preview", "publish"] = "preview"


class WritingDocumentCreate(StrictModel):
    project_id: str
    title: str = Field(min_length=1, max_length=500)
    document_type: str = Field(default="response_plan", min_length=1, max_length=64)
    content: list[dict[str, Any]] = Field(default_factory=list)


class WritingDocumentUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: Literal["draft", "reviewing", "ready", "published", "archived"] | None = None


class WritingDocumentVersionCreate(StrictModel):
    content: list[dict[str, Any]]
    change_summary: str = Field(default="", max_length=4000)
    publish: bool = False


class WritingBlockBindingUpsert(StrictModel):
    block_id: str = Field(min_length=1, max_length=100)
    block_type: str
    source_type: str
    source_id: str | None = Field(default=None, max_length=100)
    source_version: str | None = Field(default=None, max_length=100)
    knowledge_product_release_id: str | None = None
    chunk_id: str | None = None
    fact_id: str | None = None
    inferred_fact_id: str | None = None
    query_run_id: str | None = None
    computation_run_id: str | None = None
    tool_run_id: str | None = Field(default=None, max_length=100)
    evidence_ids: list[str] = Field(default_factory=list)
    data_time: str | None = None
    content_hash: str = Field(min_length=64, max_length=64)
    block_content: dict[str, Any] | None = None
    verification_status: Literal["unverified", "verified", "rejected"] = "unverified"
    freshness_status: str = "current"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("block_type")
    @classmethod
    def validate_block_type(cls, value: str) -> str:
        if value not in TRUSTED_BLOCK_TYPES:
            raise ValueError("不支持的可信业务块类型")
        return value

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str) -> str:
        if value not in SOURCE_TYPES:
            raise ValueError("不支持的来源类型")
        return value

    @field_validator("freshness_status")
    @classmethod
    def validate_freshness_status(cls, value: str) -> str:
        if value not in FACT_STATUSES:
            raise ValueError("不支持的有效状态")
        return value

    @model_validator(mode="after")
    def require_authoritative_reference(self):
        if self.block_content is not None:
            if str(self.block_content.get("id") or "") != self.block_id:
                raise ValueError("可信业务块内容与 block_id 不一致")
            if str(self.block_content.get("type") or "") != self.block_type:
                raise ValueError("可信业务块内容与 block_type 不一致")
        mapping = {
            "knowledge_citation": self.chunk_id,
            "verified_fact": self.fact_id,
            "computed_metric": self.computation_run_id,
            # 妙笔场景推演会先形成已确认 ProjectFact；知识分析发布后的
            # 图谱推演仍可绑定平台 InferredFact。两者都是受控权威来源。
            "inference_conclusion": self.inferred_fact_id or self.fact_id,
        }
        expected = mapping.get(self.block_type)
        if self.block_type in mapping and not expected:
            raise ValueError("该可信业务块缺少对应的权威来源")
        if self.block_type == "knowledge_citation" and not self.query_run_id:
            raise ValueError("知识引用必须关联产生该片段的检索记录")
        return self


class WritingDocumentValidate(StrictModel):
    content: list[dict[str, Any]] | None = None
    for_publish: bool = False


class WritingRecomputeRequest(StrictModel):
    changed_fact_ids: list[str] = Field(min_length=1)


class WritingCommentCreate(StrictModel):
    content: str = Field(min_length=1, max_length=10_000)
    thread_id: str | None = None
    parent_id: str | None = None
    block_id: str | None = Field(default=None, max_length=100)


class WritingCommentUpdate(StrictModel):
    content: str = Field(min_length=1, max_length=10_000)


class WritingCommentResolve(StrictModel):
    resolved: bool = True


class WritingKnowledgeSearch(StrictModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=8, ge=1, le=30)
    use_keyword: bool = True
    use_vector: bool = True
    use_graph: bool = True
    use_reranker: bool = False
    filters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_retrieval_channel(self):
        if not (self.use_keyword or self.use_vector or self.use_graph):
            raise ValueError("至少启用一种知识检索方式")
        return self


class WritingAgentSessionCreate(StrictModel):
    document_id: str | None = None
    start_new: bool = False
    purpose: Literal["editing", "report_generation"] = "editing"


class WritingAgentMessageCreate(StrictModel):
    content: str = Field(min_length=1, max_length=20_000)


class WritingGenerateReportRequest(StrictModel):
    document_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)


class WritingInputValueChange(StrictModel):
    fact_key: str = Field(min_length=1, max_length=160)
    new_value: dict[str, Any]
    reason: str = Field(min_length=2, max_length=2000)


class WritingInputChangePreview(StrictModel):
    document_id: str
    changes: list[WritingInputValueChange] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_fact_keys(self):
        keys = [item.fact_key for item in self.changes]
        if len(keys) != len(set(keys)):
            raise ValueError("一次影响预览中不能重复修改同一输入")
        return self


class WritingInputChangeApply(StrictModel):
    preview_id: str


class WritingAgentEditCreate(StrictModel):
    action: Literal[
        "expand", "rewrite", "shorten", "formalize", "simplify", "tone",
        "add_evidence", "fact_check", "to_list", "heading",
    ]
    original_text: str = Field(min_length=1, max_length=20_000)
    block_id: str | None = Field(default=None, max_length=100)
    instruction: str = Field(default="", max_length=4000)


class WritingAgentEditDecision(StrictModel):
    decision: Literal["accept", "reject"]


class AgentWritingRequest(StrictModel):
    conversation_id: str


class AgentWritingOutlineDraftRequest(AgentWritingRequest):
    title: str | None = Field(default=None, max_length=500)


class AgentWritingSectionDraftRequest(AgentWritingRequest):
    section_key: str = Field(min_length=1, max_length=100)
    instruction: str = Field(default="", max_length=4000)


class AgentWritingBindEvidenceRequest(AgentWritingRequest):
    block_id: str = Field(min_length=1, max_length=100)
    query_run_id: str
    chunk_id: str
    content_hash: str = Field(min_length=64, max_length=64)


class AgentWritingValidateRequest(AgentWritingRequest):
    for_publish: bool = False


class AgentWritingRecomputeRequest(AgentWritingRequest):
    changed_fact_ids: list[str] = Field(min_length=1)


class AgentWritingPrepareExportRequest(AgentWritingRequest):
    output_format: Literal["docx", "pdf", "json", "xlsx", "geojson"]


class DecisionRecordCreate(StrictModel):
    decision: Literal["confirm", "reject", "override"]
    original_value: dict[str, Any] = Field(default_factory=dict)
    new_value: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=2, max_length=4000)


class WritingExportCreate(StrictModel):
    output_format: Literal["docx", "evidence_docx", "pdf", "json", "xlsx", "geojson"]
    template_version_id: str | None = None
