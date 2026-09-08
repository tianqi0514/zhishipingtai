from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .knowledge_processing import normalize_processing_mode, processing_targets
from .model_routing import resolve_model_for_scene
from .models import (
    ChunkPolicy,
    ExtractionPolicy,
    GovernancePolicy,
    ParserPolicy,
)


PARSER_LABELS = {
    "auto": "自动选择（按文件格式）",
    "native": "内置解析",
    "docling": "版面解析",
}

MODEL_SOURCE_LABELS = {
    "explicit": "抽取策略指定",
    "routing_policy": "模型路由",
    "kind_default": "类型默认",
    "unresolved": "未配置",
}


def _default_policy(db: Session, model: Any, tenant_id: str) -> Any | None:
    return db.scalar(
        select(model).where(
            model.tenant_id == tenant_id,
            model.is_default.is_(True),
            model.enabled.is_(True),
            model.deleted_at.is_(None),
        ).limit(1)
    )


def _model_component(stage: str, label: str, resolved: Any, *, required: bool) -> dict[str, Any]:
    model = resolved.model
    return {
        "stage": stage,
        "label": label,
        "kind": "model",
        "required": required,
        "status": "ready" if model else ("blocked" if required else "not_configured"),
        "model_config_id": model.id if model else None,
        "model_name": model.name if model else None,
        "provider": model.provider if model else None,
        "model_identifier": model.model_name if model else None,
        "test_status": model.last_test_status if model else None,
        "source": resolved.source,
        "source_label": MODEL_SOURCE_LABELS.get(resolved.source, resolved.source),
        "warning": resolved.warning,
    }


