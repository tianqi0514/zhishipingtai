from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    ApplicationGrant,
    ApplicationScenarioVersion,
    KnowledgeProductAlias,
    KnowledgeProductRelease,
    KnowledgeProductReleaseItem,
)


class ApplicationConfigurationError(ValueError):
    pass


def application_has_grant(
    db: Session,
    *,
    application_id: str,
    resource_type: str,
    resource_id: str,
    permission: str = "invoke",
) -> bool:
    grants = list(db.scalars(select(ApplicationGrant).where(
        ApplicationGrant.application_id == application_id,
        ApplicationGrant.resource_type == resource_type,
        ApplicationGrant.resource_id == resource_id,
        ApplicationGrant.permission == permission,
        ApplicationGrant.deleted_at.is_(None),
    )))
    if any(row.effect == "deny" for row in grants):
        return False
    return any(row.effect == "allow" for row in grants)


def resolve_scenario_product_release(
    db: Session,
    version: ApplicationScenarioVersion,
) -> tuple[KnowledgeProductRelease, list[str]]:
    alias = db.scalar(select(KnowledgeProductAlias).where(
        KnowledgeProductAlias.product_id == version.product_id,
        KnowledgeProductAlias.alias == version.product_alias,
        KnowledgeProductAlias.deleted_at.is_(None),
    ))
    if alias is None:
        raise ApplicationConfigurationError(f"知识产品未配置 {version.product_alias} 发布别名")
    release = db.get(KnowledgeProductRelease, alias.product_release_id)
    if release is None or release.deleted_at is not None or release.status != "published":
        raise ApplicationConfigurationError("知识产品发布版本不可用")
    space_ids = list(db.scalars(select(KnowledgeProductReleaseItem.space_id).where(
        KnowledgeProductReleaseItem.product_release_id == release.id,
        KnowledgeProductReleaseItem.deleted_at.is_(None),
    ).order_by(KnowledgeProductReleaseItem.created_at)))
    if not space_ids:
        raise ApplicationConfigurationError("知识产品发布版本不包含知识空间")
    return release, space_ids


def retrieval_metrics(
    retrieved_chunk_ids: list[str],
    expected_chunk_ids: list[str],
    *,
    k: int | None = None,
) -> dict[str, float | int | bool | None]:
    limit = max(1, k or len(retrieved_chunk_ids) or 1)
    retrieved = retrieved_chunk_ids[:limit]
    expected = list(dict.fromkeys(expected_chunk_ids))
    expected_set = set(expected)
    hits = [chunk_id for chunk_id in retrieved if chunk_id in expected_set]
    recall = len(set(hits)) / len(expected_set) if expected_set else None
    first_rank = next((index for index, chunk_id in enumerate(retrieved, start=1) if chunk_id in expected_set), None)
    mrr = 1.0 / first_rank if first_rank else 0.0
    dcg = sum(1.0 / math.log2(index + 1) for index, chunk_id in enumerate(retrieved, start=1) if chunk_id in expected_set)
    ideal_hits = min(len(expected_set), limit)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    ndcg = dcg / idcg if idcg else None
    return {
        "k": limit,
        "expected_count": len(expected),
        "retrieved_count": len(retrieved),
        "hit_count": len(set(hits)),
        "recall_at_k": round(recall, 6) if recall is not None else None,
        "mrr": round(mrr, 6),
        "ndcg_at_k": round(ndcg, 6) if ndcg is not None else None,
        "passed": bool(hits) if expected else True,
    }


def aggregate_evaluation_metrics(case_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    def average(name: str) -> float | None:
        values = [float(item[name]) for item in case_metrics if item.get(name) is not None]
        return round(sum(values) / len(values), 6) if values else None

    return {
        "case_count": len(case_metrics),
        "passed_count": sum(bool(item.get("passed")) for item in case_metrics),
        "recall_at_k": average("recall_at_k"),
        "mrr": average("mrr"),
        "ndcg_at_k": average("ndcg_at_k"),
    }


def evaluation_gate_passed(metrics: dict[str, Any], gate_config: dict[str, float]) -> bool:
    for name, threshold in gate_config.items():
        if name not in {"recall_at_k", "mrr", "ndcg_at_k"}:
            raise ValueError(f"不支持的质量门禁指标：{name}")
        value = metrics.get(name)
        if value is None or float(value) < float(threshold):
            return False
    return True

