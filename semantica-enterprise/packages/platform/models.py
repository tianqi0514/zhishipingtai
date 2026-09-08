from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class OrgUnit(Base, TimestampMixin):
    __tablename__ = "org_units"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_org_tenant_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("org_units.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    unit_type: Mapped[str] = mapped_column(String(32), default="department")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(ForeignKey("org_units.id"), nullable=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    display_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_role_tenant_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), primary_key=True)


class KnowledgeSpace(Base, TimestampMixin):
    __tablename__ = "knowledge_spaces"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_space_tenant_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    security_label: Mapped[str] = mapped_column(String(32), default="internal")
    media_policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_parsing_policies.id"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class SpaceGrant(Base, TimestampMixin):
    __tablename__ = "space_grants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    permission: Mapped[str] = mapped_column(String(32))
    effect: Mapped[str] = mapped_column(String(8), default="allow")


class ModelConfig(Base, TimestampMixin):
    __tablename__ = "model_configs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    model_kind: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    last_test_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelRoutingPolicy(Base, TimestampMixin):
    __tablename__ = "model_routing_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_model_routing_policy_tenant_name"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    routes: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class ParserPolicy(Base, TimestampMixin):
    __tablename__ = "parser_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    parser_type: Mapped[str] = mapped_column(String(32), default="auto")
    enable_ocr: Mapped[bool] = mapped_column(Boolean, default=True)
    ocr_language: Mapped[str] = mapped_column(String(64), default="chi_sim+eng")
    extract_tables: Mapped[bool] = mapped_column(Boolean, default=True)
    extract_images: Mapped[bool] = mapped_column(Boolean, default=False)
    max_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class MediaParsingPolicy(Base, TimestampMixin):
    __tablename__ = "media_parsing_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_media_policy_tenant_name"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    applicable_media_types: Mapped[list] = mapped_column(
        JSON, default=lambda: ["image", "audio", "video"]
    )
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class MediaParsingPolicyVersion(Base, TimestampMixin):
    __tablename__ = "media_parsing_policy_versions"
    __table_args__ = (
        UniqueConstraint("policy_id", "version_number", name="uq_media_policy_version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("media_parsing_policies.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class SourceConnector(Base, TimestampMixin):
    __tablename__ = "source_connectors"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_parsing_policies.id"), nullable=True, index=True
    )


class DataSourceSchemaVersion(Base, TimestampMixin):
    __tablename__ = "data_source_schema_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "version_number", name="uq_source_schema_version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_connectors.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    schema_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    catalog: Mapped[dict] = mapped_column(JSON, default=dict)
    diff_from_previous: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="current", index=True)
    object_count: Mapped[int] = mapped_column(Integer, default=0)
    column_count: Mapped[int] = mapped_column(Integer, default=0)
    primary_key_count: Mapped[int] = mapped_column(Integer, default=0)
    foreign_key_count: Mapped[int] = mapped_column(Integer, default=0)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DataPreviewPolicy(Base, TimestampMixin):
    __tablename__ = "data_preview_policies"
    __table_args__ = (UniqueConstraint("source_id", name="uq_data_preview_policy_source"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_connectors.id"), index=True)
    live_preview_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_objects: Mapped[list] = mapped_column(JSON, default=list)
    denied_objects: Mapped[list] = mapped_column(JSON, default=list)
    allowed_columns: Mapped[dict] = mapped_column(JSON, default=dict)
    sensitive_columns: Mapped[dict] = mapped_column(JSON, default=dict)
    masking_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    default_order: Mapped[dict] = mapped_column(JSON, default=dict)
    default_page_size: Mapped[int] = mapped_column(Integer, default=20)
    max_page_size: Mapped[int] = mapped_column(Integer, default=100)
    max_text_length: Mapped[int] = mapped_column(Integer, default=500)
    allow_full_cell: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_exact_count: Mapped[bool] = mapped_column(Boolean, default=False)
    query_timeout_seconds: Mapped[int] = mapped_column(Integer, default=15)
    max_filter_conditions: Mapped[int] = mapped_column(Integer, default=10)
    max_result_bytes: Mapped[int] = mapped_column(Integer, default=2_000_000)


class SemanticMappingSet(Base, TimestampMixin):
    __tablename__ = "semantic_mapping_sets"
    __table_args__ = (UniqueConstraint("source_id", "name", name="uq_semantic_mapping_source_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_connectors.id"), index=True)
    ontology_id: Mapped[str] = mapped_column(ForeignKey("ontologies.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    active_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class SemanticMappingVersion(Base, TimestampMixin):
    __tablename__ = "semantic_mapping_versions"
    __table_args__ = (
        UniqueConstraint("mapping_set_id", "version_number", name="uq_semantic_mapping_version"),
        UniqueConstraint("mapping_set_id", "mapping_hash", name="uq_semantic_mapping_hash"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_connectors.id"), index=True)
    mapping_set_id: Mapped[str] = mapped_column(ForeignKey("semantic_mapping_sets.id"), index=True)
    schema_version_id: Mapped[str] = mapped_column(ForeignKey("data_source_schema_versions.id"), index=True)
    schema_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    mapping_hash: Mapped[str] = mapped_column(String(64), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    activated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StructuredQueryRun(Base, TimestampMixin):
    __tablename__ = "structured_query_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_connectors.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    mapping_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("semantic_mapping_versions.id"), nullable=True, index=True
    )
    schema_version_id: Mapped[str] = mapped_column(ForeignKey("data_source_schema_versions.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_messages.id"), nullable=True, index=True)
    original_question: Mapped[str] = mapped_column(Text, default="")
    semantic_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    plan_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    query_ir: Mapped[dict] = mapped_column(JSON, default=dict)
    ir_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    dialect: Mapped[str] = mapped_column(String(32))
    sql_template: Mapped[str] = mapped_column(Text, default="")
    parameter_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    referenced_objects: Mapped[list] = mapped_column(JSON, default=list)
    referenced_columns: Mapped[list] = mapped_column(JSON, default=list)
    result_columns: Mapped[list] = mapped_column(JSON, default=list)
    result_rows: Mapped[list] = mapped_column(JSON, default=list)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    result_bytes: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StructuredQueryCitation(Base, TimestampMixin):
    __tablename__ = "structured_query_citations"
    __table_args__ = (
        UniqueConstraint("query_run_id", "citation_number", name="uq_structured_query_citation"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    query_run_id: Mapped[str] = mapped_column(ForeignKey("structured_query_runs.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_messages.id"), nullable=True, index=True)
    citation_number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(500))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source_connectors.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    security_label: Mapped[str] = mapped_column(String(32), default="internal")
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)


class DocumentVersion(Base, TimestampMixin):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        UniqueConstraint("document_id", "sha256", name="uq_document_content"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(200))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    object_key: Mapped[str] = mapped_column(String(1000))
    parser_policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("parser_policies.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    media_policy_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_parsing_policy_versions.id"), nullable=True, index=True
    )
    media_policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class MediaProcessingRun(Base, TimestampMixin):
    __tablename__ = "media_processing_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    policy_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_parsing_policy_versions.id"), nullable=True, index=True
    )
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    media_type: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    input_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    cache: Mapped[dict] = mapped_column(JSON, default=dict)
    probe: Mapped[dict] = mapped_column(JSON, default=dict)
    asr_model_config_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_configs.id"), nullable=True
    )
    vision_model_config_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_configs.id"), nullable=True
    )
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MediaAudioSegment(Base, TimestampMixin):
    __tablename__ = "media_audio_segments"
    __table_args__ = (
        UniqueConstraint("run_id", "segment_index", name="uq_media_audio_segment"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("media_processing_runs.id"), index=True)
    segment_index: Mapped[int] = mapped_column(Integer)
    time_start: Mapped[float] = mapped_column(Float)
    time_end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    speaker: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    segment_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class MediaScene(Base, TimestampMixin):
    __tablename__ = "media_scenes"
    __table_args__ = (UniqueConstraint("run_id", "scene_index", name="uq_media_scene"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("media_processing_runs.id"), index=True)
    scene_index: Mapped[int] = mapped_column(Integer)
    time_start: Mapped[float] = mapped_column(Float)
    time_end: Mapped[float] = mapped_column(Float)
    detection_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="ready")


class MediaFrame(Base, TimestampMixin):
    __tablename__ = "media_frames"
    __table_args__ = (UniqueConstraint("run_id", "frame_index", name="uq_media_frame"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("media_processing_runs.id"), index=True)
    scene_id: Mapped[str | None] = mapped_column(ForeignKey("media_scenes.id"), nullable=True, index=True)
    frame_index: Mapped[int] = mapped_column(Integer)
    timestamp_seconds: Mapped[float] = mapped_column(Float, index=True)
    object_key: Mapped[str] = mapped_column(String(1000))
    thumbnail_key: Mapped[str] = mapped_column(String(1000))
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    selection_reason: Mapped[str] = mapped_column(String(64), default="interval")
    ocr_status: Mapped[str] = mapped_column(String(32), default="not_configured")
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    vision_status: Mapped[str] = mapped_column(String(32), default="not_configured")
    vision_result: Mapped[dict] = mapped_column(JSON, default=dict)
    frame_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class ContentElement(Base, TimestampMixin):
    __tablename__ = "content_elements"
    __table_args__ = (UniqueConstraint("version_id", "element_id", name="uq_version_element"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True)
    element_id: Mapped[str] = mapped_column(String(64))
    element_type: Mapped[str] = mapped_column(String(32))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    structural_path: Mapped[str] = mapped_column(String(500))
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[list | None] = mapped_column(JSON, nullable=True)
    element_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    security_label: Mapped[str] = mapped_column(String(32), default="internal")
    scope_tokens: Mapped[list] = mapped_column(JSON, default=list)


class DocumentProfile(Base, TimestampMixin):
    __tablename__ = "document_profiles"
    __table_args__ = (UniqueConstraint("version_id", name="uq_document_profile_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    classification: Mapped[str] = mapped_column(String(300), default="未分类", index=True)
    document_type: Mapped[str] = mapped_column(String(100), default="其他")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(String(32), default="unknown")
    main_objects: Mapped[list] = mapped_column(JSON, default=list)
    time_range: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    completeness_score: Mapped[float] = mapped_column(Float, default=0.0)
    readability_score: Mapped[float] = mapped_column(Float, default=0.0)
    structure_score: Mapped[float] = mapped_column(Float, default=0.0)
    media_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    duplicate_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    quality_issues: Mapped[list] = mapped_column(JSON, default=list)
    recommended_actions: Mapped[list] = mapped_column(JSON, default=list)
    deterministic_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_id: Mapped[str] = mapped_column(ForeignKey("governance_policies.id"), index=True)
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    model_config_id: Mapped[str | None] = mapped_column(ForeignKey("model_configs.id"), nullable=True)
    model_status: Mapped[str] = mapped_column(String(32), default="not_configured")
    model_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobStep(Base, TimestampMixin):
    __tablename__ = "job_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    sequence: Mapped[int] = mapped_column(Integer)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    object_type: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChunkPolicy(Base, TimestampMixin):
    __tablename__ = "chunk_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    method: Mapped[str] = mapped_column(String(64), default="recursive")
    chunk_size: Mapped[int] = mapped_column(Integer, default=800)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=120)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class ExtractionPolicy(Base, TimestampMixin):
    __tablename__ = "extraction_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    model_config_id: Mapped[str | None] = mapped_column(ForeignKey("model_configs.id"), nullable=True)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.65)
    max_chunks: Mapped[int] = mapped_column(Integer, default=30)
    entity_types: Mapped[list] = mapped_column(JSON, default=list)
    relation_types: Mapped[list] = mapped_column(JSON, default=list)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class GovernancePolicy(Base, TimestampMixin):
    __tablename__ = "governance_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    similarity_threshold: Mapped[float] = mapped_column(Float, default=0.86)
    publish_confidence: Mapped[float] = mapped_column(Float, default=0.72)
    conflict_strategy: Mapped[str] = mapped_column(String(64), default="highest_confidence")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class Chunk(Base, TimestampMixin):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("version_id", "chunk_id", name="uq_version_chunk"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True)
    element_id: Mapped[str | None] = mapped_column(ForeignKey("content_elements.id"), nullable=True)
    chunk_policy_id: Mapped[str] = mapped_column(ForeignKey("chunk_policies.id"))
    chunk_id: Mapped[str] = mapped_column(String(64), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    structural_path: Mapped[str] = mapped_column(String(500))
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_span: Mapped[dict] = mapped_column(JSON, default=dict)
    parent_chunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope_tokens: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="staged")


class ExtractionRun(Base, TimestampMixin):
    __tablename__ = "extraction_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("extraction_policies.id"))
    model_config_id: Mapped[str] = mapped_column(ForeignKey("model_configs.id"))
    status: Mapped[str] = mapped_column(String(32), default="running")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EntityMention(Base, TimestampMixin):
    __tablename__ = "entity_mentions"
    __table_args__ = (UniqueConstraint("run_id", "mention_id", name="uq_run_mention"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"), index=True)
    mention_id: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(String(500))
    normalized_name: Mapped[str] = mapped_column(String(500), index=True)
    entity_type: Mapped[str] = mapped_column(String(100), index=True)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    scope_tokens: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="staged")


class RelationAssertion(Base, TimestampMixin):
    __tablename__ = "relation_assertions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"), index=True)
    subject_name: Mapped[str] = mapped_column(String(500), index=True)
    predicate: Mapped[str] = mapped_column(String(200), index=True)
    object_name: Mapped[str] = mapped_column(String(500), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[str] = mapped_column(Text, default="")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    scope_tokens: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="staged")


class EventAssertion(Base, TimestampMixin):
    __tablename__ = "event_assertions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(200), index=True)
    trigger: Mapped[str] = mapped_column(String(500))
    participants: Mapped[list] = mapped_column(JSON, default=list)
    event_time: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="staged")


class CanonicalEntity(Base, TimestampMixin):
    __tablename__ = "canonical_entities"
    __table_args__ = (UniqueConstraint("space_id", "normalized_name", "entity_type", name="uq_space_entity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    canonical_name: Mapped[str] = mapped_column(String(500), index=True)
    normalized_name: Mapped[str] = mapped_column(String(500), index=True)
    entity_type: Mapped[str] = mapped_column(String(100), index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source_count: Mapped[int] = mapped_column(Integer, default=1)
    scope_tokens: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="published")


class Fact(Base, TimestampMixin):
    __tablename__ = "facts"
    __table_args__ = (UniqueConstraint("space_id", "subject_entity_id", "predicate", "object_entity_id", "source_chunk_id", name="uq_fact_source"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    subject_entity_id: Mapped[str] = mapped_column(ForeignKey("canonical_entities.id"), index=True)
    predicate: Mapped[str] = mapped_column(String(200), index=True)
    object_entity_id: Mapped[str | None] = mapped_column(ForeignKey("canonical_entities.id"), nullable=True, index=True)
    object_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("chunks.id"), nullable=True, index=True
    )
    confidence: Mapped[float] = mapped_column(Float)
    scope_tokens: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="published")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalysisRuleSet(Base, TimestampMixin):
    __tablename__ = "analysis_rule_sets"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_analysis_rule_set_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), default="通用", index=True)
    space_ids: Mapped[list] = mapped_column(JSON, default=list)
    auto_run: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AnalysisRule(Base, TimestampMixin):
    __tablename__ = "analysis_rules"
    __table_args__ = (UniqueConstraint("rule_set_id", "name", name="uq_analysis_rule_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    rule_set_id: Mapped[str] = mapped_column(ForeignKey("analysis_rule_sets.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    dsl: Mapped[str] = mapped_column(Text)
    head_predicate: Mapped[str] = mapped_column(String(200), index=True)
    body_predicates: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AnalysisRuleVersion(Base):
    __tablename__ = "analysis_rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version", name="uq_analysis_rule_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("analysis_rules.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    dsl: Mapped[str] = mapped_column(Text)
    compiled: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisScenario(Base, TimestampMixin):
    __tablename__ = "analysis_scenarios"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_analysis_scenario_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), default="通用", index=True)
    rule_set_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_rule_sets.id"), nullable=True, index=True)
    space_ids: Mapped[list] = mapped_column(JSON, default=list)
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class InferenceRun(Base, TimestampMixin):
    __tablename__ = "inference_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    rule_set_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_rule_sets.id"), nullable=True, index=True)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_scenarios.id"), nullable=True, index=True)
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), default="manual")
    mode: Mapped[str] = mapped_column(String(32), default="preview")
    space_ids: Mapped[list] = mapped_column(JSON, default=list)
    graph_releases: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    run_input: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InferredFact(Base, TimestampMixin):
    __tablename__ = "inferred_facts"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "rule_id", "space_id", "subject_entity_id", "predicate", "object_entity_id",
            name="uq_inferred_fact_run_rule",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("inference_runs.id"), index=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("analysis_rules.id"), index=True)
    rule_version_id: Mapped[str] = mapped_column(ForeignKey("analysis_rule_versions.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    subject_entity_id: Mapped[str] = mapped_column(ForeignKey("canonical_entities.id"), index=True)
    predicate: Mapped[str] = mapped_column(String(200), index=True)
    object_entity_id: Mapped[str | None] = mapped_column(ForeignKey("canonical_entities.id"), nullable=True, index=True)
    object_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="preview", index=True)
    proof: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InferenceEvidence(Base):
    __tablename__ = "inference_evidence"
    __table_args__ = (
        UniqueConstraint("inferred_fact_id", "ordinal", name="uq_inference_evidence_ordinal"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    inferred_fact_id: Mapped[str] = mapped_column(ForeignKey("inferred_facts.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    premise_type: Mapped[str] = mapped_column(String(32), default="asserted")
    source_fact_id: Mapped[str | None] = mapped_column(ForeignKey("facts.id"), nullable=True, index=True)
    source_inferred_fact_id: Mapped[str | None] = mapped_column(ForeignKey("inferred_facts.id"), nullable=True, index=True)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True, index=True)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SavedGraphQuery(Base, TimestampMixin):
    __tablename__ = "saved_graph_queries"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "name", name="uq_saved_graph_query_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    query_type: Mapped[str] = mapped_column(String(32), default="sparql")
    space_ids: Mapped[list] = mapped_column(JSON, default=list)
    query_text: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ConflictCase(Base, TimestampMixin):
    __tablename__ = "conflict_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("canonical_entities.id"), nullable=True)
    property_name: Mapped[str] = mapped_column(String(200))
    conflicting_values: Mapped[list] = mapped_column(JSON, default=list)
    source_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    strategy: Mapped[str] = mapped_column(String(64))
    decision: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="resolved")


class AutoDecisionRecord(Base, TimestampMixin):
    __tablename__ = "auto_decision_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("governance_policies.id"))
    decision_type: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str] = mapped_column(String(64))
    object_ids: Mapped[list] = mapped_column(JSON, default=list)
    decision: Mapped[dict] = mapped_column(JSON, default=dict)
    reversible: Mapped[bool] = mapped_column(Boolean, default=True)


class CurationBatch(Base, TimestampMixin):
    """One business operation that can contain several immutable decisions."""

    __tablename__ = "curation_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(300), default="人工治理")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CurationDecision(Base, TimestampMixin):
    """Append-only human decision; automatic Semantica rows remain untouched."""

    __tablename__ = "curation_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("curation_batches.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), nullable=True, index=True)
    version_id: Mapped[str | None] = mapped_column(ForeignKey("document_versions.id"), nullable=True, index=True)
    target_type: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(500), index=True)
    field_path: Mapped[str] = mapped_column(String(200), default="status")
    operation: Mapped[str] = mapped_column(String(32), index=True)
    before_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    after_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    reason_code: Mapped[str] = mapped_column(String(100), default="manual_correction")
    reason_note: Mapped[str] = mapped_column(Text, default="")
    base_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(32), default="version_only")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("curation_decisions.id"), nullable=True)


