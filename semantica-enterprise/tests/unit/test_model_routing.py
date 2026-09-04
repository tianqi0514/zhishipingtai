from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.platform.database import Base
from packages.platform.model_routing import (
    MODEL_ROUTE_SCENES,
    ModelRoutingError,
    resolve_model_for_scene,
    validate_model_routes,
)
from packages.platform.models import ModelConfig, ModelRoutingPolicy, Tenant


def _model(tenant_id: str, name: str, kind: str, *, default: bool = False) -> ModelConfig:
    return ModelConfig(
        tenant_id=tenant_id,
        name=name,
        model_kind=kind,
        provider="openai_compatible",
        model_name=name.lower(),
        enabled=True,
        is_default=default,
    )


def test_scene_routing_priority_and_kind_default_fallback() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(code="route", name="路由租户")
        db.add(tenant); db.flush()
        default_llm = _model(tenant.id, "K3", "llm", default=True)
        routed_llm = _model(tenant.id, "Qwen", "llm")
        explicit_llm = _model(tenant.id, "专用抽取", "llm")
        db.add_all([default_llm, routed_llm, explicit_llm]); db.flush()
        db.add(ModelRoutingPolicy(
            tenant_id=tenant.id,
            name="平台路由",
            routes={"agent_chat": routed_llm.id},
            enabled=True,
            is_default=True,
        )); db.flush()

        assert resolve_model_for_scene(db, tenant.id, "agent_chat").model.id == routed_llm.id
        assert resolve_model_for_scene(db, tenant.id, "semantic_extract").model.id == default_llm.id
        assert resolve_model_for_scene(
            db, tenant.id, "semantic_extract", explicit_model_id=explicit_llm.id
        ).model.id == explicit_llm.id


def test_route_validation_rejects_wrong_kind_and_cross_tenant_model() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(code="one", name="一号租户")
        other = Tenant(code="two", name="二号租户")
        db.add_all([tenant, other]); db.flush()
        embedding = _model(tenant.id, "BGE", "embedding")
        foreign_llm = _model(other.id, "其他租户模型", "llm")
        db.add_all([embedding, foreign_llm]); db.flush()
        with pytest.raises(ModelRoutingError, match="只能选择 llm"):
            validate_model_routes(db, tenant.id, {"agent_chat": embedding.id})
        with pytest.raises(ModelRoutingError, match="不存在"):
            validate_model_routes(db, tenant.id, {"agent_chat": foreign_llm.id})
        with pytest.raises(ModelRoutingError, match="不支持的模型场景"):
            validate_model_routes(db, tenant.id, {"unknown": None})
        assert set(MODEL_ROUTE_SCENES) == {
            "agent_chat", "semantic_extract", "document_governance", "structured_query",
            "vision_understanding", "embedding", "reranking", "speech_recognition",
        }


def test_explicit_unavailable_model_does_not_silently_fall_back() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(code="explicit", name="显式配置")
        db.add(tenant); db.flush()
        db.add(_model(tenant.id, "默认模型", "llm", default=True)); db.flush()
        resolved = resolve_model_for_scene(
            db, tenant.id, "semantic_extract", explicit_model_id="missing"
        )
        assert resolved.model is None
        assert resolved.source == "explicit"
        assert resolved.warning
