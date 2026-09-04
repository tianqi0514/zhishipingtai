from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ModelConfig, ModelRoutingPolicy


MODEL_ROUTE_SCENES: dict[str, dict[str, str]] = {
    "agent_chat": {"label": "智能问答", "model_kind": "llm"},
    "semantic_extract": {"label": "图谱语义抽取", "model_kind": "llm"},
    "document_governance": {"label": "文档治理画像", "model_kind": "llm"},
    "structured_query": {"label": "结构化查询规划", "model_kind": "llm"},
    "vision_understanding": {"label": "视觉理解", "model_kind": "vision"},
    "embedding": {"label": "向量化", "model_kind": "embedding"},
    "reranking": {"label": "检索重排", "model_kind": "reranker"},
    "speech_recognition": {"label": "语音识别", "model_kind": "asr"},
}


class ModelRoutingError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedModel:
    model: ModelConfig | None
    source: str
    scene: str
    warning: str | None = None


def validate_model_routes(
    db: Session,
    tenant_id: str,
    routes: dict[str, Any] | None,
) -> dict[str, str | None]:
    if routes is None:
        return {}
    if not isinstance(routes, dict):
        raise ModelRoutingError("模型路由必须是对象")
    unknown = sorted(set(routes) - set(MODEL_ROUTE_SCENES))
    if unknown:
        raise ModelRoutingError(f"不支持的模型场景：{', '.join(unknown)}")
    normalized: dict[str, str | None] = {}
    for scene, raw_model_id in routes.items():
        model_id = str(raw_model_id or "").strip() or None
        normalized[scene] = model_id
        if model_id is None:
            continue
        model = db.get(ModelConfig, model_id)
        expected = MODEL_ROUTE_SCENES[scene]["model_kind"]
        if (
            model is None
            or model.tenant_id != tenant_id
            or model.deleted_at is not None
        ):
            raise ModelRoutingError(f"{MODEL_ROUTE_SCENES[scene]['label']}引用的模型不存在")
        if model.model_kind != expected:
            raise ModelRoutingError(
                f"{MODEL_ROUTE_SCENES[scene]['label']}只能选择 {expected} 类型模型"
            )
        if not model.enabled:
            raise ModelRoutingError(f"{MODEL_ROUTE_SCENES[scene]['label']}引用的模型未启用")
    return normalized


def resolve_model_for_scene(
    db: Session,
    tenant_id: str,
    scene: str,
    *,
    explicit_model_id: str | None = None,
) -> ResolvedModel:
    definition = MODEL_ROUTE_SCENES.get(scene)
    if definition is None:
        raise ModelRoutingError(f"不支持的模型场景：{scene}")
    expected = definition["model_kind"]
    if explicit_model_id:
        explicit = db.get(ModelConfig, explicit_model_id)
        if (
            explicit
            and explicit.tenant_id == tenant_id
            and explicit.model_kind == expected
            and explicit.enabled
            and explicit.deleted_at is None
        ):
            return ResolvedModel(explicit, "explicit", scene)
        return ResolvedModel(None, "explicit", scene, "显式配置的模型不可用")

    policy = db.scalar(
        select(ModelRoutingPolicy).where(
            ModelRoutingPolicy.tenant_id == tenant_id,
            ModelRoutingPolicy.enabled.is_(True),
            ModelRoutingPolicy.is_default.is_(True),
            ModelRoutingPolicy.deleted_at.is_(None),
        ).limit(1)
    )
    routed_id = str((policy.routes or {}).get(scene) or "").strip() if policy else ""
    if routed_id:
        routed = db.get(ModelConfig, routed_id)
        if (
            routed
            and routed.tenant_id == tenant_id
            and routed.model_kind == expected
            and routed.enabled
            and routed.deleted_at is None
        ):
            return ResolvedModel(routed, "routing_policy", scene)

    fallback = db.scalar(
        select(ModelConfig).where(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.model_kind == expected,
            ModelConfig.enabled.is_(True),
            ModelConfig.is_default.is_(True),
            ModelConfig.deleted_at.is_(None),
        ).limit(1)
    )
    warning = "模型路由未配置或目标不可用，已使用同类型默认模型" if policy else None
    return ResolvedModel(fallback, "kind_default" if fallback else "unresolved", scene, warning)


def resolved_routes(db: Session, tenant_id: str) -> list[dict[str, Any]]:
    rows = []
    for scene, definition in MODEL_ROUTE_SCENES.items():
        resolved = resolve_model_for_scene(db, tenant_id, scene)
        rows.append({
            "scene": scene,
            "label": definition["label"],
            "model_kind": definition["model_kind"],
            "model_config_id": resolved.model.id if resolved.model else None,
            "model_name": resolved.model.name if resolved.model else None,
            "source": resolved.source,
            "warning": resolved.warning,
        })
    return rows
