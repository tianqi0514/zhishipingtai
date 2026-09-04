from __future__ import annotations

from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.routes import router
from packages.platform.database import Base, get_db
from packages.platform.models import KnowledgeSpace, Tenant, User
from packages.platform.security import create_access_token, hash_password


@contextmanager
def governance_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(code="journey", name="业务旅程测试")
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        username="journey-admin",
        password_hash=hash_password("JourneyTest@123"),
        display_name="业务管理员",
        is_admin=True,
        enabled=True,
    )
    db.add(admin)
    db.flush()
    space = KnowledgeSpace(
        tenant_id=tenant.id,
        code="journey-space",
        name="业务旅程空间",
        owner_id=admin.id,
        enabled=True,
    )
    db.add(space)
    db.commit()

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    token = create_access_token(admin.id, tenant.id, True)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        yield client, space
    db.close()


def test_empty_space_governance_overview_recommends_first_real_step() -> None:
    with governance_client() as (client, space):
        response = client.get(
            "/api/v1/knowledge/governance-overview",
            params={"space_id": space.id},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["space_id"] == space.id
        assert body["document_count"] == 0
        assert body["processing_modes"] == {
            "vector_only": 0,
            "graph_only": 0,
            "both": 0,
            "pending": 0,
        }
        assert body["entity_count"] == 0
        assert body["asserted_fact_count"] == 0
        assert body["next_action"]["view"] == "documents"
        assert body["next_action"]["label"] == "上传第一份文档"
