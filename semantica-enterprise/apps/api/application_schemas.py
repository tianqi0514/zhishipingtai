from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,99}$")
APP_SCOPES = {
    "knowledge.search",
    "knowledge.chat",
    "knowledge.fragment.read",
    "knowledge.graph.read",
    "knowledge.profile.read",
    "scenario.invoke",
    "feedback.write",
}


def _valid_code(value: str) -> str:
    value = value.strip().lower()
    if not CODE_PATTERN.fullmatch(value):
        raise ValueError("编码需以小写字母开头，仅可包含小写字母、数字、下划线和连字符")
    return value


def _empty_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


class ApplicationCreate(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    app_type: Literal["web", "backend", "agent", "integration"] = "agent"
    environment: Literal["development", "testing", "production"] = "development"
    owner_id: str | None = None
    org_unit_id: str | None = None
    status: Literal["draft", "active", "suspended", "retired"] = "draft"
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    _normalize_code = field_validator("code")(_valid_code)
    _normalize_optional_ids = field_validator("owner_id", "org_unit_id", mode="before")(_empty_to_none)


class ApplicationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    app_type: Literal["web", "backend", "agent", "integration"] | None = None
    environment: Literal["development", "testing", "production"] | None = None
    owner_id: str | None = None
    org_unit_id: str | None = None
    status: Literal["draft", "active", "suspended", "retired"] | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None

    _normalize_optional_ids = field_validator("owner_id", "org_unit_id", mode="before")(_empty_to_none)


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=lambda: ["scenario.invoke"])
    expires_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        result = list(dict.fromkeys(value))
        unknown = sorted(set(result) - APP_SCOPES)
        if unknown:
            raise ValueError(f"不支持的应用权限：{', '.join(unknown)}")
        if not result:
            raise ValueError("至少选择一个应用权限")
        return result


class CredentialTokenRequest(BaseModel):
    client_id: str = Field(min_length=8, max_length=120)
    client_secret: str = Field(min_length=32, max_length=500)
    scope: str | None = Field(default=None, max_length=1000)


class GrantCreate(BaseModel):
    resource_type: Literal["knowledge_product", "scenario"]
    resource_id: str
    permission: Literal["invoke", "read", "manage"] = "invoke"
    effect: Literal["allow", "deny"] = "allow"


class KnowledgeProductCreate(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    owner_id: str | None = None
    status: Literal["draft", "active", "retired"] = "draft"
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    space_ids: list[str] = Field(default_factory=list)

    _normalize_code = field_validator("code")(_valid_code)
    _normalize_owner = field_validator("owner_id", mode="before")(_empty_to_none)


class KnowledgeProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    owner_id: str | None = None
    status: Literal["draft", "active", "retired"] | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    space_ids: list[str] | None = None

    _normalize_owner = field_validator("owner_id", mode="before")(_empty_to_none)


class ProductReleaseCreate(BaseModel):
    note: str = Field(default="", max_length=500)


class ProductAliasMove(BaseModel):
    product_release_id: str
    reason: str = Field(default="", max_length=500)


class ScenarioCreate(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    scenario_type: Literal["search", "chat", "analysis", "structured"] = "chat"
    owner_id: str | None = None
    status: Literal["draft", "active", "retired"] = "draft"
    enabled: bool = True

    _normalize_code = field_validator("code")(_valid_code)
    _normalize_owner = field_validator("owner_id", mode="before")(_empty_to_none)


class ScenarioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    scenario_type: Literal["search", "chat", "analysis", "structured"] | None = None
    owner_id: str | None = None
    status: Literal["draft", "active", "retired"] | None = None
    enabled: bool | None = None

    _normalize_owner = field_validator("owner_id", mode="before")(_empty_to_none)


class ScenarioVersionCreate(BaseModel):
    product_id: str
    product_alias: Literal["development", "testing", "production"] = "production"
    model_config_id: str | None = None
    tool_whitelist: list[str] = Field(default_factory=lambda: ["knowledge_search", "knowledge_get_fragment"])
    retrieval_policy: dict[str, Any] = Field(default_factory=lambda: {
        "top_k": 8,
        "use_keyword": True,
        "use_vector": True,
        "use_graph": True,
        "use_reranker": False,
    })
    system_policy: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    citation_policy: dict[str, Any] = Field(default_factory=lambda: {"required": True})
    fallback_policy: dict[str, Any] = Field(default_factory=lambda: {"insufficient_evidence": "disclose"})
    analysis_rule_set_ids: list[str] = Field(default_factory=list)

    _normalize_model = field_validator("model_config_id", mode="before")(_empty_to_none)

    @model_validator(mode="after")
    def validate_retrieval(self) -> "ScenarioVersionCreate":
        channels = ("use_keyword", "use_vector", "use_graph")
        if not any(bool(self.retrieval_policy.get(item, False)) for item in channels):
            raise ValueError("至少启用一个检索通道")
        top_k = int(self.retrieval_policy.get("top_k", 8))
        if not 1 <= top_k <= 100:
            raise ValueError("top_k 必须在 1 到 100 之间")
        return self


class ScenarioInvokeRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    filters: dict[str, Any] = Field(default_factory=dict)


class EvaluationDatasetCreate(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    enabled: bool = True

    _normalize_code = field_validator("code")(_valid_code)


class EvaluationDatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None


class EvaluationCaseCreate(BaseModel):
    case_key: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=10000)
    expected_answer: str = Field(default="", max_length=30000)
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_facts: list[dict[str, Any]] = Field(default_factory=list)
    expected_schema: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class EvaluationCaseUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=10000)
    expected_answer: str | None = Field(default=None, max_length=30000)
    expected_chunk_ids: list[str] | None = None
    expected_facts: list[dict[str, Any]] | None = None
    expected_schema: dict[str, Any] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


class EvaluationRunCreate(BaseModel):
    dataset_id: str
    scenario_version_id: str
    gate_config: dict[str, float] = Field(default_factory=dict)


class FeedbackCreate(BaseModel):
    scenario_id: str | None = None
    invocation_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    query_run_id: str | None = None
    product_release_id: str | None = None
    feedback_type: Literal[
        "incorrect", "incomplete", "outdated", "bad_citation", "permission", "suggestion", "positive"
    ]
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str = Field(default="", max_length=10000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class FeedbackUpdate(BaseModel):
    status: Literal["open", "triaged", "converted", "resolved", "dismissed"] | None = None
    comment: str | None = Field(default=None, max_length=10000)
