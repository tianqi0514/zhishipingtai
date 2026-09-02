from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from packages.platform.media import normalize_media_policy


SOURCE_TYPES = Literal[
    "web", "rest", "rss", "sitemap", "git", "database", "email", "mcp",
    "google_drive", "mongodb", "elasticsearch", "opensearch", "duckdb",
    "parquet", "arrow", "huggingface", "stream", "snowflake", "databricks",
    "local_dir", "s3", "sftp", "ftp", "ftps", "webdav", "smb", "onedrive",
    "sharepoint", "object_prefix",
]


def _validate_source_config(value: dict[str, Any]) -> dict[str, Any]:
    value = dict(value)
    for forbidden in ("password", "api_key", "access_token", "refresh_token", "client_secret", "private_key"):
        if value.get(forbidden) not in (None, ""):
            raise ValueError(f"{forbidden} 属于敏感凭据，必须通过加密密钥字段保存")
    if "url" in value:
        url = str(value.get("url") or "").strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("数据源 URL 必须是有效的 HTTP 或 HTTPS 地址")
        if parsed.username or parsed.password:
            raise ValueError("URL 中不能包含账号或密码，请使用独立的访问密钥字段")
        value["url"] = url
    private_flag = value.get("allow_private_ips")
    private_enabled = (
        private_flag is not None
        and private_flag is not False
        and private_flag != 0
        and str(private_flag).strip().lower() not in {"", "0", "false", "no", "off"}
    )
    if private_enabled:
        raise ValueError("平台不允许数据源访问私有、回环或链路本地地址")
    for key in ("headers", "params"):
        if key in value and value[key] is not None and not isinstance(value[key], dict):
            raise ValueError(f"{key} 必须是 JSON 对象")
    for header_name, header_value in (value.get("headers") or {}).items():
        if not isinstance(header_name, str) or not isinstance(header_value, str):
            raise ValueError("headers 的名称和值都必须是字符串")
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,200}", header_name):
            raise ValueError("请求头名称不合法")
        if "\r" in header_value or "\n" in header_value:
            raise ValueError("请求头值不能包含换行符")
        if header_name.casefold() in {"authorization", "proxy-authorization", "x-api-key", "api-key"}:
            raise ValueError("认证请求头必须通过加密密钥字段配置")
    for key, minimum, maximum in (
        ("delay", 0, 60),
        ("timeout", 1, 120),
        ("max_retries", 0, 10),
        ("schedule_minutes", 0, 10080),
        ("max_urls", 1, 1000),
        ("max_items", 1, 1000),
        ("max_rows_per_table", 1, 100000),
        ("max_emails", 1, 1000),
        ("max_files", 1, 5000),
        ("max_depth", 0, 32),
        ("limit", 1, 100000),
        ("max_messages", 1, 10000),
        ("max_media_items_per_sync", 1, 10000),
    ):
        if key not in value or value[key] in (None, ""):
            continue
        try:
            number = float(value[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 必须是数字") from exc
        if number < minimum or number > maximum:
            raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间")
        value[key] = int(number) if key != "delay" else number
    if "respect_robots" in value and not isinstance(value["respect_robots"], bool):
        raise ValueError("respect_robots 必须是布尔值")
    if "method" in value:
        method = str(value["method"] or "GET").upper()
        if method not in {"GET", "POST"}:
            raise ValueError("REST 数据源仅支持 GET 或 POST")
        value["method"] = method
    if "response_mode" in value and value["response_mode"] not in {"json", "binary"}:
        raise ValueError("REST 响应类型仅支持 json 或 binary")
    if "media_sync_failure_mode" in value and value["media_sync_failure_mode"] not in {"partial", "fail"}:
        raise ValueError("媒体同步失败处理仅支持 partial 或 fail")
    for key in ("media_allow_sync_override", "media_cloud_processing_confirmed"):
        if key in value and not isinstance(value[key], bool):
            raise ValueError(f"{key} 必须是布尔值")
    if "media_policy_override" in value:
        override = value["media_policy_override"]
        if not isinstance(override, dict):
            raise ValueError("media_policy_override 必须是对象")
        # Validate the partial override against the same strict schema used by
        # upload and processing. Keep the partial shape so inheritance from a
        # selected policy is not accidentally replaced by default values.
        normalize_media_policy(override)
    for key in ("secret_header", "secret_prefix"):
        if key not in value:
            continue
        text = str(value[key])
        if "\r" in text or "\n" in text:
            raise ValueError(f"{key} 不能包含换行符")
        if key == "secret_header" and not re.fullmatch(r"[A-Za-z0-9-]{1,100}", text):
            raise ValueError("密钥请求头名称不合法")
        if key == "secret_prefix" and len(text) > 100:
            raise ValueError("密钥前缀不能超过 100 个字符")
    return value


def _validate_source_type_config(source_type: str, config: dict[str, Any]) -> None:
    if source_type in {"web", "rest", "rss", "sitemap", "git", "mcp", "webdav", "elasticsearch", "opensearch"} and not config.get("url"):
        raise ValueError("该数据源必须配置 URL")
    if source_type == "web" and "method" in config:
        raise ValueError("网页数据源不能配置 REST 请求方法")
    if source_type == "database":
        required = ["dialect", "host", "database", "username"]
        missing = [key for key in required if not str(config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"数据库配置缺少：{', '.join(missing)}")
        if config.get("dialect") not in {"postgresql", "mysql"}:
            raise ValueError("数据库类型仅支持 postgresql 或 mysql")
        tables = config.get("include_tables")
        if tables is not None and not isinstance(tables, list):
            raise ValueError("include_tables 必须是数组")
    if source_type == "email":
        required = ["server", "username"]
        missing = [key for key in required if not str(config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"邮箱配置缺少：{', '.join(missing)}")
        if config.get("protocol", "imap") not in {"imap", "pop3"}:
            raise ValueError("邮箱协议仅支持 imap 或 pop3")
    required_by_type = {
        "mongodb": ["host", "database", "collection"],
        "duckdb": ["path"],
        "parquet": ["path"],
        "arrow": ["path"],
        "huggingface": ["dataset"],
        "snowflake": ["account", "username", "warehouse", "database", "table"],
        "databricks": ["host", "http_path", "table"],
        "local_dir": ["path"],
        "s3": ["endpoint", "bucket", "access_key"],
        "object_prefix": ["endpoint", "bucket", "access_key"],
        "sftp": ["host", "username"],
        "ftp": ["host"],
        "ftps": ["host"],
        "smb": ["server", "share", "username"],
        "stream": ["stream_type", "host", "queue"],
    }
    missing = [key for key in required_by_type.get(source_type, []) if not str(config.get(key) or "").strip()]
    if missing:
        raise ValueError(f"{source_type} 配置缺少：{', '.join(missing)}")
    if source_type == "google_drive" and not str(config.get("folder_id") or "root").strip():
        raise ValueError("Google Drive folder_id 不能为空")
    if source_type in {"onedrive", "sharepoint"} and not isinstance(config.get("recursive", True), bool):
        raise ValueError("recursive 必须是布尔值")
    if source_type == "stream" and config.get("stream_type") != "rabbitmq":
        raise ValueError("当前仅支持 RabbitMQ 流同步")


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str | None = None
    new_password: str = Field(min_length=10)


class OrgCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None
    unit_type: str = "department"
    sort_order: int = 0
    enabled: bool = True


class OrgUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: str | None = None
    unit_type: str | None = None
    sort_order: int | None = None
    enabled: bool | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=10)
    display_name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    org_unit_id: str | None = None
    is_admin: bool = False
    enabled: bool = True
    role_ids: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    org_unit_id: str | None = None
    is_admin: bool | None = None
    enabled: bool | None = None
    role_ids: list[str] | None = None


class RoleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    permissions: list[str] = Field(default_factory=list)
    enabled: bool = True


class RoleUpdate(BaseModel):
    name: str | None = None
    permissions: list[str] | None = None
    enabled: bool | None = None


class SpaceCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    owner_id: str | None = None
    media_policy_id: str | None = None
    enabled: bool = True


class SpaceUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    description: str | None = None
    owner_id: str | None = None
    media_policy_id: str | None = None
    enabled: bool | None = None


class GrantCreate(BaseModel):
    subject_type: Literal["user", "role", "org"]
    subject_id: str
    permission: Literal["read", "write", "manage"]
    effect: Literal["allow", "deny"] = "allow"


class ModelConfigCreate(BaseModel):
    name: str
    model_kind: Literal["llm", "embedding", "reranker", "vision", "asr"]
    provider: Literal[
        "kimi", "openai", "openai_compatible", "groq", "litellm", "huggingface", "bge", "fastembed"
    ]
    model_name: str
    base_url: str | None = None
    api_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False


class ModelConfigUpdate(BaseModel):
    name: str | None = None
    model_kind: str | None = None
    provider: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class ParserPolicyCreate(BaseModel):
    name: str
    parser_type: Literal["auto", "native", "docling"] = "auto"
    enable_ocr: bool = True
    ocr_language: str = "chi_sim+eng"
    extract_tables: bool = True
    extract_images: bool = False
    max_pages: int | None = Field(default=None, ge=1)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False


class ParserPolicyUpdate(BaseModel):
    name: str | None = None
    parser_type: str | None = None
    enable_ocr: bool | None = None
    ocr_language: str | None = None
    extract_tables: bool | None = None
    extract_images: bool | None = None
    max_pages: int | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class MediaPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    applicable_media_types: list[Literal["image", "audio", "video"]] = Field(
        default_factory=lambda: ["image", "audio", "video"]
    )
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("策略名称不能为空")
        return value

    @field_validator("applicable_media_types")
    @classmethod
    def validate_media_types(cls, value: list[str]) -> list[str]:
        value = list(dict.fromkeys(value))
        if not value:
            raise ValueError("至少选择一种适用媒介")
        return value

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        return normalize_media_policy(value)


class MediaPolicyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    applicable_media_types: list[Literal["image", "audio", "video"]] | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    is_default: bool | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("策略名称不能为空")
        return value

    @field_validator("applicable_media_types")
    @classmethod
    def validate_media_types(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        value = list(dict.fromkeys(value))
        if not value:
            raise ValueError("至少选择一种适用媒介")
        return value

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return normalize_media_policy(value) if value is not None else None


class MediaFrameEstimateRequest(BaseModel):
    duration_seconds: float = Field(ge=0, le=86_400)
    media_policy_id: str | None = None
    override: dict[str, Any] = Field(default_factory=dict)


class MediaReprocessRequest(BaseModel):
    media_policy_id: str | None = None
    override: dict[str, Any] = Field(default_factory=dict)
    bypass_cache: bool = False
    cloud_processing_confirmed: bool = False


class SourceCreate(BaseModel):
    space_id: str
    name: str = Field(min_length=1, max_length=200)
    source_type: SOURCE_TYPES
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None
    media_policy_id: str | None = None
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("数据源名称不能为空")
        return value

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_source_config(value)

    @model_validator(mode="after")
    def validate_type_config(self):
        _validate_source_type_config(self.source_type, self.config)
        return self


class SourceUpdate(BaseModel):
    space_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    source_type: SOURCE_TYPES | None = None
    config: dict[str, Any] | None = None
    secret: str | None = None
    clear_secret: bool = False
    media_policy_id: str | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("数据源名称不能为空")
        return value

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_source_config(value) if value is not None else None


class SourceConnectionTest(BaseModel):
    source_id: str | None = None
    space_id: str | None = None
    source_type: SOURCE_TYPES
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None
    clear_secret: bool = False

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_source_config(value)

    @model_validator(mode="after")
    def validate_target(self):
        if not self.source_id and not self.space_id:
            raise ValueError("测试新数据源时必须选择知识空间")
        _validate_source_type_config(self.source_type, self.config)
        return self


class SourceSyncRequest(BaseModel):
    media_policy_id: str | None = None
    media_policy_override: dict[str, Any] = Field(default_factory=dict)
    cloud_processing_confirmed: bool = False


class DocumentUpdate(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    owner_id: str | None = None


class ChunkPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    method: Literal["recursive", "sentence", "paragraph", "token", "semantic"] = "recursive"
    chunk_size: int = Field(default=800, ge=100, le=10000)
    chunk_overlap: int = Field(default=120, ge=0, le=5000)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False

    @model_validator(mode="after")
    def validate_overlap(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("重叠长度必须小于切片长度")
        return self


class ChunkPolicyUpdate(BaseModel):
    name: str | None = None
    method: str | None = None
    chunk_size: int | None = Field(default=None, ge=100, le=10000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=5000)
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class ExtractionPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    model_config_id: str | None = None
    min_confidence: float = Field(default=0.65, ge=0, le=1)
    max_chunks: int = Field(default=30, ge=1, le=500)
    entity_types: list[str] = Field(default_factory=list)
    relation_types: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False


class ExtractionPolicyUpdate(BaseModel):
    name: str | None = None
    model_config_id: str | None = None
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    max_chunks: int | None = Field(default=None, ge=1, le=500)
    entity_types: list[str] | None = None
    relation_types: list[str] | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class GovernancePolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    similarity_threshold: float = Field(default=0.86, ge=0, le=1)
    publish_confidence: float = Field(default=0.72, ge=0, le=1)
    conflict_strategy: Literal["highest_confidence", "latest", "keep_all"] = "highest_confidence"
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False


class GovernancePolicyUpdate(BaseModel):
    name: str | None = None
    similarity_threshold: float | None = Field(default=None, ge=0, le=1)
    publish_confidence: float | None = Field(default=None, ge=0, le=1)
    conflict_strategy: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class CurationDecisionCreate(BaseModel):
    space_id: str
    target_type: Literal[
        "document_profile", "content_element", "chunk", "entity", "fact",
        "entity_pair", "quality_issue",
    ]
    target_id: str = Field(min_length=1, max_length=500)
    version_id: str | None = None
    field_path: str = Field(default="status", min_length=1, max_length=200)
    operation: Literal[
        "accept", "override", "reject", "suppress", "restore", "merge", "split",
        "must_link", "cannot_link", "lock", "unlock", "resolve", "ignore",
    ]
    value: Any = None
    scope: Literal["version_only", "document_future", "space"] = "version_only"
    reason_code: str = Field(default="manual_correction", max_length=100)
    reason_note: str = Field(default="", max_length=2000)
    base_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    batch_id: str | None = None
    auto_publish: bool = True


class CurationBatchCreate(BaseModel):
    space_id: str
    name: str = Field(default="批量人工治理", min_length=1, max_length=300)


class CurationCaseUpdate(BaseModel):
    status: Literal["open", "handled", "ignored"]
    resolution: Literal["accepted_automatic", "corrected", "ignored", "reopened"] | None = None
    reason_note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def ignored_requires_reason(self):
        if self.status == "ignored" and not self.reason_note.strip():
            raise ValueError("忽略问题时必须填写原因")
        return self


class CurationProfileUpdate(BaseModel):
    space_id: str
    changes: dict[str, Any]
    scope: Literal["version_only", "document_future"] = "version_only"
    reason_note: str = Field(min_length=1, max_length=2000)
    case_id: str | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        editable = {
            "summary", "classification", "document_type", "tags", "keywords",
            "main_objects", "time_range",
        }
        if not self.changes:
            raise ValueError("至少修改一个画像字段")
        unknown = sorted(set(self.changes) - editable)
        if unknown:
            raise ValueError(f"画像字段不可人工修改：{', '.join(unknown)}")
        return self


class EntityPairCuration(BaseModel):
    space_id: str
    left_entity_id: str
    right_entity_id: str
    operation: Literal["must_link", "cannot_link", "merge", "split"]
    winner_entity_id: str | None = None
    reason_note: str = Field(default="", max_length=2000)


class OntologyCreate(BaseModel):
    space_id: str | None = None
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    namespace: str = Field(min_length=1, max_length=500)
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class OntologyUpdate(BaseModel):
    space_id: str | None = None
    code: str | None = None
    name: str | None = None
    namespace: str | None = None
    description: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class OntologyTermCreate(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=500)
    term_type: Literal["class", "property", "relation", "event"] = "class"
    parent_code: str | None = None
    aliases: list[str] = Field(default_factory=list)
    definition: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class OntologyTermUpdate(BaseModel):
    code: str | None = None
    label: str | None = None
    term_type: str | None = None
    parent_code: str | None = None
    aliases: list[str] | None = None
    definition: str | None = None
    constraints: dict[str, Any] | None = None
    enabled: bool | None = None


class KnowledgeEntityCreate(BaseModel):
    space_id: str
    canonical_name: str = Field(min_length=1, max_length=500)
    entity_type: str = Field(default="其他", min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0, le=1)
    status: str = Field(default="published", min_length=1, max_length=32)


class KnowledgeEntityUpdate(BaseModel):
    canonical_name: str | None = Field(default=None, min_length=1, max_length=500)
    entity_type: str | None = Field(default=None, min_length=1, max_length=100)
    aliases: list[str] | None = None
    properties: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: str | None = Field(default=None, min_length=1, max_length=32)
    reason_note: str | None = Field(default=None, max_length=2000)


class KnowledgeFactCreate(BaseModel):
    space_id: str
    subject_entity_id: str
    predicate: str = Field(min_length=1, max_length=200)
    object_entity_id: str | None = None
    object_value: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    status: str = Field(default="published", min_length=1, max_length=32)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def exactly_one_object(self) -> "KnowledgeFactCreate":
        if bool(self.object_entity_id) == bool((self.object_value or "").strip()):
            raise ValueError("关系必须且只能选择一个客体节点或客体值")
        if self.object_value is not None:
            self.object_value = self.object_value.strip()
        return self


class KnowledgeFactUpdate(BaseModel):
    subject_entity_id: str | None = None
    predicate: str | None = Field(default=None, min_length=1, max_length=200)
    object_entity_id: str | None = None
    object_value: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: str | None = Field(default=None, min_length=1, max_length=32)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    reason_note: str | None = Field(default=None, max_length=2000)


class AnalysisCondition(BaseModel):
    predicate: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=500)
    object: str = Field(min_length=1, max_length=500)


class AnalysisRuleDefinition(BaseModel):
    conditions: list[AnalysisCondition] = Field(min_length=1, max_length=12)
    conclusion: AnalysisCondition


class AnalysisRuleSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    category: str = Field(default="通用", min_length=1, max_length=100)
    space_ids: list[str] = Field(default_factory=list, max_length=50)
    auto_run: bool = False
    auto_publish: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class AnalysisRuleSetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    space_ids: list[str] | None = Field(default=None, max_length=50)
    auto_run: bool | None = None
    auto_publish: bool | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class AnalysisRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    definition: AnalysisRuleDefinition | None = None
    dsl: str | None = Field(default=None, min_length=1, max_length=8000)
    priority: int = Field(default=100, ge=0, le=10000)
    confidence: float = Field(default=1.0, ge=0, le=1)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def definition_or_dsl(self) -> "AnalysisRuleCreate":
        if self.definition is None and not (self.dsl or "").strip():
            raise ValueError("必须配置可视化规则定义或 DSL")
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise ValueError("规则失效时间必须晚于生效时间")
        return self


class AnalysisRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    definition: AnalysisRuleDefinition | None = None
    dsl: str | None = Field(default=None, min_length=1, max_length=8000)
    priority: int | None = Field(default=None, ge=0, le=10000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    enabled: bool | None = None


class AnalysisScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    category: str = Field(default="通用", min_length=1, max_length=100)
    rule_set_id: str | None = None
    space_ids: list[str] = Field(default_factory=list, max_length=50)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class AnalysisScenarioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    rule_set_id: str | None = None
    space_ids: list[str] | None = Field(default=None, max_length=50)
    input_schema: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class InferenceRunCreate(BaseModel):
    rule_set_id: str
    scenario_id: str | None = None
    space_ids: list[str] = Field(default_factory=list, max_length=50)
    mode: Literal["preview", "publish"] = "preview"
    max_results: int = Field(default=1000, ge=1, le=10000)


class SavedGraphQueryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    query_type: Literal["sparql", "visual"] = "sparql"
    space_ids: list[str] = Field(default_factory=list, max_length=50)
    query_text: str = Field(default="", max_length=20000)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class SavedGraphQueryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    query_type: Literal["sparql", "visual"] | None = None
    space_ids: list[str] | None = Field(default=None, max_length=50)
    query_text: str | None = Field(default=None, max_length=20000)
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class SparqlAnalysisRequest(BaseModel):
    space_ids: list[str] = Field(min_length=1, max_length=50)
    query: str = Field(min_length=1, max_length=20000)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    space_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=10, ge=1, le=50)
    use_keyword: bool = True
    use_vector: bool = True
    use_graph: bool = True
    use_reranker: bool = True
    filters: dict[str, Any] = Field(default_factory=dict)


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=300)
    space_ids: list[str] = Field(default_factory=list)
    use_keyword: bool = True
    use_vector: bool = True
    use_graph: bool = True
    use_reranker: bool = True
    top_k: int = Field(default=10, ge=1, le=50)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    space_ids: list[str] | None = None
    use_keyword: bool | None = None
    use_vector: bool | None = None
    use_graph: bool | None = None
    use_reranker: bool | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)


class ConversationMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class AgentCredentialRequest(BaseModel):
    harness_session_id: str = Field(min_length=1, max_length=100)


class AgentKnowledgeSearchRequest(SearchRequest):
    conversation_id: str


class AgentGraphQueryRequest(BaseModel):
    conversation_id: str
    space_ids: list[str] = Field(default_factory=list)
    entity_query: str = Field(default="", max_length=1000)
    relation_query: str = Field(default="", max_length=1000)
    limit: int = Field(default=20, ge=1, le=100)


class AgentKnowledgeReasonRequest(BaseModel):
    conversation_id: str
    goal: str = Field(min_length=1, max_length=2000)
    space_ids: list[str] = Field(default_factory=list, max_length=50)
    rule_set_ids: list[str] = Field(default_factory=list, max_length=20)
    max_results: int = Field(default=100, ge=1, le=500)
