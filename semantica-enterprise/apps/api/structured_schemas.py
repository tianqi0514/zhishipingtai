from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreviewFilter(StrictModel):
    column_id: str = Field(min_length=1, max_length=500)
    operator: Literal[
        "eq", "ne", "contains", "not_contains", "starts_with",
        "gt", "gte", "lt", "lte", "between", "in", "is_null", "is_not_null",
    ]
    value: Any = None
    upper: Any = None


class DataPreviewRequest(StrictModel):
    object_id: str = Field(min_length=1, max_length=500)
    mode: Literal["live", "snapshot"] = "live"
    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=20, ge=1, le=100)
    order_by: str | None = Field(default=None, max_length=500)
    order_direction: Literal["asc", "desc"] = "asc"
    filters: list[PreviewFilter] = Field(default_factory=list, max_length=10)


class DataPreviewCountRequest(StrictModel):
    object_id: str = Field(min_length=1, max_length=500)
    filters: list[PreviewFilter] = Field(default_factory=list, max_length=10)


class DataPreviewPolicyUpdate(StrictModel):
    live_preview_enabled: bool = True
    allowed_objects: list[str] = Field(default_factory=list)
    denied_objects: list[str] = Field(default_factory=list)
    allowed_columns: dict[str, list[str]] = Field(default_factory=dict)
    sensitive_columns: dict[str, list[str]] = Field(default_factory=dict)
    masking_rules: dict[str, str] = Field(default_factory=dict)
    default_order: dict[str, str] = Field(default_factory=dict)
    default_page_size: int = Field(default=20, ge=1, le=100)
    max_page_size: int = Field(default=100, ge=1, le=100)
    max_text_length: int = Field(default=500, ge=50, le=10_000)
    allow_full_cell: bool = False
    allow_exact_count: bool = False
    query_timeout_seconds: int = Field(default=15, ge=1, le=120)
    max_filter_conditions: int = Field(default=10, ge=1, le=10)
    max_result_bytes: int = Field(default=2_000_000, ge=10_000, le=20_000_000)

    @model_validator(mode="after")
    def validate_page_sizes(self):
        if self.default_page_size > self.max_page_size:
            raise ValueError("默认每页数量不能大于最大每页数量")
        if set(self.allowed_objects) & set(self.denied_objects):
            raise ValueError("同一个对象不能同时允许和禁止预览")
        return self


class MappingJoinColumn(StrictModel):
    object_id: str = Field(min_length=1)
    column_id: str = Field(min_length=1)


class MappingJoinPredicate(StrictModel):
    left: MappingJoinColumn
    operator: Literal["="] = "="
    right: MappingJoinColumn


class SemanticEntityFragment(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    object_id: str = Field(min_length=1)
    role: Literal["primary", "extension", "bridge", "alternative"] = "primary"
    identity_column_ids: list[str] = Field(default_factory=list)
    display_column_id: str | None = None
    grain: str = Field(default="", max_length=500)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SemanticEntityMapping(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    ontology_term_id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2000)
    fragments: list[SemanticEntityFragment] = Field(min_length=1)

    @model_validator(mode="after")
    def one_primary_fragment(self):
        if sum(item.role == "primary" for item in self.fragments) != 1:
            raise ValueError(f"实体 {self.id} 必须且只能有一个主要数据表")
        return self


class SemanticMetricFilter(StrictModel):
    attribute_id: str = Field(min_length=1, max_length=100)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "between", "in", "is_null", "is_not_null"]
    value: Any = None
    upper: Any = None


class SemanticAttributeMapping(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    ontology_term_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    fragment_id: str = Field(min_length=1)
    column_id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=500)
    semantic_type: Literal[
        "string", "integer", "number", "boolean", "date", "datetime", "json", "unknown"
    ] = "unknown"
    is_measure: bool = False
    aliases: list[str] = Field(default_factory=list, max_length=20)
    business_definition: str = Field(default="", max_length=2000)
    default_aggregate: Literal["count", "sum", "average", "min", "max"] | None = None
    required_filters: list[SemanticMetricFilter] = Field(default_factory=list, max_length=10)
    confidence: float = Field(default=1.0, ge=0, le=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SemanticRelationshipMapping(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    ontology_term_id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=500)
    from_entity_id: str = Field(min_length=1)
    to_entity_id: str = Field(min_length=1)
    predicates: list[MappingJoinPredicate] = Field(min_length=1)
    cardinality: Literal[
        "one_to_one", "one_to_many", "many_to_one", "many_to_many", "unknown"
    ] = "unknown"
    confidence: float = Field(default=1.0, ge=0, le=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SemanticMappingManifest(StrictModel):
    manifest_version: Literal["chuanshen.semantic-mapping/v1"] = "chuanshen.semantic-mapping/v1"
    source_id: str = Field(min_length=1)
    ontology_id: str = Field(min_length=1)
    schema_version_id: str = Field(min_length=1)
    entities: list[SemanticEntityMapping] = Field(default_factory=list)
    attributes: list[SemanticAttributeMapping] = Field(default_factory=list)
    relationships: list[SemanticRelationshipMapping] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SemanticMappingCreate(StrictModel):
    source_id: str
    ontology_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    manifest: SemanticMappingManifest | None = None


class SemanticMappingUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    manifest: SemanticMappingManifest | None = None


class MappingSuggestionRequest(StrictModel):
    minimum_confidence: float = Field(default=0.65, ge=0, le=1)
    include_unmapped_tables: bool = True


class MappingRollbackRequest(StrictModel):
    version_id: str


class PlannedOutput(StrictModel):
    position: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=200)
    kind: Literal["attribute", "metric", "derived"]
    attribute_ids: list[str] = Field(default_factory=list)
    aggregate: Literal["count", "sum", "average", "min", "max"] | None = None


class PlannedFilter(StrictModel):
    attribute_id: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "between", "in", "is_null", "is_not_null", "contains"]
    value: Any = None
    upper: Any = None


