from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import (
    ChunkPolicy,
    ExtractionPolicy,
    GovernancePolicy,
    MediaParsingPolicy,
    ModelConfig,
    Ontology,
    OntologyTerm,
    OrgUnit,
    ParserPolicy,
    Role,
    Tenant,
    User,
)
from .security import encrypt_secret, hash_password
from .media import DEFAULT_MEDIA_POLICY
from .media_policy import ensure_policy_version


DEFAULT_PERMISSIONS = {
    "platform_admin": ["*"],
    "space_admin": ["space.manage", "document.*", "source.*", "job.read"],
    "editor": ["document.create", "document.update", "document.read", "source.read"],
    "reader": ["document.read", "search", "answer"],
}


def bootstrap(db: Session) -> None:
    settings = get_settings()
    tenant = db.scalar(select(Tenant).where(Tenant.code == "gl_group"))
    if tenant is None:
        tenant = Tenant(code="gl_group", name="国联集团")
        db.add(tenant)
        db.flush()

    root = db.scalar(
        select(OrgUnit).where(OrgUnit.tenant_id == tenant.id, OrgUnit.code == "root")
    )
    if root is None:
        root = OrgUnit(
            tenant_id=tenant.id,
            code="root",
            name="国联集团",
            unit_type="group",
        )
        db.add(root)
        db.flush()

    admin = db.scalar(select(User).where(User.username == settings.bootstrap_admin_username))
    if admin is None:
        admin = User(
            tenant_id=tenant.id,
            org_unit_id=root.id,
            username=settings.bootstrap_admin_username,
            password_hash=hash_password(settings.bootstrap_admin_password),
            display_name="系统管理员",
            is_admin=True,
        )
        db.add(admin)

    for code, permissions in DEFAULT_PERMISSIONS.items():
        if db.scalar(select(Role).where(Role.tenant_id == tenant.id, Role.code == code)) is None:
            db.add(
                Role(
                    tenant_id=tenant.id,
                    code=code,
                    name={
                        "platform_admin": "平台管理员",
                        "space_admin": "空间管理员",
                        "editor": "知识编辑",
                        "reader": "知识读者",
                    }[code],
                    permissions=permissions,
                    builtin=True,
                )
            )

    if db.scalar(select(ParserPolicy).where(ParserPolicy.tenant_id == tenant.id)) is None:
        db.add(
            ParserPolicy(
                tenant_id=tenant.id,
                name="默认解析策略",
                parser_type="auto",
                enable_ocr=True,
                ocr_language="chi_sim+eng",
                extract_tables=True,
                is_default=True,
            )
        )

    media_policy = db.scalar(
        select(MediaParsingPolicy).where(
            MediaParsingPolicy.tenant_id == tenant.id,
            MediaParsingPolicy.is_default.is_(True),
            MediaParsingPolicy.deleted_at.is_(None),
        )
    )
    if media_policy is None:
        media_policy = MediaParsingPolicy(
            tenant_id=tenant.id,
            name="默认多模态解析策略",
            description="本地媒体预处理、音频转写与可控云端视觉理解",
            applicable_media_types=["image", "audio", "video"],
            config=DEFAULT_MEDIA_POLICY,
            enabled=True,
            is_default=True,
        )
        db.add(media_policy)
        db.flush()
    kimi_model = db.scalar(
        select(ModelConfig).where(
            ModelConfig.tenant_id == tenant.id,
            ModelConfig.provider == "kimi",
            ModelConfig.model_name == "kimi-k3",
        )
    )
    if kimi_model is None:
        api_key = None
        if settings.kimi_api_key_file.exists():
            api_key = settings.kimi_api_key_file.read_text(encoding="utf-8").strip()
        kimi_model = ModelConfig(
                tenant_id=tenant.id,
                name="Kimi K3",
                model_kind="llm",
                provider="kimi",
                model_name="kimi-k3",
                base_url="https://api.moonshot.cn/v1",
                api_key_encrypted=encrypt_secret(api_key),
                config={"temperature": 1.0},
                enabled=True,
                is_default=True,
                last_test_status="untested",
            )
        db.add(kimi_model)
        db.flush()

    vision_model = db.scalar(
        select(ModelConfig).where(
            ModelConfig.tenant_id == tenant.id,
            ModelConfig.model_kind == "vision",
            ModelConfig.is_default.is_(True),
            ModelConfig.deleted_at.is_(None),
        )
    )
    if vision_model is None:
        vision_model = ModelConfig(
            tenant_id=tenant.id,
            name="Kimi K3 视觉理解",
            model_kind="vision",
            provider="kimi",
            model_name="kimi-k3",
            base_url=kimi_model.base_url,
            config={
                "credential_model_config_id": kimi_model.id,
                "timeout": 120,
                "max_tokens": 2048,
            },
            enabled=True,
            is_default=True,
            last_test_status="untested",
        )
        db.add(vision_model); db.flush()

    asr_model = db.scalar(
        select(ModelConfig).where(
            ModelConfig.tenant_id == tenant.id,
            ModelConfig.model_kind == "asr",
            ModelConfig.is_default.is_(True),
            ModelConfig.deleted_at.is_(None),
        )
    )
    if asr_model is None:
        asr_model = ModelConfig(
            tenant_id=tenant.id,
            name="本地 SenseVoiceSmall",
            model_kind="asr",
            provider="openai_compatible",
            model_name="sensevoice",
            base_url="http://asr-runtime:8001/v1",
            config={"local_runtime": True, "timeout": 600, "language": "zh"},
            enabled=True,
            is_default=True,
            last_test_status="untested",
        )
        db.add(asr_model); db.flush()

    media_config = {**(media_policy.config or {})}
    media_config["asr"] = {**(media_config.get("asr") or {}), "model_config_id": asr_model.id}
    media_config["vision"] = {**(media_config.get("vision") or {}), "model_config_id": vision_model.id}
    current_version = ensure_policy_version(db, media_policy, actor_id=admin.id if admin.id else None)
    from .media import media_policy_snapshot
    desired_snapshot = media_policy_snapshot(
        policy_id=media_policy.id,
        policy_version_id=None,
        policy_name=media_policy.name,
        version_number=media_policy.current_version + 1,
        applicable_media_types=media_policy.applicable_media_types or [],
        config=media_config,
    )
    if current_version.config_hash != desired_snapshot["config_hash"]:
        media_policy.config = media_config
        media_policy.current_version += 1
        ensure_policy_version(db, media_policy, actor_id=admin.id if admin.id else None)

    if db.scalar(select(ModelConfig).where(ModelConfig.tenant_id == tenant.id, ModelConfig.model_kind == "embedding")) is None:
        db.add(
            ModelConfig(
                tenant_id=tenant.id,
                name="BGE 中文向量",
                model_kind="embedding",
                provider="fastembed",
                model_name="BAAI/bge-small-zh-v1.5",
                config={"method": "fastembed", "normalize": True},
                enabled=True,
                is_default=True,
                last_test_status="untested",
            )
        )

    if db.scalar(select(ChunkPolicy).where(ChunkPolicy.tenant_id == tenant.id)) is None:
        db.add(
            ChunkPolicy(
                tenant_id=tenant.id,
                name="默认中文切片策略",
                method="recursive",
                chunk_size=800,
                chunk_overlap=120,
                config={"unicode_form": "NFKC"},
                is_default=True,
            )
        )

    if db.scalar(select(ExtractionPolicy).where(ExtractionPolicy.tenant_id == tenant.id)) is None:
        db.add(
            ExtractionPolicy(
                tenant_id=tenant.id,
                name="默认中文语义抽取",
                model_config_id=kimi_model.id,
                min_confidence=0.65,
                max_chunks=30,
                entity_types=["组织", "人物", "产品", "地点", "时间", "指标", "制度"],
                relation_types=[],
                config={"temperature": 1.0},
                is_default=True,
            )
        )

    if db.scalar(select(GovernancePolicy).where(GovernancePolicy.tenant_id == tenant.id)) is None:
        db.add(
            GovernancePolicy(
                tenant_id=tenant.id,
                name="默认自动治理策略",
                similarity_threshold=0.86,
                publish_confidence=0.72,
                conflict_strategy="highest_confidence",
                config={"single_value_predicates": ["负责人", "发布日期", "技术运营单位"]},
                is_default=True,
            )
        )

    ontology = db.scalar(select(Ontology).where(Ontology.tenant_id == tenant.id, Ontology.code == "group-core"))
    if ontology is None:
        ontology = Ontology(
            tenant_id=tenant.id,
            code="group-core",
            name="集团核心词表",
            namespace="urn:guolian:knowledge:core",
            description="组织级知识图谱基础词表",
        )
        db.add(ontology)
        db.flush()
        for code, label, term_type in [
            ("organization", "组织", "class"),
            ("person", "人物", "class"),
            ("product", "产品", "class"),
            ("metric", "指标", "class"),
            ("policy", "制度", "class"),
            ("related", "相关", "relation"),
        ]:
            db.add(OntologyTerm(ontology_id=ontology.id, code=code, label=label, term_type=term_type))

    db.commit()
