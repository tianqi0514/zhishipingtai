from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .media import DEFAULT_MEDIA_POLICY, media_policy_snapshot, normalize_media_policy
from .models import (
    KnowledgeSpace,
    MediaParsingPolicy,
    MediaParsingPolicyVersion,
    SourceConnector,
)


def ensure_policy_version(
    db: Session, policy: MediaParsingPolicy, *, actor_id: str | None = None
) -> MediaParsingPolicyVersion:
    version = db.scalar(
        select(MediaParsingPolicyVersion).where(
            MediaParsingPolicyVersion.policy_id == policy.id,
            MediaParsingPolicyVersion.version_number == policy.current_version,
            MediaParsingPolicyVersion.deleted_at.is_(None),
        )
    )
    if version is not None:
        return version
    snapshot = media_policy_snapshot(
        policy_id=policy.id,
        policy_version_id=None,
        policy_name=policy.name,
        version_number=policy.current_version,
        applicable_media_types=policy.applicable_media_types or [],
        config=policy.config or {},
    )
    version = MediaParsingPolicyVersion(
        tenant_id=policy.tenant_id,
        policy_id=policy.id,
        version_number=policy.current_version,
        snapshot=snapshot,
        config_hash=snapshot["config_hash"],
        created_by=actor_id,
    )
    db.add(version)
    db.flush()
    snapshot["policy_version_id"] = version.id
    snapshot["config_hash"] = media_policy_snapshot(
        policy_id=policy.id,
        policy_version_id=version.id,
        policy_name=policy.name,
        version_number=policy.current_version,
        applicable_media_types=policy.applicable_media_types or [],
        config=policy.config or {},
    )["config_hash"]
    version.snapshot = snapshot
    return version


def resolve_media_policy(
    db: Session,
    *,
    tenant_id: str,
    media_type: str,
    explicit_policy_id: str | None = None,
    source_id: str | None = None,
    space_id: str | None = None,
    override: dict[str, Any] | None = None,
    actor_id: str | None = None,
) -> tuple[MediaParsingPolicyVersion | None, dict[str, Any]]:
    policy_id = explicit_policy_id
    if not policy_id and source_id:
        source = db.get(SourceConnector, source_id)
        if source and source.tenant_id == tenant_id and source.deleted_at is None:
            policy_id = source.media_policy_id
    if not policy_id and space_id:
        space = db.get(KnowledgeSpace, space_id)
        if space and space.tenant_id == tenant_id and space.deleted_at is None:
            policy_id = space.media_policy_id
    policy = db.get(MediaParsingPolicy, policy_id) if policy_id else None
    if policy is None:
        policy = db.scalar(
            select(MediaParsingPolicy).where(
                MediaParsingPolicy.tenant_id == tenant_id,
                MediaParsingPolicy.is_default.is_(True),
                MediaParsingPolicy.enabled.is_(True),
                MediaParsingPolicy.deleted_at.is_(None),
            )
        )
    if policy is None:
        # Fresh/legacy databases can still parse media with a durable inline
        # snapshot. Bootstrap will normally create the named default policy.
        config = normalize_media_policy(override or DEFAULT_MEDIA_POLICY)
        return None, media_policy_snapshot(
            policy_id=None,
            policy_version_id=None,
            policy_name="内置默认媒体策略",
            version_number=1,
            applicable_media_types=["image", "audio", "video"],
            config=config,
        )
    if (
        policy.tenant_id != tenant_id
        or policy.deleted_at is not None
        or not policy.enabled
        or media_type not in (policy.applicable_media_types or [])
    ):
        raise ValueError("媒体解析策略不存在、已停用或不适用于该文件")
    version = ensure_policy_version(db, policy, actor_id=actor_id)
    snapshot = media_policy_snapshot(
        policy_id=policy.id,
        policy_version_id=version.id,
        policy_name=policy.name,
        version_number=policy.current_version,
        applicable_media_types=policy.applicable_media_types or [],
        config=policy.config or {},
        override=override,
    )
    return version, snapshot