class PlannedOrdering(StrictModel):
    attribute_ids: list[str] = Field(default_factory=list)
    output_position: int | None = Field(default=None, ge=1)
    direction: Literal["asc", "desc"] = "asc"


class MetricContract(StrictModel):
    kind: Literal[
        "none", "count", "sum", "average", "difference", "ratio", "percentage",
        "extremum", "rank", "other",
    ] = "none"
    numerator: str = Field(default="", max_length=1000)
    denominator: str = Field(default="", max_length=1000)
    scale: float | None = None
    aggregation_grain: str = Field(default="", max_length=500)
    distinct_policy: str = Field(default="", max_length=500)
    base_entity_ids: list[str] = Field(default_factory=list)
    base_relationship_ids: list[str] = Field(default_factory=list)


class CalculationStep(StrictModel):
    step_id: str = Field(min_length=1, max_length=100)
    kind: Literal["filter", "aggregate", "derive", "rank", "project"]
    operation: str = Field(min_length=1, max_length=100)
    input_step_ids: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    relationship_ids: list[str] = Field(default_factory=list)
    attribute_ids: list[str] = Field(default_factory=list)
    group_by_attribute_ids: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=1000)


class SemanticQueryPlan(StrictModel):
    version: Literal["chuanshen.semantic-query-plan/v1"] = "chuanshen.semantic-query-plan/v1"
    original_question: str = Field(min_length=1, max_length=4000)
    intent: str = Field(min_length=1, max_length=1000)
    entity_ids: list[str] = Field(min_length=1)
    relationship_ids: list[str] = Field(default_factory=list)
    outputs: list[PlannedOutput] = Field(min_length=1)
    filters: list[PlannedFilter] = Field(default_factory=list)
    group_by_attribute_ids: list[str] = Field(default_factory=list)
    ordering: list[PlannedOrdering] = Field(default_factory=list)
    distinct: bool = False
    limit: int | None = Field(default=None, ge=1, le=10_000)
    expected_cardinality: Literal["single_value", "single_row", "multiple_rows"]
    result_grain: str = Field(default="", max_length=500)
    population: str = Field(default="", max_length=1000)
    numerator: str = Field(default="", max_length=1000)
    denominator: str = Field(default="", max_length=1000)
    distinct_policy: str = Field(default="", max_length=500)
    time_range: str = Field(default="", max_length=500)
    null_policy: str = Field(default="", max_length=500)
    calculation_steps: list[CalculationStep] = Field(default_factory=list)
    result_step_id: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    evidence_constraints: list[str] = Field(default_factory=list)
    metric_contract: MetricContract = Field(default_factory=MetricContract)

    @model_validator(mode="after")
    def validate_plan_shape(self):
        positions = [item.position for item in self.outputs]
        if positions != list(range(1, len(self.outputs) + 1)):
            raise ValueError("输出位置必须从 1 开始连续排列")
        if len(self.entity_ids) != len(set(self.entity_ids)):
            raise ValueError("业务实体不能重复")
        if len(self.relationship_ids) != len(set(self.relationship_ids)):
            raise ValueError("业务关系不能重复")
        seen: set[str] = set()
        for step in self.calculation_steps:
            if step.step_id in seen:
                raise ValueError("计算步骤 ID 不能重复")
            if set(step.input_step_ids) - seen:
                raise ValueError("计算步骤必须按依赖顺序排列")
            seen.add(step.step_id)
        if self.calculation_steps and self.result_step_id not in seen:
            raise ValueError("结果步骤必须引用已声明的计算步骤")
        if not self.calculation_steps and self.result_step_id is not None:
            raise ValueError("没有计算步骤时不能设置结果步骤")
        if self.expected_cardinality == "single_value" and len(self.outputs) != 1:
            raise ValueError("单值查询只能声明一个输出")
        return self


