from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.application_foundation import _credential_view
from apps.api.application_quality import update_application_feedback
from apps.api.application_schemas import FeedbackUpdate
from apps.api.routes import router as platform_router
from packages.platform.application_services import application_delivery_readiness
from packages.platform.database import Base, get_db
from packages.platform.models import (
    Application,
    ApplicationCredential,
    ApplicationFeedback,
    ApplicationGrant,
    ApplicationScenario,
    ApplicationScenarioVersion,
    EvaluationRun,
    Job,
    KnowledgeProduct,
    KnowledgeProductAlias,
    KnowledgeProductRelease,
    KnowledgeProductReleaseItem,
    KnowledgeSpace,
    Role,
    SpaceGrant,
    Tenant,
    User,
    UserRole,
)
from packages.platform.security import create_access_token, hash_password


ROOT = Path(__file__).resolve().parents[2]


def _memory_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


@contextmanager
def _jobs_client():
    db = _memory_session()
    tenant = Tenant(code="management-safety", name="管理模块安全测试")
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        username="management-admin",
        password_hash=hash_password("Management@123"),
        display_name="管理模块管理员",
        is_admin=True,
        enabled=True,
    )
    reader = User(
        tenant_id=tenant.id,
        username="job-reader",
        password_hash=hash_password("Management@123"),
        display_name="任务查看员",
        enabled=True,
    )
    no_permission = User(
        tenant_id=tenant.id,
        username="no-job-permission",
        password_hash=hash_password("Management@123"),
        display_name="普通用户",
        enabled=True,
    )
    db.add_all([admin, reader, no_permission])
    db.flush()
    role = Role(
        tenant_id=tenant.id,
        code="job-reader",
        name="任务查看员",
        permissions=["job.read"],
        enabled=True,
    )
    db.add(role)
    db.flush()
    db.add(UserRole(user_id=reader.id, role_id=role.id))
    visible_space = KnowledgeSpace(
        tenant_id=tenant.id,
        code="visible-space",
        name="可见空间",
        owner_id=admin.id,
    )
    hidden_space = KnowledgeSpace(
        tenant_id=tenant.id,
        code="hidden-space",
        name="不可见空间",
        owner_id=admin.id,
    )
    db.add_all([visible_space, hidden_space])
    db.flush()
    db.add(SpaceGrant(
        tenant_id=tenant.id,
        space_id=visible_space.id,
        subject_type="user",
        subject_id=reader.id,
        permission="read",
        effect="allow",
    ))
    visible_job = Job(
        tenant_id=tenant.id,
        job_type="demo",
        status="succeeded",
        progress=100,
        idempotency_key="management-visible-job",
        input={"space_id": visible_space.id, "business_marker": "visible"},
    )
    hidden_job = Job(
        tenant_id=tenant.id,
        job_type="demo",
        status="failed",
        progress=20,
        idempotency_key="management-hidden-job",
        input={"space_id": hidden_space.id, "business_marker": "hidden"},
        error_message="隐藏空间任务错误",
    )
    unscoped_job = Job(
        tenant_id=tenant.id,
        job_type="platform-maintenance",
        status="failed",
        progress=10,
        idempotency_key="management-unscoped-job",
        input={"internal_marker": "admin-only"},
    )
    db.add_all([visible_job, hidden_job, unscoped_job])
    db.commit()

    app = FastAPI()
    app.include_router(platform_router, prefix="/api/v1")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    tokens = {
        "admin": create_access_token(admin.id, tenant.id, True),
        "reader": create_access_token(reader.id, tenant.id, False),
        "no_permission": create_access_token(no_permission.id, tenant.id, False),
    }
    with TestClient(app) as client:
        yield client, tokens, visible_job, hidden_job, unscoped_job
    db.close()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_space_creation_returns_a_business_conflict_for_duplicate_codes() -> None:
    with _jobs_client() as (client, tokens, *_):
        response = client.post(
            "/api/v1/spaces",
            headers=_auth(tokens["admin"]),
            json={"code": "visible-space", "name": "重复编码空间"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "知识空间编码已存在，请更换编码"


def test_jobs_require_platform_permission_and_enforce_space_boundary() -> None:
    with _jobs_client() as (client, tokens, visible, hidden, unscoped):
        denied = client.get("/api/v1/jobs", headers=_auth(tokens["no_permission"]))
        assert denied.status_code == 403

        listed = client.get("/api/v1/jobs", headers=_auth(tokens["reader"]))
        assert listed.status_code == 200, listed.text
        assert [row["id"] for row in listed.json()] == [visible.id]
        assert client.get(
            f"/api/v1/jobs/{hidden.id}", headers=_auth(tokens["reader"])
        ).status_code == 404
        assert client.get(
            f"/api/v1/jobs/{unscoped.id}", headers=_auth(tokens["reader"])
        ).status_code == 404

        admin_rows = client.get("/api/v1/jobs", headers=_auth(tokens["admin"]))
        assert admin_rows.status_code == 200
        assert {row["id"] for row in admin_rows.json()} == {
            visible.id,
            hidden.id,
            unscoped.id,
        }


def _release_product(
    db: Session,
    *,
    tenant_id: str,
    owner_id: str,
    space_id: str,
    code: str,
) -> KnowledgeProduct:
    product = KnowledgeProduct(
        tenant_id=tenant_id,
        owner_id=owner_id,
        code=code,
        name=code,
        status="active",
        enabled=True,
    )
    db.add(product)
    db.flush()
    release = KnowledgeProductRelease(
        product_id=product.id,
        tenant_id=tenant_id,
        version=1,
        manifest={},
        checksum=(code[0] * 64),
        status="published",
        created_by=owner_id,
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()
    db.add_all([
        KnowledgeProductReleaseItem(
            product_release_id=release.id,
            tenant_id=tenant_id,
            space_id=space_id,
            knowledge_release_id=f"knowledge-{code}",
            checksum=(code[-1] * 64),
        ),
        KnowledgeProductAlias(
            product_id=product.id,
            tenant_id=tenant_id,
            alias="production",
            product_release_id=release.id,
            moved_by=owner_id,
        ),
    ])
    return product


def test_application_readiness_correlates_scenario_product_test_and_credential() -> None:
    db = _memory_session()
    tenant = Tenant(code="readiness", name="应用准备度测试")
    db.add(tenant)
    db.flush()
    owner = User(
        tenant_id=tenant.id,
        username="readiness-owner",
        password_hash=hash_password("Readiness@123"),
        display_name="应用负责人",
        is_admin=True,
        enabled=True,
    )
    db.add(owner)
    db.flush()
    space = KnowledgeSpace(
        tenant_id=tenant.id,
        code="readiness-space",
        name="应用空间",
        owner_id=owner.id,
    )
    db.add(space)
    db.flush()
    granted_product = _release_product(
        db,
        tenant_id=tenant.id,
        owner_id=owner.id,
        space_id=space.id,
        code="alpha",
    )
    scenario_product = _release_product(
        db,
        tenant_id=tenant.id,
        owner_id=owner.id,
        space_id=space.id,
        code="beta",
    )
    scenario = ApplicationScenario(
        tenant_id=tenant.id,
        code="risk-search",
        name="风险检索",
        scenario_type="search",
        owner_id=owner.id,
        status="active",
        enabled=True,
    )
    db.add(scenario)
    db.flush()
    version = ApplicationScenarioVersion(
        scenario_id=scenario.id,
        tenant_id=tenant.id,
        version=1,
        product_id=scenario_product.id,
        product_alias="production",
        tool_whitelist=["knowledge_search"],
        retrieval_policy={"use_keyword": True, "use_vector": True, "use_graph": True},
        system_policy={},
        response_schema={},
        citation_policy={"required": True},
        fallback_policy={},
        analysis_rule_set_ids=[],
        checksum="c" * 64,
        status="published",
        created_by=owner.id,
    )
    db.add(version)
    db.flush()
    scenario.current_version_id = version.id
    application = Application(
        tenant_id=tenant.id,
        code="risk-app",
        name="风险应用",
        app_type="agent",
        environment="testing",
        owner_id=owner.id,
        status="active",
        enabled=True,
    )
    db.add(application)
    db.flush()
    db.add_all([
        ApplicationGrant(
            application_id=application.id,
            tenant_id=tenant.id,
            resource_type="knowledge_product",
            resource_id=granted_product.id,
            permission="read",
            effect="allow",
        ),
        ApplicationGrant(
            application_id=application.id,
            tenant_id=tenant.id,
            resource_type="scenario",
            resource_id=scenario.id,
            permission="invoke",
            effect="allow",
        ),
        ApplicationCredential(
            application_id=application.id,
            tenant_id=tenant.id,
            name="演示接入",
            client_id="csa_readiness_test",
            secret_prefix="css_ready",
            secret_hash="not-used-in-readiness",
            scopes=["scenario.invoke"],
        ),
        EvaluationRun(
            tenant_id=tenant.id,
            dataset_id="dataset-readiness",
            scenario_version_id=version.id,
            status="succeeded",
            progress=100,
            metrics={"recall_at_k": 1.0},
            gate_config={"recall_at_k": 0.8},
            gate_passed=True,
            created_by=owner.id,
        ),
    ])
    db.commit()

    mismatched = application_delivery_readiness(db, application)
    assert mismatched["supply_ready"] is True
    assert mismatched["scenario_ready"] is False
    assert mismatched["test_ready"] is False
    assert mismatched["ready"] is False
    assert mismatched["progress"] == 25

    db.add(ApplicationGrant(
        application_id=application.id,
        tenant_id=tenant.id,
        resource_type="knowledge_product",
        resource_id=scenario_product.id,
        permission="read",
        effect="allow",
    ))
    db.commit()
    correlated = application_delivery_readiness(db, application)
    assert correlated["eligible_scenario_ids"] == [scenario.id]
    assert correlated["test_ready"] is True
    assert correlated["access_ready"] is True
    assert correlated["progress"] == 100
    assert correlated["ready"] is True
    db.close()


def test_expired_application_credential_is_not_reported_active() -> None:
    row = ApplicationCredential(
        application_id="application",
        tenant_id="tenant",
        name="已过期凭据",
        client_id="csa_expired_credential",
        secret_prefix="css_expired",
        secret_hash="redacted",
        scopes=["scenario.invoke"],
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert _credential_view(row)["status"] == "expired"


def test_knowledge_quality_feedback_cannot_bypass_governance_verification() -> None:
    db = _memory_session()
    tenant = Tenant(code="feedback", name="反馈闭环测试")
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        username="feedback-admin",
        password_hash=hash_password("Feedback@123"),
        display_name="反馈管理员",
        is_admin=True,
        enabled=True,
    )
    db.add(admin)
    db.flush()
    application = Application(
        tenant_id=tenant.id,
        code="feedback-app",
        name="反馈应用",
        owner_id=admin.id,
        status="active",
        enabled=True,
    )
    db.add(application)
    db.flush()
    quality_issue = ApplicationFeedback(
        tenant_id=tenant.id,
        application_id=application.id,
        feedback_type="bad_citation",
        comment="引用与原文不一致",
        evidence={},
        status="open",
    )
    suggestion = ApplicationFeedback(
        tenant_id=tenant.id,
        application_id=application.id,
        feedback_type="suggestion",
        comment="建议调整页面排序",
        evidence={},
        status="open",
    )
    db.add_all([quality_issue, suggestion])
    db.commit()

    with pytest.raises(HTTPException) as blocked:
        update_application_feedback(
            quality_issue.id,
            FeedbackUpdate(status="resolved"),
            admin,
            db,
        )
    assert blocked.value.status_code == 409
    assert db.get(ApplicationFeedback, quality_issue.id).status == "open"

    resolved = update_application_feedback(
        suggestion.id,
        FeedbackUpdate(status="resolved"),
        admin,
        db,
    )
    assert resolved["status"] == "resolved"
    db.close()


def test_management_frontend_uses_authoritative_readiness_and_permission_gates() -> None:
    app = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    for phrase in (
        "permission:'job.read'",
        "/applications/${selected.id}/readiness",
        "readinessSnapshot?.ready",
        "知识质量反馈必须完成治理",
    ):
        if phrase == "知识质量反馈必须完成治理":
            source = (ROOT / "apps/api/application_quality.py").read_text(encoding="utf-8")
        else:
            source = app
        assert phrase in source
