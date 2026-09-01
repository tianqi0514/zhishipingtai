from __future__ import annotations

from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.application_foundation import router
from packages.platform.database import Base, get_db
from packages.platform.models import (
    ApplicationInvocation,
    GraphRelease,
    IndexRelease,
    KnowledgeRelease,
    KnowledgeSpace,
    QueryRun,
    Tenant,
    User,
)
from packages.platform.security import create_access_token, hash_password


@contextmanager
def application_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(code="test", name="测试租户")
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        username="app-admin",
        password_hash=hash_password("Application@123"),
        display_name="应用管理员",
        is_admin=True,
        enabled=True,
    )
    db.add(admin)
    db.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    token = create_access_token(admin.id, tenant.id, True)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        yield client, db
    db.close()


def test_application_credential_is_shown_once_and_revocation_is_immediate() -> None:
    with application_client() as (client, _):
        created = client.post("/api/v1/applications", json={
            "code": "risk_agent",
            "name": "风险问答应用",
            "app_type": "agent",
            "environment": "testing",
            "status": "active",
            "org_unit_id": "",
        })
        assert created.status_code == 200, created.text
        assert created.json()["org_unit_id"] is None
        application_id = created.json()["id"]

        issued = client.post(f"/api/v1/applications/{application_id}/credentials", json={
            "name": "测试凭据",
            "scopes": ["scenario.invoke", "feedback.write"],
        })
        assert issued.status_code == 200, issued.text
        credential = issued.json()
        assert credential["client_secret"].startswith("css_")
        assert "secret_hash" not in credential

        listed = client.get(f"/api/v1/applications/{application_id}/credentials")
        assert listed.status_code == 200
        assert "client_secret" not in listed.json()[0]
        assert "secret_hash" not in listed.json()[0]

        exchanged = client.post("/api/v1/application-auth/token", json={
            "client_id": credential["client_id"],
            "client_secret": credential["client_secret"],
            "scope": "scenario.invoke",
        })
        assert exchanged.status_code == 200, exchanged.text
        app_token = exchanged.json()["access_token"]

        whoami = client.get(
            "/api/v1/application-runtime/whoami",
            headers={"Authorization": f"Bearer {app_token}"},
        )
        assert whoami.status_code == 200, whoami.text
        assert whoami.json()["application_code"] == "risk_agent"
        assert whoami.json()["scopes"] == ["scenario.invoke"]

        revoked = client.delete(
            f"/api/v1/applications/{application_id}/credentials/{credential['id']}"
        )
        assert revoked.status_code == 200
        rejected = client.get(
            "/api/v1/application-runtime/whoami",
            headers={"Authorization": f"Bearer {app_token}"},
        )
        assert rejected.status_code == 401


def test_application_scope_escalation_and_invalid_code_are_rejected() -> None:
    with application_client() as (client, _):
        invalid = client.post("/api/v1/applications", json={"code": "中文 编码", "name": "无效"})
        assert invalid.status_code == 422
        created = client.post("/api/v1/applications", json={
            "code": "service_api",
            "name": "服务应用",
            "status": "active",
        }).json()
        issued = client.post(f"/api/v1/applications/{created['id']}/credentials", json={
            "name": "最小权限",
            "scopes": ["knowledge.search"],
        }).json()
        escalation = client.post("/api/v1/application-auth/token", json={
            "client_id": issued["client_id"],
            "client_secret": issued["client_secret"],
            "scope": "knowledge.search knowledge.chat",
        })
        assert escalation.status_code == 403