class QueryExpression(StrictModel):
    kind: Literal[
        "attribute", "literal", "aggregate", "function", "binary", "logical", "not",
        "between", "in", "is_null", "case", "cast", "subquery", "exists", "window",
    ]
    attribute_id: str | None = None
    binding: str | None = None
    value: Any = None
    function: str | None = None
    arguments: list["QueryExpression"] = Field(default_factory=list)
    operator: str | None = None
    left: "QueryExpression | None" = None
    right: "QueryExpression | None" = None
    operands: list["QueryExpression"] = Field(default_factory=list)
    expression: "QueryExpression | None" = None
    lower: "QueryExpression | None" = None
    upper: "QueryExpression | None" = None
    options: list["QueryExpression"] = Field(default_factory=list)
    query: "SemanticQueryIR | None" = None
    negated: bool = False
    whens: list[dict[str, Any]] = Field(default_factory=list)
    else_expression: "QueryExpression | None" = None
    target_type: str | None = None
    distinct: bool = False
    partition_by: list["QueryExpression"] = Field(default_factory=list)
    window_order_by: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_expression_shape(self):
        required = {
            "attribute": ["attribute_id", "binding"], "literal": [],
            "aggregate": ["function"], "function": ["function"],
            "binary": ["operator", "left", "right"], "logical": ["operator"],
            "not": ["expression"], "between": ["expression", "lower", "upper"],
            "in": ["expression"], "is_null": ["expression"], "case": [],
            "cast": ["expression", "target_type"], "subquery": ["query"],
            "exists": ["query"], "window": ["function"],
        }[self.kind]
        missing = [name for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(f"{self.kind} 表达式缺少字段：{', '.join(missing)}")
        if self.binding and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", self.binding):
            raise ValueError("实体绑定名称不合法")
        if self.kind == "logical" and (self.operator not in {"and", "or"} or len(self.operands) < 2):
            raise ValueError("逻辑表达式需要 and/or 和至少两个子表达式")
        if self.kind == "in" and bool(self.options) == bool(self.query):
            raise ValueError("IN 表达式必须且只能提供值列表或子查询")
        if self.kind == "case" and not self.whens:
            raise ValueError("CASE 表达式至少需要一个分支")
        return self


class EntityBinding(StrictModel):
    binding: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    entity_id: str


class QueryJoin(StrictModel):
    binding: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    entity_id: str
    relationship_id: str
    from_binding: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    join_type: Literal["inner", "left"] = "inner"


class QueryProjection(StrictModel):
    expression: QueryExpression
    alias: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class QueryOrdering(StrictModel):
    expression: QueryExpression
    direction: Literal["asc", "desc"] = "asc"


class SemanticQueryIR(StrictModel):
    version: Literal["chuanshen.query-ir/v1"] = "chuanshen.query-ir/v1"
    from_entity: EntityBinding
    joins: list[QueryJoin] = Field(default_factory=list, max_length=8)
    select: list[QueryProjection] = Field(min_length=1, max_length=100)
    where: QueryExpression | None = None
    group_by: list[QueryExpression] = Field(default_factory=list, max_length=50)
    having: QueryExpression | None = None
    order_by: list[QueryOrdering] = Field(default_factory=list, max_length=20)
    distinct: bool = False
    limit: int | None = Field(default=None, ge=1, le=10_000)
    offset: int | None = Field(default=None, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_bindings(self):
        available = {self.from_entity.binding}
        for join in self.joins:
            if join.binding in available:
                raise ValueError("实体绑定不能重复")
            if join.from_binding not in available:
                raise ValueError("Join 引用了尚未声明的实体绑定")
            available.add(join.binding)
        return self


QueryExpression.model_rebuild()


class StructuredPlanValidationRequest(StrictModel):
    mapping_version_id: str
    plan: SemanticQueryPlan


class StructuredIRValidationRequest(StrictModel):
    mapping_version_id: str
    plan: SemanticQueryPlan
    query_ir: SemanticQueryIR


class StructuredCompileRequest(StructuredIRValidationRequest):
    max_rows: int = Field(default=500, ge=1, le=10_000)


class StructuredExecuteRequest(StructuredCompileRequest):
    conversation_id: str | None = None
    message_id: str | None = None


class StructuredNaturalLanguageRequest(StrictModel):
    mapping_version_id: str
    question: str = Field(min_length=1, max_length=4000)
    execute: bool = True
    max_rows: int = Field(default=500, ge=1, le=10_000)


class AgentStructuredSchemaSearchRequest(StrictModel):
    conversation_id: str
    query: str = Field(min_length=1, max_length=1000)
    space_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=50)


class AgentStructuredObjectRequest(StrictModel):
    conversation_id: str
    semantic_object_id: str
    mapping_version_id: str


class AgentStructuredRelationPathRequest(StrictModel):
    conversation_id: str
    from_entity_id: str
    to_entity_id: str
    mapping_version_id: str
    max_depth: int = Field(default=4, ge=1, le=8)


class AgentStructuredInspectValuesRequest(StrictModel):
    conversation_id: str
    attribute_id: str
    mapping_version_id: str
    search: str = Field(default="", max_length=200)
    limit: int = Field(default=20, ge=1, le=100)


class AgentStructuredExecuteRequest(StrictModel):
    conversation_id: str
    semantic_query_plan: SemanticQueryPlan
    query_ir: SemanticQueryIR
    mapping_version_id: str
    max_rows: int = Field(default=500, ge=1, le=10_000)
