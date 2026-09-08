from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.platform.database import Base
from packages.platform.models import (
    ChunkPolicy,
    ExtractionPolicy,
    GovernancePolicy,
    ModelConfig,
    ModelRoutingPolicy,
    ParserPolicy,
    Tenant,
)
from packages.platform.processing_readiness import build_processing_readiness


ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
ROUTES = (ROOT / "apps/api/routes.py").read_text(encoding="utf-8")


def _model(
    tenant_id: str,
    name: str,
    kind: str,
    *,
    default: bool = False,
    test_status: str = "success",
) -> ModelConfig:
    return ModelConfig(
        tenant_id=tenant_id,
        name=name,
        model_kind=kind,
        provider="openai_compatible",
        model_name=name.lower(),
        enabled=True,
        is_default=default,
        last_test_status=test_status,
    )


def _configured_db() -> tuple[Session, dict[str, object]]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(code="preflight", name="加工预检")
    db.add(tenant); db.flush()
    llm = _model(tenant.id, "可用大模型", "llm", default=True)
    failed_llm = _model(tenant.id, "失效大模型", "llm", test_status="failed")
    embedding = _model(tenant.id, "中文向量", "embedding", default=True)
    db.add_all([llm, failed_llm, embedding]); db.flush()
    parser = ParserPolicy(
        tenant_id=tenant.id,
        name="默认解析",
        parser_type="auto",
        enable_ocr=True,
        enabled=True,
        is_default=True,
    )
    chunk = ChunkPolicy(
        tenant_id=tenant.id,
        name="默认切片",
        enabled=True,
        is_default=True,
    )
    extraction = ExtractionPolicy(
        tenant_id=tenant.id,
        name="默认抽取",
        model_config_id=failed_llm.id,
        enabled=True,
        is_default=True,
    )
    governance = GovernancePolicy(
        tenant_id=tenant.id,
        name="默认治理",
        config={"enable_model_analysis": True},
        enabled=True,
        is_default=True,
    )
    route = ModelRoutingPolicy(
        tenant_id=tenant.id,
        name="默认路由",
        routes={
            "semantic_extract": llm.id,
            "document_governance": llm.id,
            "embedding": embedding.id,
        },
        enabled=True,
        is_default=True,
    )
    db.add_all([parser, chunk, extraction, governance, route]); db.flush()
    return db, {
        "tenant": tenant,
        "llm": llm,
        "failed_llm": failed_llm,
        "embedding": embedding,
        "parser": parser,
        "extraction": extraction,
    }


def test_graph_upload_is_blocked_before_queue_when_explicit_model_failed() -> None:
    db, rows = _configured_db()
    try:
        result = build_processing_readiness(
            db,
            tenant_id=rows["tenant"].id,
            mode="both",
            parser_policy_id=rows["parser"].id,
        )
        assert result["ready"] is False
        assert any(
            issue["code"] == "EXTRACTION_MODEL_UNAVAILABLE"
            for issue in result["blocking_issues"]
        )
        semantic = next(
            item for item in result["components"] if item["stage"] == "semantic_extract"
        )
        assert semantic["status"] == "blocked"
        assert semantic["model_name"] is None
    finally:
        db.close()


def test_vector_only_upload_does_not_require_semantic_extraction_model() -> None:
    db, rows = _configured_db()
    try:
        result = build_processing_readiness(
            db,
            tenant_id=rows["tenant"].id,
            mode="vector",
            parser_policy_id=rows["parser"].id,
        )
        assert result["ready"] is True
        assert not any(
            item["stage"] == "semantic_extract" for item in result["components"]
        )
        embedding = next(
            item for item in result["components"] if item["stage"] == "embedding"
        )
        assert embedding["model_name"] == "中文向量"
    finally:
        db.close()


def test_extraction_policy_can_follow_the_active_model_route() -> None:
    db, rows = _configured_db()
    try:
        rows["extraction"].model_config_id = None
        db.flush()
        result = build_processing_readiness(
            db,
            tenant_id=rows["tenant"].id,
            mode="graph",
            parser_policy_id=rows["parser"].id,
        )
        assert result["ready"] is True
        semantic = next(
            item for item in result["components"] if item["stage"] == "semantic_extract"
        )
        assert semantic["model_name"] == "可用大模型"
        assert semantic["source"] == "routing_policy"
        assert semantic["policy_name"] == "默认抽取"
    finally:
        db.close()


def test_upload_ui_runs_preflight_and_parser_page_shows_model_combination() -> None:
    assert '@router.get("/processing/readiness")' in ROUTES
    assert "readiness = build_processing_readiness" in ROUTES
    assert ROUTES.index("readiness = build_processing_readiness") < ROUTES.index(
        "with tempfile.NamedTemporaryFile"
    )
    assert "upload-processing-readiness" in APP
    assert "loadReadiness" in APP
    assert "parserSelect.onchange=loadReadiness" in APP
    assert "[name=knowledge_processing_mode]" in APP
    assert "showParserCombination" in APP
    assert "配套加工模型" in APP