def test_product_release_scenario_version_and_application_runtime_are_linked() -> None:
    with application_client() as (client, db):
        admin = db.query(User).filter(User.username == "app-admin").one()
        space = KnowledgeSpace(
            tenant_id=admin.tenant_id,
            code="policy",
            name="制度知识",
            owner_id=admin.id,
            enabled=True,
        )
        db.add(space)
        db.commit()

        product = client.post("/api/v1/knowledge-products", json={
            "code": "policy_product",
            "name": "制度知识产品",
            "status": "active",
            "space_ids": [space.id],
        })
        assert product.status_code == 200, product.text
        product_id = product.json()["id"]
        duplicate = client.post("/api/v1/knowledge-products", json={
            "code": "policy_product",
            "name": "重复编码",
            "status": "active",
            "space_ids": [space.id],
        })
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "知识产品编码已存在"
        unavailable = client.post(
            f"/api/v1/knowledge-products/{product_id}/releases",
            json={"note": "第一次发布"},
        )
        assert unavailable.status_code == 409

        graph = GraphRelease(
            tenant_id=admin.tenant_id,
            space_id=space.id,
            release_number=1,
            graph_name="test-graph",
            status="published",
        )
        db.add(graph)
        db.flush()
        index = IndexRelease(
            tenant_id=admin.tenant_id,
            space_id=space.id,
            release_number=1,
            opensearch_index="test-index",
            qdrant_collection="test-collection",
            graph_release_id=graph.id,
            model_config_id="unused-model",
            embedding_dimension=3,
            status="published",
        )
        db.add(index)
        db.flush()
        knowledge = KnowledgeRelease(
            tenant_id=admin.tenant_id,
            space_id=space.id,
            release_number=1,
            graph_release_id=graph.id,
            index_release_id=index.id,
            checksum="a" * 64,
            status="published",
        )
        db.add(knowledge)
        db.commit()

        release = client.post(
            f"/api/v1/knowledge-products/{product_id}/releases",
            json={"note": "稳定发布"},
        )
        assert release.status_code == 200, release.text
        product_release_id = release.json()["id"]
        alias = client.put(
            f"/api/v1/knowledge-products/{product_id}/aliases/production",
            json={"product_release_id": product_release_id, "reason": "投入生产验证"},
        )
        assert alias.status_code == 200, alias.text

        scenario = client.post("/api/v1/application-scenarios", json={
            "code": "policy_search",
            "name": "制度检索",
            "scenario_type": "search",
            "status": "active",
        })
        assert scenario.status_code == 200, scenario.text
        scenario_id = scenario.json()["id"]
        version = client.post(f"/api/v1/application-scenarios/{scenario_id}/versions", json={
            "product_id": product_id,
            "product_alias": "production",
            "tool_whitelist": ["knowledge_search"],
            "retrieval_policy": {
                "top_k": 5,
                "use_keyword": False,
                "use_vector": False,
                "use_graph": True,
                "use_reranker": False,
            },
        })
        assert version.status_code == 200, version.text
        assert version.json()["version"] == 1

        application = client.post("/api/v1/applications", json={
            "code": "policy_portal",
            "name": "制度门户",
            "status": "active",
        }).json()
        grant = client.post(f"/api/v1/applications/{application['id']}/grants", json={
            "resource_type": "scenario",
            "resource_id": scenario_id,
            "permission": "invoke",
            "effect": "allow",
        })
        assert grant.status_code == 200, grant.text
        credential = client.post(f"/api/v1/applications/{application['id']}/credentials", json={
            "name": "运行凭据",
            "scopes": ["scenario.invoke"],
        }).json()
        access = client.post("/api/v1/application-auth/token", json={
            "client_id": credential["client_id"],
            "client_secret": credential["client_secret"],
        }).json()["access_token"]
        denied_without_product_grant = client.post(
            "/api/v1/application-runtime/scenarios/policy_search/search",
            json={"query": "差旅制度是什么？"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert denied_without_product_grant.status_code == 403
        product_grant = client.post(f"/api/v1/applications/{application['id']}/grants", json={
            "resource_type": "knowledge_product",
            "resource_id": product_id,
            "permission": "read",
            "effect": "allow",
        })
        assert product_grant.status_code == 200, product_grant.text
        invoked = client.post(
            "/api/v1/application-runtime/scenarios/policy_search/search",
            json={"query": "差旅制度是什么？"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert invoked.status_code == 200, invoked.text
        assert invoked.json()["scenario"] == {"code": "policy_search", "version": 1}
        assert invoked.json()["knowledge_product_release"]["id"] == product_release_id
        assert db.query(QueryRun).count() == 1
        invocation = db.query(ApplicationInvocation).one()
        assert invocation.status == "succeeded"
        assert invocation.output_summary["result_count"] == 0