class CurationOverlay(Base, TimestampMixin):
    """Materialized pointer to the currently effective decision for one field."""

    __tablename__ = "curation_overlays"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "space_id", "target_type", "target_id", "field_path",
            name="uq_curation_overlay_target",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("curation_decisions.id"), unique=True, index=True)
    target_type: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(500), index=True)
    field_path: Mapped[str] = mapped_column(String(200))
    operation: Mapped[str] = mapped_column(String(32))
    effective_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    base_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(32), default="version_only")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)


class CurationCase(Base, TimestampMixin):
    """Actionable governance issue, not an approval workflow."""

    __tablename__ = "curation_cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "fingerprint", name="uq_curation_case_fingerprint"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), nullable=True, index=True)
    version_id: Mapped[str | None] = mapped_column(ForeignKey("document_versions.id"), nullable=True, index=True)
    target_type: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(500), index=True)
    case_type: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(32), default="medium", index=True)
    title: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    handled_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Ontology(Base, TimestampMixin):
    __tablename__ = "ontologies"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_ontology_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_spaces.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    namespace: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="published")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class OntologyTerm(Base, TimestampMixin):
    __tablename__ = "ontology_terms"
    __table_args__ = (UniqueConstraint("ontology_id", "code", name="uq_ontology_term"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ontology_id: Mapped[str] = mapped_column(ForeignKey("ontologies.id"), index=True)
    code: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(500), index=True)
    term_type: Mapped[str] = mapped_column(String(64), default="class")
    parent_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    definition: Mapped[str] = mapped_column(Text, default="")
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class GraphRelease(Base, TimestampMixin):
    __tablename__ = "graph_releases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    release_number: Mapped[int] = mapped_column(Integer)
    graph_name: Mapped[str] = mapped_column(String(200))
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="published")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IndexRelease(Base, TimestampMixin):
    __tablename__ = "index_releases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    release_number: Mapped[int] = mapped_column(Integer)
    opensearch_index: Mapped[str] = mapped_column(String(200))
    qdrant_collection: Mapped[str] = mapped_column(String(200))
    graph_release_id: Mapped[str | None] = mapped_column(ForeignKey("graph_releases.id"), nullable=True)
    model_config_id: Mapped[str] = mapped_column(ForeignKey("model_configs.id"))
    embedding_dimension: Mapped[int] = mapped_column(Integer)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    checksums: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="published")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeRelease(Base, TimestampMixin):
    """Atomic business pointer pairing graph and search projections."""

    __tablename__ = "knowledge_releases"
    __table_args__ = (
        UniqueConstraint("space_id", "release_number", name="uq_knowledge_release_number"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    release_number: Mapped[int] = mapped_column(Integer)
    graph_release_id: Mapped[str] = mapped_column(ForeignKey("graph_releases.id"), index=True)
    index_release_id: Mapped[str] = mapped_column(ForeignKey("index_releases.id"), index=True)
    curation_batch_id: Mapped[str | None] = mapped_column(ForeignKey("curation_batches.id"), nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="published", index=True)
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QueryRun(Base, TimestampMixin):
    __tablename__ = "query_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    query: Mapped[str] = mapped_column(Text)
    space_ids: Mapped[list] = mapped_column(JSON, default=list)
    retrieval_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="succeeded")


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("harness_session_id", name="uq_conversation_harness_session"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    harness_session_id: Mapped[str] = mapped_column(String(100), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="新会话")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class ConversationMessage(Base, TimestampMixin):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_conversation_message_sequence"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    parent_message_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_messages.id"), nullable=True)
    harness_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class RetrievalTrace(Base, TimestampMixin):
    __tablename__ = "retrieval_traces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("conversation_messages.id"), index=True)
    query_run_id: Mapped[str | None] = mapped_column(ForeignKey("query_runs.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    trace: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Citation(Base, TimestampMixin):
    __tablename__ = "citations"
    __table_args__ = (
        UniqueConstraint("message_id", "citation_number", name="uq_message_citation_number"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("conversation_messages.id"), index=True)
    query_run_id: Mapped[str | None] = mapped_column(ForeignKey("query_runs.id"), nullable=True, index=True)
    citation_number: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentEventProjection(Base, TimestampMixin):
    __tablename__ = "agent_event_projections"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_conversation_agent_event_sequence"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_messages.id"), nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentCredential(Base, TimestampMixin):
    __tablename__ = "agent_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    space_ids: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---- Application foundation ---------------------------------------------------------


class Application(Base, TimestampMixin):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_application_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    app_type: Mapped[str] = mapped_column(String(32), default="agent")
    environment: Mapped[str] = mapped_column(String(32), default="development")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(ForeignKey("org_units.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ApplicationCredential(Base, TimestampMixin):
    __tablename__ = "application_credentials"
    __table_args__ = (UniqueConstraint("tenant_id", "client_id", name="uq_application_client_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    client_id: Mapped[str] = mapped_column(String(120), index=True)
    secret_prefix: Mapped[str] = mapped_column(String(24))
    secret_hash: Mapped[str] = mapped_column(String(300))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_from_id: Mapped[str | None] = mapped_column(ForeignKey("application_credentials.id"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class ApplicationGrant(Base, TimestampMixin):
    __tablename__ = "application_grants"
    __table_args__ = (
        UniqueConstraint(
            "application_id", "resource_type", "resource_id", "permission",
            name="uq_application_resource_grant",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    permission: Mapped[str] = mapped_column(String(32), default="invoke")
    effect: Mapped[str] = mapped_column(String(16), default="allow")


class KnowledgeProduct(Base, TimestampMixin):
    __tablename__ = "knowledge_products"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_knowledge_product_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class KnowledgeProductSpace(Base, TimestampMixin):
    __tablename__ = "knowledge_product_spaces"
    __table_args__ = (UniqueConstraint("product_id", "space_id", name="uq_product_space"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("knowledge_products.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class KnowledgeProductRelease(Base, TimestampMixin):
    __tablename__ = "knowledge_product_releases"
    __table_args__ = (UniqueConstraint("product_id", "version", name="uq_product_release_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("knowledge_products.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="published", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeProductReleaseItem(Base, TimestampMixin):
    __tablename__ = "knowledge_product_release_items"
    __table_args__ = (UniqueConstraint("product_release_id", "space_id", name="uq_product_release_space"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_release_id: Mapped[str] = mapped_column(ForeignKey("knowledge_product_releases.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id"), index=True)
    knowledge_release_id: Mapped[str] = mapped_column(ForeignKey("knowledge_releases.id"), index=True)
    checksum: Mapped[str] = mapped_column(String(64))


class KnowledgeProductAlias(Base, TimestampMixin):
    __tablename__ = "knowledge_product_aliases"
    __table_args__ = (UniqueConstraint("product_id", "alias", name="uq_product_alias"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("knowledge_products.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    alias: Mapped[str] = mapped_column(String(32), index=True)
    product_release_id: Mapped[str] = mapped_column(ForeignKey("knowledge_product_releases.id"), index=True)
    moved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class KnowledgeProductAliasHistory(Base, TimestampMixin):
    __tablename__ = "knowledge_product_alias_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    alias_id: Mapped[str] = mapped_column(ForeignKey("knowledge_product_aliases.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    from_release_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_product_releases.id"), nullable=True)
    to_release_id: Mapped[str] = mapped_column(ForeignKey("knowledge_product_releases.id"))
    moved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(500), default="")


class ApplicationScenario(Base, TimestampMixin):
    __tablename__ = "application_scenarios"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_application_scenario_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    scenario_type: Mapped[str] = mapped_column(String(32), default="chat")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ApplicationScenarioVersion(Base, TimestampMixin):
    __tablename__ = "application_scenario_versions"
    __table_args__ = (UniqueConstraint("scenario_id", "version", name="uq_application_scenario_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("application_scenarios.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[str] = mapped_column(ForeignKey("knowledge_products.id"), index=True)
    product_alias: Mapped[str] = mapped_column(String(32), default="production")
    model_config_id: Mapped[str | None] = mapped_column(ForeignKey("model_configs.id"), nullable=True)
    tool_whitelist: Mapped[list] = mapped_column(JSON, default=list)
    retrieval_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    system_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    response_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    citation_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    fallback_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    analysis_rule_set_ids: Mapped[list] = mapped_column(JSON, default=list)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="published", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class EvaluationDataset(Base, TimestampMixin):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_evaluation_dataset_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class EvaluationCase(Base, TimestampMixin):
    __tablename__ = "evaluation_cases"
    __table_args__ = (UniqueConstraint("dataset_id", "case_key", name="uq_evaluation_case_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("evaluation_datasets.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_key: Mapped[str] = mapped_column(String(100))
    question: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str] = mapped_column(Text, default="")
    expected_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    expected_facts: Mapped[list] = mapped_column(JSON, default=list)
    expected_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class EvaluationRun(Base, TimestampMixin):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("evaluation_datasets.id"), index=True)
    scenario_version_id: Mapped[str] = mapped_column(ForeignKey("application_scenario_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    gate_config: Mapped[dict] = mapped_column(JSON, default=dict)
    gate_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class EvaluationCaseResult(Base, TimestampMixin):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id", name="uq_evaluation_run_case"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("evaluation_cases.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="succeeded")
    answer: Mapped[str] = mapped_column(Text, default="")
    retrieved_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    trace: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApplicationFeedback(Base, TimestampMixin):
    __tablename__ = "application_feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("application_scenarios.id"), nullable=True, index=True)
    invocation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_messages.id"), nullable=True)
    query_run_id: Mapped[str | None] = mapped_column(ForeignKey("query_runs.id"), nullable=True)
    product_release_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_product_releases.id"), nullable=True)
    feedback_type: Mapped[str] = mapped_column(String(64), index=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    submitted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    curation_case_id: Mapped[str | None] = mapped_column(ForeignKey("curation_cases.id"), nullable=True, index=True)


class ApplicationInvocation(Base, TimestampMixin):
    __tablename__ = "application_invocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("application_credentials.id"), nullable=True)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("application_scenarios.id"), nullable=True, index=True)
    scenario_version_id: Mapped[str | None] = mapped_column(ForeignKey("application_scenario_versions.id"), nullable=True)
    product_release_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_product_releases.id"), nullable=True)
    operation: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="succeeded", index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    output_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---- Miaobi writing execution domain -----------------------------------------------


class ScenarioPackage(Base, TimestampMixin):
    __tablename__ = "scenario_packages"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_scenario_package_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    disaster_type: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ScenarioPackageVersion(Base, TimestampMixin):
    __tablename__ = "scenario_package_versions"
    __table_args__ = (
        UniqueConstraint("scenario_package_id", "version", name="uq_scenario_package_version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    scenario_package_id: Mapped[str] = mapped_column(ForeignKey("scenario_packages.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    ontology_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    rule_set_ids: Mapped[list] = mapped_column(JSON, default=list)
    formula_ids: Mapped[list] = mapped_column(JSON, default=list)
    tool_ids: Mapped[list] = mapped_column(JSON, default=list)
    chapter_template: Mapped[dict] = mapped_column(JSON, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    review_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_gates: Mapped[list] = mapped_column(JSON, default=list)
    comparison_dimensions: Mapped[list] = mapped_column(JSON, default=list)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class WritingProject(Base, TimestampMixin):
    __tablename__ = "writing_projects"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_writing_project_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id"), nullable=True, index=True)
    scenario_package_version_id: Mapped[str] = mapped_column(
        ForeignKey("scenario_package_versions.id"), index=True
    )
    knowledge_product_release_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_product_releases.id"), index=True
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class WritingProjectMember(Base, TimestampMixin):
    __tablename__ = "writing_project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_writing_project_member"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="editor")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class WritingDocument(Base, TimestampMixin):
    __tablename__ = "writing_documents"
    __table_args__ = (UniqueConstraint("project_id", "title", name="uq_writing_document_title"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    document_type: Mapped[str] = mapped_column(String(64), default="response_plan")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class WritingDocumentVersion(Base, TimestampMixin):
    __tablename__ = "writing_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_writing_document_version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("writing_documents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[list] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    scenario_package_version_id: Mapped[str] = mapped_column(
        ForeignKey("scenario_package_versions.id"), index=True
    )
    knowledge_product_release_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_product_releases.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="immutable", index=True)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WritingBlockBinding(Base, TimestampMixin):
    __tablename__ = "writing_block_bindings"
    __table_args__ = (
        UniqueConstraint("document_id", "block_id", name="uq_writing_document_block"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("writing_documents.id"), index=True)
    block_id: Mapped[str] = mapped_column(String(100), index=True)
    block_type: Mapped[str] = mapped_column(String(64), index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    source_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    knowledge_product_release_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_product_releases.id"), nullable=True, index=True
    )
    chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True, index=True)
    fact_id: Mapped[str | None] = mapped_column(
        ForeignKey("writing_project_facts.id"), nullable=True, index=True
    )
    inferred_fact_id: Mapped[str | None] = mapped_column(
        ForeignKey("inferred_facts.id"), nullable=True, index=True
    )
    query_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("structured_query_runs.id"), nullable=True, index=True
    )
    retrieval_query_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("query_runs.id"), nullable=True, index=True
    )
    computation_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("computation_runs.id"), nullable=True, index=True
    )
    tool_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    data_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified", index=True)
    freshness_status: Mapped[str] = mapped_column(String(32), default="current", index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ProjectFact(Base, TimestampMixin):
    __tablename__ = "writing_project_facts"
    __table_args__ = (
        UniqueConstraint("project_id", "fact_key", "version", name="uq_writing_project_fact_version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    fact_key: Mapped[str] = mapped_column(String(160), index=True)
    label: Mapped[str] = mapped_column(String(300))
    fact_type: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    source_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_locator: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified", index=True)
    freshness_status: Mapped[str] = mapped_column(String(32), default="current", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FactConflict(Base, TimestampMixin):
    __tablename__ = "writing_fact_conflicts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    fact_key: Mapped[str] = mapped_column(String(160), index=True)
    candidate_fact_ids: Mapped[list] = mapped_column(JSON, default=list)
    conflict_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    resolution: Mapped[dict] = mapped_column(JSON, default=dict)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WritingReasoningRun(Base, TimestampMixin):
    __tablename__ = "writing_reasoning_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    mode: Mapped[str] = mapped_column(String(32), default="preview", index=True)
    engine: Mapped[str] = mapped_column(String(100), default="semantica-datalog")
    engine_version: Mapped[str] = mapped_column(String(100), default="")
    input_fact_ids: Mapped[list] = mapped_column(JSON, default=list)
    rule_manifest: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    proof: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class ComputationDefinition(Base, TimestampMixin):
    __tablename__ = "computation_definitions"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_computation_definition_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ComputationDefinitionVersion(Base, TimestampMixin):
    __tablename__ = "computation_definition_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_computation_definition_version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    definition_id: Mapped[str] = mapped_column(ForeignKey("computation_definitions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(64))
    expression: Mapped[str] = mapped_column(String(1000))
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rounding: Mapped[dict] = mapped_column(JSON, default=dict)
    default_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    tests: Mapped[list] = mapped_column(JSON, default=list)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class ComputationRun(Base, TimestampMixin):
    __tablename__ = "computation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    definition_version_id: Mapped[str] = mapped_column(
        ForeignKey("computation_definition_versions.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="succeeded", index=True)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    input_fact_ids: Mapped[list] = mapped_column(JSON, default=list)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class DecisionGate(Base, TimestampMixin):
    __tablename__ = "writing_decision_gates"
    __table_args__ = (UniqueConstraint("project_id", "gate_key", name="uq_writing_decision_gate"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    gate_key: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    current_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class DecisionRecord(Base, TimestampMixin):
    __tablename__ = "writing_decision_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    gate_id: Mapped[str] = mapped_column(ForeignKey("writing_decision_gates.id"), index=True)
    decision: Mapped[str] = mapped_column(String(64))
    original_value: Mapped[dict] = mapped_column(JSON, default=dict)
    new_value: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class AlternativePlan(Base, TimestampMixin):
    __tablename__ = "writing_alternative_plans"
    __table_args__ = (UniqueConstraint("project_id", "plan_key", "version", name="uq_writing_plan_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    plan_key: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    objective: Mapped[str] = mapped_column(String(64), index=True)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    constraints: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    algorithm: Mapped[dict] = mapped_column(JSON, default=dict)
    unresolved_gaps: Mapped[list] = mapped_column(JSON, default=list)
    risks: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    selected_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewIssue(Base, TimestampMixin):
    __tablename__ = "writing_review_issues"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("writing_documents.id"), index=True)
    block_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    issue_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(32), default="warning", index=True)
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WritingAgentSession(Base, TimestampMixin):
    __tablename__ = "writing_agent_sessions"
    __table_args__ = (UniqueConstraint("project_id", "harness_session_id", name="uq_writing_harness_session"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("writing_documents.id"), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    harness_session_id: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)


class WritingEventProjection(Base, TimestampMixin):
    __tablename__ = "writing_event_projections"
    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_writing_event_sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("writing_agent_sessions.id"), index=True)
    sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ExportTemplate(Base, TimestampMixin):
    __tablename__ = "writing_export_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_writing_export_template_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ExportTemplateVersion(Base, TimestampMixin):
    __tablename__ = "writing_export_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_writing_export_template_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("writing_export_templates.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    format_config: Mapped[dict] = mapped_column(JSON, default=dict)
    object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class ExportJob(Base, TimestampMixin):
    __tablename__ = "writing_export_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("writing_documents.id"), index=True)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("writing_document_versions.id"), index=True)
    template_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("writing_export_template_versions.id"), nullable=True, index=True
    )
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    output_format: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