def build_processing_readiness(
    db: Session,
    *,
    tenant_id: str,
    mode: str = "both",
    parser_policy_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the exact parser and model chain before an upload is accepted."""
    normalized_mode = normalize_processing_mode(mode)
    targets = processing_targets(normalized_mode)
    blocking_issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    parser_policy = db.get(ParserPolicy, parser_policy_id) if parser_policy_id else None
    if parser_policy_id and (
        parser_policy is None
        or parser_policy.tenant_id != tenant_id
        or parser_policy.deleted_at is not None
    ):
        parser_policy = None
        blocking_issues.append({
            "code": "PARSER_POLICY_NOT_FOUND",
            "message": "所选解析策略不存在，请重新选择。",
            "action_view": "parsers",
        })
    elif parser_policy_id and not parser_policy.enabled:
        blocking_issues.append({
            "code": "PARSER_POLICY_DISABLED",
            "message": "所选解析策略已停用，请更换策略后再上传。",
            "action_view": "parsers",
        })
    if parser_policy is None and not parser_policy_id:
        parser_policy = _default_policy(db, ParserPolicy, tenant_id)

    components: list[dict[str, Any]] = [{
        "stage": "parse",
        "label": "文档解析",
        "kind": "engine",
        "required": True,
        "status": "ready" if not parser_policy_id or parser_policy is not None else "blocked",
        "engine": PARSER_LABELS.get(
            parser_policy.parser_type if parser_policy else "auto",
            parser_policy.parser_type if parser_policy else "平台自动选择",
        ),
        "source_label": "解析策略" if parser_policy else "平台默认",
    }]
    if parser_policy and parser_policy.enable_ocr:
        components.append({
            "stage": "ocr",
            "label": "文字识别",
            "kind": "engine",
            "required": False,
            "status": "ready",
            "engine": f"按内容触发（Docling / Tesseract，{parser_policy.ocr_language}）",
            "source_label": "解析策略",
        })

    chunk_policy = _default_policy(db, ChunkPolicy, tenant_id)
    components.append({
        "stage": "chunk",
        "label": "内容切片",
        "kind": "policy",
        "required": True,
        "status": "ready" if chunk_policy else "blocked",
        "policy_id": chunk_policy.id if chunk_policy else None,
        "policy_name": chunk_policy.name if chunk_policy else None,
        "engine": chunk_policy.method if chunk_policy else None,
        "source_label": "默认加工策略",
    })
    if chunk_policy is None:
        blocking_issues.append({
            "code": "CHUNK_POLICY_MISSING",
            "message": "未配置可用的默认切片策略。",
            "action_view": "processing",
        })

    if "vector" in targets:
        embedding = resolve_model_for_scene(db, tenant_id, "embedding")
        components.append(_model_component("embedding", "向量化", embedding, required=True))
        if embedding.model is None:
            blocking_issues.append({
                "code": "EMBEDDING_MODEL_UNAVAILABLE",
                "message": "向量化模型不可用，请先检查模型服务或模型路由。",
                "action_view": "modelrouting",
            })

    extraction_policy = _default_policy(db, ExtractionPolicy, tenant_id)
    extraction_model = (
        resolve_model_for_scene(
            db,
            tenant_id,
            "semantic_extract",
            explicit_model_id=extraction_policy.model_config_id,
        )
        if extraction_policy
        else None
    )
    if "graph" in targets:
        if extraction_policy is None:
            components.append({
                "stage": "semantic_extract",
                "label": "图谱语义抽取",
                "kind": "policy",
                "required": True,
                "status": "blocked",
                "policy_name": None,
                "model_name": None,
                "source_label": "未配置",
            })
            blocking_issues.append({
                "code": "EXTRACTION_POLICY_MISSING",
                "message": "未配置可用的默认语义抽取策略。",
                "action_view": "processing",
            })
        else:
            component = _model_component(
                "semantic_extract", "图谱语义抽取", extraction_model, required=True
            )
            component.update({
                "policy_id": extraction_policy.id,
                "policy_name": extraction_policy.name,
            })
            components.append(component)
            if extraction_model.model is None:
                blocking_issues.append({
                    "code": "EXTRACTION_MODEL_UNAVAILABLE",
                    "message": "语义抽取使用的大模型不可用，请检查默认抽取策略或模型路由后再上传。",
                    "action_view": "processing",
                })

    governance_policy = _default_policy(db, GovernancePolicy, tenant_id)
    if governance_policy is None:
        components.append({
            "stage": "document_governance",
            "label": "文档治理",
            "kind": "policy",
            "required": True,
            "status": "blocked",
            "policy_name": None,
            "source_label": "未配置",
        })
        blocking_issues.append({
            "code": "GOVERNANCE_POLICY_MISSING",
            "message": "未配置可用的默认治理策略。",
            "action_view": "processing",
        })
    else:
        governance_config = governance_policy.config or {}
        model_enabled = bool(governance_config.get("enable_model_analysis", True))
        governance_model = resolve_model_for_scene(
            db,
            tenant_id,
            "document_governance",
            explicit_model_id=str(governance_config.get("model_config_id") or "") or None,
        )
        if (
            governance_model.model is None
            and not governance_config.get("model_config_id")
            and extraction_model is not None
            and extraction_model.model is not None
        ):
            governance_model = extraction_model
        component = _model_component(
            "document_governance", "文档治理", governance_model, required=False
        )
        component.update({
            "policy_id": governance_policy.id,
            "policy_name": governance_policy.name,
            "status": (
                "disabled" if not model_enabled
                else "ready" if governance_model.model
                else "degraded"
            ),
            "engine": "确定性质量分析" if not model_enabled else None,
        })
        components.append(component)
        if model_enabled and governance_model.model is None:
            warnings.append({
                "code": "GOVERNANCE_MODEL_UNAVAILABLE",
                "message": "治理大模型不可用；文档仍可上传，将保留确定性质量分析结果。",
                "action_view": "modelrouting",
            })

    return {
        "ready": not blocking_issues,
        "mode": normalized_mode,
        "targets": sorted(targets),
        "parser_policy": {
            "id": parser_policy.id if parser_policy else None,
            "name": parser_policy.name if parser_policy else "平台自动解析",
            "parser_type": parser_policy.parser_type if parser_policy else "auto",
            "enabled": parser_policy.enabled if parser_policy else True,
            "is_default": parser_policy.is_default if parser_policy else False,
        },
        "components": components,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
    }
