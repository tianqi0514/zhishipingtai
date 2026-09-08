from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.writing import router
from packages.platform.database import Base, get_db
from packages.platform.models import (
    KnowledgeProduct,
    KnowledgeProductRelease,
    KnowledgeProductReleaseItem,
    KnowledgeSpace,
    Tenant,
    User,
)
from packages.platform.security import create_access_token, hash_password


@contextmanager
def writing_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(code="miaobi", name="妙笔测试租户")
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        username="miaobi-admin",
        password_hash=hash_password("MiaobiTest@123"),
        display_name="妙笔管理员",
        is_admin=True,
        enabled=True,
    )
    db.add(admin)
    db.flush()
    space = KnowledgeSpace(
        tenant_id=tenant.id,
        code="emergency",
        name="应急知识空间",
        owner_id=admin.id,
        enabled=True,
    )
    db.add(space)
    db.flush()
    product = KnowledgeProduct(
        tenant_id=tenant.id,
        code="emergency-product",
        name="应急知识产品",
        owner_id=admin.id,
        status="active",
        enabled=True,
    )
    db.add(product)
    db.flush()
    release = KnowledgeProductRelease(
        tenant_id=tenant.id,
        product_id=product.id,
        version=1,
        manifest={},
        checksum="a" * 64,
        status="published",
        created_by=admin.id,
    )
    db.add(release)
    db.flush()
    db.add(
        KnowledgeProductReleaseItem(
            tenant_id=tenant.id,
            product_release_id=release.id,
            space_id=space.id,
            knowledge_release_id="knowledge-release-test",
            checksum="b" * 64,
        )
    )
    db.commit()

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    token = create_access_token(admin.id, tenant.id, True)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        yield client, db, release
    db.close()


def _create_project(client: TestClient, release_id: str) -> dict:
    fixture = json.loads(Path("demo/miaobi/scenarios/earthquake.v1.json").read_text(encoding="utf-8"))
    package = client.post(
        "/api/v1/writing/scenario-packages",
        json={
            "code": fixture["code"],
            "name": fixture["name"],
            "disaster_type": fixture["disaster_type"],
            "description": fixture.get("description", ""),
        },
    )
    assert package.status_code == 200, package.text
    version_payload = {
        key: fixture[key]
        for key in (
            "input_schema",
            "ontology_mapping",
            "rule_set_ids",
            "formula_ids",
            "tool_ids",
            "chapter_template",
            "output_schema",
            "review_rules",
            "decision_gates",
            "comparison_dimensions",
            "config",
        )
    }
    version = client.post(
        f"/api/v1/writing/scenario-packages/{package.json()['id']}/versions",
        json={**version_payload, "activate": True},
    )
    assert version.status_code == 200, version.text
    project = client.post(
        "/api/v1/writing/projects",
        json={
            "code": "jishishan-earthquake",
            "name": "积石山县地震应急处置方案",
            "scenario_package_version_id": version.json()["id"],
            "knowledge_product_release_id": release_id,
        },
    )
    assert project.status_code == 200, project.text
    return project.json()


def test_writing_project_fact_computation_and_local_stale_propagation() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        fact = client.post(
            f"/api/v1/writing/projects/{project['id']}/facts",
            json={
                "fact_key": "rescue_available",
                "label": "可用搜救人员",
                "fact_type": "official_brief",
                "value": {"number": 320},
                "unit": "人",
                "source_type": "official_brief",
                "source_id": "brief-v1",
                "verification_status": "verified",
            },
        )
        assert fact.status_code == 200, fact.text
        run = client.post(
            f"/api/v1/writing/projects/{project['id']}/compute",
            json={
                "operation": "resource_gap",
                "inputs": {"required": 500, "available": 320},
                "input_fact_ids": [fact.json()["id"]],
            },
        )
        assert run.status_code == 200, run.text
        assert run.json()["result"]["value"] == 180
        document = client.post(
            "/api/v1/writing/documents",
            json={
                "project_id": project["id"],
                "title": "地震应急处置方案",
                "content": [{"id": "gap-block", "type": "computed_metric", "children": [{"text": "搜救人员缺口180人"}]}],
            },
        )
        assert document.status_code == 200, document.text
        binding = client.post(
            f"/api/v1/writing/documents/{document.json()['id']}/bindings",
            json={
                "block_id": "gap-block",
                "block_type": "computed_metric",
                "source_type": "computation",
                "computation_run_id": run.json()["id"],
                "content_hash": "c" * 64,
                "verification_status": "verified",
            },
        )
        assert binding.status_code == 200, binding.text
        updated = client.post(
            f"/api/v1/writing/projects/{project['id']}/facts",
            json={
                "fact_key": "rescue_available",
                "label": "可用搜救人员",
                "fact_type": "official_brief",
                "value": {"number": 400},
                "unit": "人",
                "source_type": "official_brief",
                "source_id": "brief-v2",
                "verification_status": "verified",
            },
        )
        assert updated.status_code == 200, updated.text
        stale = client.get(f"/api/v1/writing/documents/{document.json()['id']}/stale-blocks")
        assert stale.status_code == 200
        assert [item["block_id"] for item in stale.json()] == ["gap-block"]
        impact = client.post(
            f"/api/v1/writing/documents/{document.json()['id']}/recompute",
            json={"changed_fact_ids": [updated.json()["id"]]},
        )
        assert impact.status_code == 200, impact.text
        assert impact.json()["block_ids"] == ["gap-block"]
        assert impact.json()["automatic_overwrite"] is False


def test_publish_requires_all_decision_gates() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        document = client.post(
            "/api/v1/writing/documents",
            json={"project_id": project["id"], "title": "正式方案", "content": []},
        ).json()
        blocked = client.post(
            f"/api/v1/writing/documents/{document['id']}/versions",
            json={"content": [], "change_summary": "拟发布", "publish": True},
        )
        assert blocked.status_code == 409
        gates = client.get(f"/api/v1/writing/projects/{project['id']}/decision-gates").json()
        assert gates
        for gate in gates:
            decided = client.post(
                f"/api/v1/writing/projects/{project['id']}/decision-gates/{gate['id']}/records",
                json={"decision": "confirm", "reason": "测试确认"},
            )
            assert decided.status_code == 200, decided.text
        published = client.post(
            f"/api/v1/writing/documents/{document['id']}/versions",
            json={"content": [], "change_summary": "正式发布", "publish": True},
        )
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "published"


def test_plan_profiles_are_not_language_only_variants() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        response = client.post(
            f"/api/v1/writing/projects/{project['id']}/plans/generate",
            json={
                "count": 3,
                "inputs": {
                    "route_start": "指挥部",
                    "route_target": "震中",
                    "route_graph": {
                        "指挥部": [
                            {"to": "快速通道", "minutes": 10, "risk": 7},
                            {"to": "安全通道", "minutes": 24, "risk": 1},
                        ],
                        "快速通道": [{"to": "震中", "minutes": 10, "risk": 7}],
                        "安全通道": [{"to": "震中", "minutes": 24, "risk": 1}],
                    },
                    "resources": [{"name": "搜救人员", "unit": "人", "required": 500, "available": 320}],
                },
            },
        )
        assert response.status_code == 200, response.text
        plans = response.json()
        assert len(plans) == 3
        assert plans[0]["result"]["route"]["path"] != plans[1]["result"]["route"]["path"]
        assert all(item["unresolved_gaps"][0]["gap"] == 180 for item in plans)
