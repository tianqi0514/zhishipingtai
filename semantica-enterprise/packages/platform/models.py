from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
