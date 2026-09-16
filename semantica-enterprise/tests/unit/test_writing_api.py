from __future__ import annotations

import json
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.writing import _select_current_plan_rows, router
from apps.api.agent_internal import (
    _writing_model_capacity,
    agent_writing_context,
    agent_writing_document_outline,
    issue_credential,
)
from apps.api.schemas import AgentCredentialRequest
from apps.api.writing_schemas import AgentWritingRequest
from packages.platform.database import Base, get_db
from packages.platform.models import (
    AgentCredential,
    Conversation,
    Document,
    DocumentVersion,
    KnowledgeProduct,
    KnowledgeProductRelease,
    KnowledgeProductReleaseItem,
    KnowledgeRelease,
    KnowledgeSpace,
    GraphRelease,
    IndexRelease,
    ModelConfig,
    ProjectFact,
    Tenant,
    User,
    WritingAgentSession,
    WritingBlockBinding,
    WritingDocument,
    WritingDocumentVersion,
    WritingProjectMember,
    WritingProjectMaterial,
    WritingGraphRelease,
    WritingGraphReleaseItem,
)
from packages.platform.security import create_access_token, hash_password
from packages.platform.writing import content_hash


def test_export_plan_selection_keeps_one_current_plan_per_objective() -> None:
    def plan(key: str, objective: str, version: int, status: str, fingerprint: str):
        return SimpleNamespace(
            plan_key=key,
            name=key,
            objective=objective,
            version=version,
            status=status,
            result={"input_fingerprint": fingerprint},
        )

    rows = [
        plan("speed", "speed", 3, "candidate", "current"),
        plan("safety", "safety", 3, "candidate", "current"),
        plan("balanced", "balanced", 3, "candidate", "current"),
        plan("balanced", "balanced", 2, "selected", "current"),
        plan("speed", "speed", 1, "candidate", "old"),
        plan("safety", "safety", 1, "candidate", "old"),
    ]

    selected = _select_current_plan_rows(rows)

    assert [(row.objective, row.version) for row in selected] == [
        ("balanced", 2),
        ("safety", 3),
        ("speed", 3),
    ]


def test_writing_model_capacity_keeps_provider_context_headroom() -> None:
    max_tokens, parameters = _writing_model_capacity(
        {
            "parameters": {"context_window": 16384, "provider_flag": True},
            "writing_context_safety_tokens": 512,
            "writing_input_reserve_tokens": 9216,
        },
        7000,
    )

    assert parameters == {
        "context_window": 15744,
        "provider_flag": True,
    }
    assert max_tokens == 2600


def test_writing_model_capacity_preserves_provider_parameters_without_context() -> None:
    max_tokens, parameters = _writing_model_capacity(
        {"parameters": {"enable_thinking": False}},
        4096,
    )

    assert max_tokens == 2600
    assert parameters == {"enable_thinking": False}


def test_writing_compaction_budget_is_independent_from_chapter_output_source() -> None:
    source = Path("apps/api/agent_internal.py").read_text(encoding="utf-8")
    assert 'config.get("writing_compaction_max_tokens", 4096)' in source
    assert '"compaction_max_tokens": compaction_max_tokens' in source
    assert "context_window // 4" in source


def test_public_reference_is_project_scoped_versioned_metadata_not_a_fact() -> None:
    with writing_client() as (client, _db, release):
        project = _create_project(client, release.id)
        payload = {
            "title": "某省地震应急预案",
            "publisher": "某省人民政府办公厅",
            "url": "https://example.gov.cn/policy/earthquake-plan",
            "publication_date": "2026-03-01",
            "excerpt": "本材料仅用于测试公开制度引用，不构成本项目灾情事实。",
            "applicable_scope": {"region": "某省", "purpose": "structure_reference"},
            "validity_status": "current",
            "usage_sections": ["编制说明", "组织指挥"],
        }
        created = client.post(
            f"/api/v1/writing/projects/{project['id']}/public-references", json=payload,
        )
        assert created.status_code == 200, created.text
        assert created.json()["url"] == payload["url"]
        assert created.json()["validity_status"] == "current"
        duplicate = client.post(
            f"/api/v1/writing/projects/{project['id']}/public-references", json=payload,
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["unchanged"] is True
        listed = client.get(
            f"/api/v1/writing/projects/{project['id']}/public-references",
        )
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        invalid = client.post(
            f"/api/v1/writing/projects/{project['id']}/public-references",
            json={**payload, "url": "file:///etc/passwd"},
        )
        assert invalid.status_code == 422


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
    embedding = ModelConfig(
        tenant_id=tenant.id,
        name="测试向量模型",
        model_kind="embedding",
        provider="local",
        model_name="test-embedding",
        enabled=True,
        is_default=True,
    )
    db.add(embedding)
    db.flush()
    graph_release = GraphRelease(
        tenant_id=tenant.id,
        space_id=space.id,
        release_number=1,
        graph_name="miaobi_test_graph",
        entity_count=12,
        fact_count=18,
        status="published",
        published_at=datetime.now(timezone.utc),
    )
    db.add(graph_release)
    db.flush()
    index_release = IndexRelease(
        tenant_id=tenant.id,
        space_id=space.id,
        release_number=1,
        opensearch_index="miaobi_test_index",
        qdrant_collection="miaobi_test_vectors",
        graph_release_id=graph_release.id,
        model_config_id=embedding.id,
        embedding_dimension=4,
        document_count=3,
        chunk_count=16,
        status="published",
        published_at=datetime.now(timezone.utc),
    )
    db.add(index_release)
    db.flush()
    knowledge_release = KnowledgeRelease(
        tenant_id=tenant.id,
        space_id=space.id,
        release_number=1,
        graph_release_id=graph_release.id,
        index_release_id=index_release.id,
        checksum="e" * 64,
        status="published",
        published_at=datetime.now(timezone.utc),
    )
    db.add(knowledge_release)
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
            knowledge_release_id=knowledge_release.id,
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


def test_vector_only_space_can_start_writing_from_real_index_snapshot() -> None:
    with writing_client() as (client, db, _):
        space = db.scalar(select(KnowledgeSpace))
        first = db.scalar(select(IndexRelease).where(IndexRelease.space_id == space.id))
        db.add(IndexRelease(
            tenant_id=space.tenant_id, space_id=space.id, release_number=2,
            opensearch_index="miaobi_vector_only_index_2",
            qdrant_collection="miaobi_vector_only_vectors_2",
            graph_release_id=None, model_config_id=first.model_config_id,
            embedding_dimension=first.embedding_dimension, document_count=4,
            chunk_count=20, status="published", published_at=datetime.now(timezone.utc),
        ))
        db.commit()
        response = client.post("/api/v1/writing/projects", json={
            "code": "vector-only-writing", "name": "仅全文向量写作测试",
            "space_id": space.id,
        })
        assert response.status_code == 200, response.text
        release = db.scalar(select(KnowledgeRelease).where(
            KnowledgeRelease.space_id == space.id,
            KnowledgeRelease.status == "published",
        ))
        assert release is not None and release.graph_release_id is None
        assert db.get(IndexRelease, release.index_release_id).release_number == 2
        context = client.get(f"/api/v1/writing/projects/{response.json()['id']}/knowledge-context")
        assert context.status_code == 200, context.text
        assert context.json()["spaces"][0]["graph_available"] is False
        assert context.json()["spaces"][0]["vector_available"] is True


def test_project_knowledge_context_exposes_locked_zhiku_release() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        response = client.get(f"/api/v1/writing/projects/{project['id']}/knowledge-context")
        assert response.status_code == 200, response.text
        context = response.json()
        assert context["product"]["name"] == "应急知识产品"
        assert context["release"]["version"] == 1
        assert context["release"]["is_latest"] is True
        assert context["snapshot_locked"] is True
        assert context["document_count"] == 3
        assert context["chunk_count"] == 16
        assert context["entity_count"] == 12
        assert context["fact_count"] == 18
        assert context["spaces"][0]["graph_available"] is True
        assert context["spaces"][0]["vector_available"] is True


def test_writer_can_create_blank_project_without_technical_scope() -> None:
    with writing_client() as (client, db, release):
        _create_project(client, release.id)  # seeds the tenant's active writing template
        response = client.post(
            "/api/v1/writing/projects",
            json={"name": "空白写作项目", "config": {"subject": "先起草，稍后补充资料"}},
        )
        assert response.status_code == 200, response.text
        project = response.json()
        assert project["code"].startswith("writing-")
        assert project["knowledge_product_release_id"] is None
        detail = client.get(f"/api/v1/writing/projects/{project['id']}")
        assert detail.status_code == 200
        assert detail.json()["scenario"]["package_name"] == "通用写作"
        assert detail.json()["input_contract"]["required"] == []
        document = client.post(
            "/api/v1/writing/documents",
            json={
                "project_id": project["id"],
                "title": "空白文章",
                "document_type": "custom",
                "purpose": "内部讨论",
                "audience": "项目组",
                "content": [{"id": "blank-p", "type": "p", "children": [{"text": "可以直接编辑。"}]}],
            },
        )
        assert document.status_code == 200, document.text
        assert document.json()["purpose"] == "内部讨论"
        assert document.json()["current_version"]["knowledge_product_release_id"] is None
        search = client.post(
            f"/api/v1/writing/projects/{project['id']}/knowledge/search",
            json={"query": "尚未添加的资料", "document_id": document.json()["id"]},
        )
        assert search.status_code == 409
        assert "尚未选择知识空间" in search.text

        release_item = db.scalar(
            select(KnowledgeProductReleaseItem).where(
                KnowledgeProductReleaseItem.product_release_id == release.id
            )
        )
        attached = client.post(
            f"/api/v1/writing/projects/{project['id']}/knowledge-space",
            json={"space_id": release_item.space_id},
        )
        assert attached.status_code == 200, attached.text
        assert attached.json()["knowledge_product_release_id"]
        refreshed_document = client.get(f"/api/v1/writing/documents/{document.json()['id']}")
        assert refreshed_document.status_code == 200
        assert refreshed_document.json()["knowledge_product_release_id"] == attached.json()["knowledge_product_release_id"]


def test_multiple_articles_keep_independent_business_scope() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        first = client.post(
            "/api/v1/writing/documents",
            json={
                "project_id": project["id"],
                "title": "应急预案修订稿",
                "document_type": "emergency_plan",
                "purpose": "制度修订",
                "audience": "管理层",
                "applicability": {"region": "积石山县", "time_range": "长期"},
                "writing_requirements": "保持预案体例",
            },
        )
        second = client.post(
            "/api/v1/writing/documents",
            json={
                "project_id": project["id"],
                "title": "本次事件处置方案",
                "document_type": "response_plan",
                "purpose": "现场执行",
                "audience": "应急指挥人员",
                "applicability": {"region": "积石山县", "time_range": "本次事件"},
                "writing_requirements": "突出任务、责任和时限",
            },
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        rows = client.get(f"/api/v1/writing/projects/{project['id']}/documents").json()
        by_title = {row["title"]: row for row in rows}
        assert by_title["应急预案修订稿"]["purpose"] == "制度修订"
        assert by_title["本次事件处置方案"]["purpose"] == "现场执行"
        assert by_title["应急预案修订稿"]["applicability"] != by_title["本次事件处置方案"]["applicability"]


def test_report_generation_can_start_ready_sections_without_global_input_gate() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        document = client.post(
            "/api/v1/writing/documents",
            json={"project_id": project["id"], "title": "分章节起草", "content": []},
        )
        assert document.status_code == 200, document.text
        response = client.post(
            f"/api/v1/writing/projects/{project['id']}/generate-report",
            json={"document_id": document.json()["id"], "allow_partial": True},
        )
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["status"] == "awaiting_agent"
        assert run["section_plan"]
        assert run["input_snapshot"]["facts"] == []


def test_report_generation_does_not_overwrite_an_edited_document() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        document = client.post(
            "/api/v1/writing/documents",
            json={"project_id": project["id"], "title": "已人工编辑的文章", "content": []},
        ).json()
        saved = client.post(
            f"/api/v1/writing/documents/{document['id']}/versions",
            json={
                "content": [{"id": "manual-p", "type": "p", "children": [{"text": "这是作者已经确认的正文。"}]}],
                "change_summary": "人工修改正文",
                "publish": False,
            },
        )
        assert saved.status_code == 200, saved.text
        response = client.post(
            f"/api/v1/writing/projects/{project['id']}/generate-report",
            json={"document_id": document["id"], "allow_partial": True},
        )
        assert response.status_code == 409
        assert "不能用整篇生成覆盖" in response.text


def test_writing_project_is_created_from_a_knowledge_space() -> None:
    with writing_client() as (client, db, release):
        seeded = _create_project(client, release.id)
        spaces = client.get("/api/v1/writing/spaces")
        assert spaces.status_code == 200, spaces.text
        space_id = db.query(KnowledgeProductReleaseItem).filter_by(product_release_id=release.id).one().space_id
        assert spaces.json() == [
            {
                "id": space_id,
                "name": "应急知识空间",
                "code": "emergency",
                "ready": True,
                "knowledge_version": 1,
                "writing_graph_ready": False,
                "writing_graph_releases": [],
            }
        ]
        created = client.post(
            "/api/v1/writing/projects",
            json={
                "code": "space-first-writing-project",
                "name": "从知识空间创建的写作项目",
                "scenario_package_version_id": seeded["scenario_package_version_id"],
                "space_id": spaces.json()[0]["id"],
            },
        )
        assert created.status_code == 200, created.text
        context = client.get(
            f"/api/v1/writing/projects/{created.json()['id']}/knowledge-context"
        )
        assert context.status_code == 200, context.text
        assert [item["id"] for item in context.json()["spaces"]] == [spaces.json()[0]["id"]]


def test_project_and_article_pin_one_immutable_writing_graph_release() -> None:
    with writing_client() as (client, db, release):
        seeded = _create_project(client, release.id)
        space_id = db.scalar(select(KnowledgeProductReleaseItem.space_id).where(
            KnowledgeProductReleaseItem.product_release_id == release.id,
        ))
        graph = WritingGraphRelease(
            tenant_id=release.tenant_id,
            space_id=space_id,
            release_number=1,
            graph_name="writing_acceptance_r1",
            evidence_count=4,
            entity_count=3,
            claim_count=3,
            fact_count=3,
            relation_count=2,
            checksum="c" * 64,
            status="published",
            created_by=db.scalar(select(User.id)),
            published_at=datetime.now(timezone.utc),
        )
        db.add(graph); db.commit()
        spaces = client.get("/api/v1/writing/spaces").json()
        assert spaces[0]["writing_graph_ready"] is True
        assert spaces[0]["writing_graph_releases"][0]["id"] == graph.id
        project = client.post("/api/v1/writing/projects", json={
            "code": "writing-graph-pinned",
            "name": "锁定写作图谱的项目",
            "scenario_package_version_id": seeded["scenario_package_version_id"],
            "space_id": space_id,
            "writing_graph_release_id": graph.id,
        })
        assert project.status_code == 200, project.text
        assert project.json()["knowledge_space_id"] == space_id
        assert project.json()["writing_graph_release_id"] == graph.id
        article = client.post("/api/v1/writing/documents", json={
            "project_id": project.json()["id"],
            "title": "图谱约束文章",
            "document_type": "response_plan",
            "content": [],
        })
        assert article.status_code == 200, article.text
        assert article.json()["writing_graph_release_id"] == graph.id
        assert article.json()["current_version"]["writing_graph_release_id"] == graph.id
        context = client.get(f"/api/v1/writing/projects/{project.json()['id']}/knowledge-context")
        assert context.status_code == 200, context.text
        assert context.json()["writing_graph"]["id"] == graph.id
        assert context.json()["writing_graph"]["snapshot_locked"] is True


def test_project_adopts_only_verified_facts_from_its_pinned_writing_graph() -> None:
    with writing_client() as (client, db, release):
        seeded = _create_project(client, release.id)
        space_id = db.scalar(select(KnowledgeProductReleaseItem.space_id).where(
            KnowledgeProductReleaseItem.product_release_id == release.id,
        ))
        graph = WritingGraphRelease(
            tenant_id=release.tenant_id,
            space_id=space_id,
            release_number=1,
            graph_name="writing_fact_adoption_r1",
            evidence_count=1,
            entity_count=1,
            claim_count=1,
            fact_count=1,
            relation_count=0,
            checksum="f" * 64,
            status="published",
            created_by=db.scalar(select(User.id)),
            published_at=datetime.now(timezone.utc),
        )
        db.add(graph)
        db.flush()
        graph_fact_id = "11111111-1111-4111-8111-111111111111"
        db.add(WritingGraphReleaseItem(
            tenant_id=release.tenant_id,
            release_id=graph.id,
            object_type="fact",
            object_id=graph_fact_id,
            object_version=1,
            content_hash="1" * 64,
            snapshot={
                "id": graph_fact_id,
                "predicate": "可用搜救人员",
                "object_value": {"value": 320},
                "value_type": "number",
                "unit": "人",
                "evidence_ids": ["evidence-1"],
                "claim_ids": ["claim-1"],
                "time_scope": {"as_of": "2026-09-16"},
                "applicable_scope": {"region": "测试地区"},
                "verification_status": "verified",
            },
        ))
        db.commit()

        project = client.post("/api/v1/writing/projects", json={
            "code": "writing-graph-fact-adoption",
            "name": "采用治理事实的项目",
            "scenario_package_version_id": seeded["scenario_package_version_id"],
            "space_id": space_id,
            "writing_graph_release_id": graph.id,
        })
        assert project.status_code == 200, project.text
        endpoint = f"/api/v1/writing/projects/{project.json()['id']}/facts/adopt-writing-graph"
        payload = {"items": [{
            "fact_id": graph_fact_id,
            "fact_key": "rescue_available",
            "label": "可用搜救人员",
        }]}
        adopted = client.post(endpoint, json=payload)
        assert adopted.status_code == 200, adopted.text
        assert len(adopted.json()["adopted"]) == 1
        project_fact = adopted.json()["adopted"][0]
        assert project_fact["source_type"] == "writing_graph_fact"
        assert project_fact["fact_type"] == "writing_graph_fact"
        assert project_fact["value"] == {"value": 320}
        assert project_fact["verification_status"] == "verified"
        assert project_fact["source_locator"]["writing_graph_release_id"] == graph.id
        assert project_fact["source_locator"]["evidence_ids"] == ["evidence-1"]

        repeated = client.post(endpoint, json=payload)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["adopted"] == []
        assert len(repeated.json()["reused"]) == 1

        unknown = client.post(endpoint, json={"items": [{
            "fact_id": "22222222-2222-4222-8222-222222222222",
            "fact_key": "unknown_fact",
        }]})
        assert unknown.status_code == 422


def test_project_materials_pin_versions_and_removal_preserves_zhiku_document() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        release_item = db.query(KnowledgeProductReleaseItem).filter_by(product_release_id=release.id).one()
        document = Document(
            tenant_id=release.tenant_id,
            space_id=release_item.space_id,
            title="临夏州地震应急预案",
            status="published",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            tenant_id=release.tenant_id,
            document_id=document.id,
            version_number=1,
            filename="临夏州地震应急预案.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size=1024,
            sha256="9" * 64,
            object_key="tests/linxia-plan.docx",
            status="processed",
        )
        db.add(version)
        db.flush()
        document.current_version_id = version.id

        internal_document = Document(
            tenant_id=release.tenant_id,
            space_id=release_item.space_id,
            title="旧版机器契约",
            status="published",
        )
        db.add(internal_document)
        db.flush()
        internal_version = DocumentVersion(
            tenant_id=release.tenant_id,
            document_id=internal_document.id,
            version_number=1,
            filename="linxiaearthquakeemergency.ontology.yaml",
            content_type="application/yaml",
            size=512,
            sha256="8" * 64,
            object_key="tests/internal-ontology.yaml",
            status="processed",
        )
        db.add(internal_version)
        db.flush()
        internal_document.current_version_id = internal_version.id
        db.commit()

        candidates = client.get(f"/api/v1/writing/projects/{project['id']}/material-candidates")
        assert candidates.status_code == 200, candidates.text
        assert [item["filename"] for item in candidates.json()] == [version.filename]
        assert candidates.json()[0]["already_linked"] is False

        internal_add = client.post(
            f"/api/v1/writing/projects/{project['id']}/materials",
            json={"document_id": internal_document.id},
        )
        assert internal_add.status_code == 409
        assert "系统配置或中间产物" in internal_add.json()["detail"]

        created = client.post(
            f"/api/v1/writing/projects/{project['id']}/materials",
            json={
                "document_id": document.id,
                "material_role": "policy_basis",
                "usage_scope": "space_asset",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["version"]["id"] == version.id
        assert created.json()["version_pinned"] is True

        article = client.post(
            "/api/v1/writing/documents",
            json={"project_id": project["id"], "title": "仅采用选定资料的文章"},
        ).json()
        inherited = client.get(
            f"/api/v1/writing/projects/{project['id']}/materials?document_id={article['id']}"
        )
        assert inherited.status_code == 200, inherited.text
        assert inherited.json()[0]["adopted_by_article"] is True
        cleared = client.put(
            f"/api/v1/writing/documents/{article['id']}/materials",
            json={"material_ids": []},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["material_ids"] == []
        explicit = client.get(
            f"/api/v1/writing/projects/{project['id']}/materials?document_id={article['id']}"
        )
        assert explicit.json()[0]["adopted_by_article"] is False

        duplicate = client.post(
            f"/api/v1/writing/projects/{project['id']}/materials",
            json={"document_id": document.id},
        )
        assert duplicate.status_code == 409

        updated = client.put(
            f"/api/v1/writing/projects/{project['id']}/materials/{created.json()['id']}",
            json={"material_role": "reference", "usage_scope": "task_only"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["material_role"] == "reference"

        session = client.post(
            f"/api/v1/writing/projects/{project['id']}/agent-sessions",
            json={"purpose": "editing", "start_new": True},
        )
        assert session.status_code == 200, session.text
        conversation = db.get(Conversation, session.json()["conversation_id"])
        assert conversation.settings["material_document_ids"] == [document.id]
        assert conversation.settings["allowed_document_ids"] == [document.id]

        removed = client.delete(
            f"/api/v1/writing/projects/{project['id']}/materials/{created.json()['id']}"
        )
        assert removed.status_code == 200, removed.text
        assert removed.json() == {"deleted": True, "document_preserved": True}
        assert db.get(Document, document.id).deleted_at is None
        assert db.get(DocumentVersion, version.id).deleted_at is None
        assert db.get(WritingProjectMaterial, created.json()["id"]).status == "removed"

        fallback_session = client.post(
            f"/api/v1/writing/projects/{project['id']}/agent-sessions",
            json={"purpose": "editing", "start_new": True},
        )
        assert fallback_session.status_code == 200, fallback_session.text
        fallback_conversation = db.get(Conversation, fallback_session.json()["conversation_id"])
        assert fallback_conversation.settings["material_document_ids"] == []
        assert fallback_conversation.settings["allowed_document_ids"] == [document.id]
        assert internal_document.id not in fallback_conversation.settings["allowed_document_ids"]


def test_extraction_workbench_projects_real_materials_facts_and_prompts() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        release_item = db.query(KnowledgeProductReleaseItem).filter_by(product_release_id=release.id).one()
        document = Document(
            tenant_id=release.tenant_id,
            space_id=release_item.space_id,
            title="地震应急工作简报",
            status="published",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            tenant_id=release.tenant_id,
            document_id=document.id,
            version_number=1,
            filename="地震应急工作简报.pdf",
            content_type="application/pdf",
            size=2048,
            sha256="7" * 64,
            object_key="tests/earthquake-brief.pdf",
            status="processed",
        )
        db.add(version)
        db.flush()
        document.current_version_id = version.id
        db.commit()
        linked = client.post(
            f"/api/v1/writing/projects/{project['id']}/materials",
            json={"document_id": document.id, "material_role": "task_data"},
        )
        assert linked.status_code == 200, linked.text
        fact = ProjectFact(
            tenant_id=release.tenant_id,
            project_id=project["id"],
            fact_key="available_rescuers",
            label="可用搜救人员",
            fact_type="manual_input",
            value={"value": 320},
            unit="人",
            source_type="document",
            source_id=version.id,
            verification_status="verified",
            freshness_status="current",
            version=1,
            active=True,
        )
        db.add(fact)
        db.commit()

        response = client.get(f"/api/v1/writing/projects/{project['id']}/extraction-workbench")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["material_count"] == 1
        assert [step["key"] for step in payload["steps"]] == [
            "material_role", "sample_profile", "evidence", "entity",
            "claim", "fact", "relation", "metric",
        ]
        assert all(step["prompt"] and "JSON" in step["prompt"] for step in payload["steps"])
        by_key = {step["key"]: step for step in payload["steps"]}
        assert by_key["material_role"]["items"][0]["role"] == "task_data"
        assert by_key["fact"]["items"][0]["key"] == "available_rescuers"
        assert by_key["metric"]["items"][0]["name"] == "可用搜救人员"
        assert by_key["sample_profile"]["status"] == "not_required"


def test_business_scenario_config_round_trip_creates_executable_version() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        detail = client.get(f"/api/v1/writing/projects/{project['id']}").json()
        packages = client.get("/api/v1/writing/scenario-packages").json()
        package_id = packages[0]["id"]
        current = client.get(f"/api/v1/writing/scenario-packages/{package_id}/business-config")
        assert current.status_code == 200, current.text
        config = current.json()["config"]
        assert config["inputs"]
        assert config["sections"]
        assert next(item for item in config["inputs"] if item["key"] == "magnitude")["label"] == "地震震级"
        assert all(item["runtime_effect"] for item in current.json()["setting_effects"])

        config["sections"][0]["purpose"] = "说明方案编制目的、适用范围和知识依据。"
        config["toolbox"]["target_sections"]["rescue_gap"] = [config["sections"][0]["key"]]
        config["writing_policy"]["allow_manual_override"] = False
        config["output"]["allowed_formats"] = ["docx"]
        config["activate"] = True
        validated = client.post(
            f"/api/v1/writing/scenario-packages/{package_id}/business-config/validate",
            json=config,
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"] is True
        saved = client.put(
            f"/api/v1/writing/scenario-packages/{package_id}/business-config",
            json=config,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["version"]["status"] == "active"
        assert saved.json()["config"]["sections"][0]["purpose"].startswith("说明方案编制目的")
        assert detail["scenario_package_version_id"] != saved.json()["version"]["id"]

        pinned = client.post(
            "/api/v1/writing/projects",
            json={
                "code": "configured-earthquake",
                "name": "已配置地震报告",
                "scenario_package_version_id": saved.json()["version"]["id"],
                "knowledge_product_release_id": release.id,
            },
        )
        assert pinned.status_code == 200, pinned.text
        pinned_detail = client.get(f"/api/v1/writing/projects/{pinned.json()['id']}")
        assert pinned_detail.status_code == 200, pinned_detail.text
        assert pinned_detail.json()["scenario"]["business_config"]["output"]["allowed_formats"] == ["docx"]
        assert pinned_detail.json()["scenario"]["business_config"]["toolbox"]["target_sections"]["rescue_gap"] == [config["sections"][0]["key"]]

        fact = client.post(
            f"/api/v1/writing/projects/{pinned.json()['id']}/facts",
            json={
                "fact_key": "magnitude",
                "label": "震级",
                "fact_type": "official_brief",
                "value": {"number": 6.2},
                "unit": "级",
                "source_type": "official_brief",
                "verification_status": "verified",
            },
        )
        assert fact.status_code == 200, fact.text
        blocked_override = client.post(
            f"/api/v1/writing/projects/{pinned.json()['id']}/facts/{fact.json()['id']}/confirm",
            json={"decision": "override", "new_value": {"number": 6.3}, "reason": "测试配置约束"},
        )
        assert blocked_override.status_code == 409

        writing_document = WritingDocument(
            tenant_id=release.tenant_id,
            project_id=pinned.json()["id"],
            title="导出约束测试",
            status="draft",
            created_by=release.created_by,
        )
        db.add(writing_document)
        db.flush()
        writing_version = WritingDocumentVersion(
            tenant_id=release.tenant_id,
            document_id=writing_document.id,
            version=1,
            content=[{"id": "title", "type": "h1", "children": [{"text": "导出约束测试"}]}],
            content_hash=content_hash([{"id": "title", "type": "h1", "children": [{"text": "导出约束测试"}]}]),
            scenario_package_version_id=saved.json()["version"]["id"],
            knowledge_product_release_id=release.id,
            status="draft",
            created_by=release.created_by,
        )
        db.add(writing_version)
        db.flush()
        writing_document.current_version_id = writing_version.id
        db.commit()
        blocked_export = client.post(
            f"/api/v1/writing/documents/{writing_document.id}/exports",
            json={"output_format": "pdf"},
        )
        assert blocked_export.status_code == 409


def test_writing_project_fact_computation_and_local_stale_propagation() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        required_fact = client.post(
            f"/api/v1/writing/projects/{project['id']}/facts",
            json={
                "fact_key": "rescue_required",
                "label": "搜救人员需求",
                "fact_type": "official_brief",
                "value": {"number": 500},
                "unit": "人",
                "source_type": "official_brief",
                "source_id": "brief-v1",
                "verification_status": "verified",
            },
        )
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
                "input_fact_ids": [required_fact.json()["id"], fact.json()["id"]],
                "input_fact_map": {
                    "required": required_fact.json()["id"],
                    "available": fact.json()["id"],
                },
                "output_fact_key": "rescue_gap",
                "output_label": "搜救人员缺口",
                "output_unit": "人",
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
        replacement = impact.json()["replacement_runs"][0]
        assert replacement["status"] == "recomputed"
        assert replacement["result"]["value"] == 100
        current_facts = client.get(f"/api/v1/writing/projects/{project['id']}/facts").json()
        assert next(item for item in current_facts if item["fact_key"] == "rescue_gap")["value"]["number"] == 100


def test_ground_truth_baseline_computations_and_fact_override_are_versioned() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        ground_truth = json.loads(Path("demo/miaobi/earthquake_ground_truth.json").read_text(encoding="utf-8"))["facts"]
        input_keys = [
            "rescue_required", "rescue_available", "trauma_beds_required",
            "county_trauma_beds", "callable_trauma_beds", "tents_required", "tents_available",
        ]
        created = {}
        for key in input_keys:
            item = ground_truth[key]
            response = client.post(
                f"/api/v1/writing/projects/{project['id']}/facts",
                json={
                    "fact_key": key,
                    "label": key,
                    "fact_type": "official_brief",
                    "value": {"number": item["value"]},
                    "unit": item["unit"],
                    "source_type": "official_brief",
                    "source_id": "customer-ground-truth",
                    "verification_status": "verified",
                },
            )
            assert response.status_code == 200, response.text
            created[key] = response.json()
        baseline = client.post(f"/api/v1/writing/projects/{project['id']}/computations/run-baseline")
        assert baseline.status_code == 200, baseline.text
        outputs = {item["fact"]["fact_key"]: item["fact"]["value"]["number"] for item in baseline.json()["items"]}
        assert outputs == {"rescue_gap": 180, "county_bed_gap": 220, "all_area_bed_gap": 80, "tents_gap": 1800}

        overridden = client.post(
            f"/api/v1/writing/projects/{project['id']}/facts/{created['rescue_available']['id']}/confirm",
            json={"decision": "override", "new_value": {"number": 400}, "reason": "现场资源更新"},
        )
        assert overridden.status_code == 200, overridden.text
        assert overridden.json()["version"] == 2
        assert overridden.json()["value"]["number"] == 400
        historical = db.get(ProjectFact, created["rescue_available"]["id"])
        assert historical is not None and historical.active is False
        assert historical.value["number"] == 320


def test_input_change_preview_is_non_mutating_and_apply_updates_only_dependent_report_content() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        values = {
            "event_name": ("事件名称", {"text": "积石山县6.2级地震"}, None),
            "magnitude": ("地震震级", {"number": 6.2}, "级"),
            "population_density": ("人口密度", {"number": 305.6}, "人/km²"),
            "rescue_required": ("搜救人员需求", {"number": 500}, "人"),
            "rescue_available": ("可用搜救人员", {"number": 320}, "人"),
            "trauma_beds_required": ("创伤床位需求", {"number": 330}, "张"),
            "county_trauma_beds": ("县域可用床位", {"number": 110}, "张"),
            "callable_trauma_beds": ("全域可调床位", {"number": 250}, "张"),
            "tents_required": ("帐篷需求", {"number": 7000}, "顶"),
            "tents_available": ("可用帐篷", {"number": 5200}, "顶"),
        }
        fact_ids = {}
        for key, (label, value, unit) in values.items():
            created = client.post(
                f"/api/v1/writing/projects/{project['id']}/facts",
                json={
                    "fact_key": key,
                    "label": label,
                    "fact_type": "official_brief",
                    "value": value,
                    "unit": unit,
                    "source_type": "official_brief",
                    "source_id": "customer-ground-truth",
                    "verification_status": "verified",
                },
            )
            assert created.status_code == 200, created.text
            fact_ids[key] = created.json()["id"]
        baseline = client.post(f"/api/v1/writing/projects/{project['id']}/computations/run-baseline")
        assert baseline.status_code == 200, baseline.text
        rescue_item = next(item for item in baseline.json()["items"] if item["fact"]["fact_key"] == "rescue_gap")
        run = rescue_item["run"]
        metric = {
            "id": "rescue-gap-block",
            "type": "computed_metric",
            "label": "搜救人员缺口",
            "value": 180,
            "unit": "人",
            "formula": "resource_gap",
            "dependencies": {"required": "rescue_required", "available": "rescue_available"},
            "computation_run_id": run["id"],
            "evidence_ids": [fact_ids["rescue_required"], fact_ids["rescue_available"]],
            "freshness_status": "current",
            "children": [{"text": "经核验与测算，搜救人员缺口为180人。"}],
        }
        untouched = {"id": "untouched", "type": "p", "children": [{"text": "组织体系保持不变。"}]}
        document = client.post(
            "/api/v1/writing/documents",
            json={"project_id": project["id"], "title": "影响更新测试", "content": [metric, untouched]},
        ).json()
        bound = client.post(
            f"/api/v1/writing/documents/{document['id']}/bindings",
            json={
                "block_id": metric["id"],
                "block_type": "computed_metric",
                "source_type": "computation",
                "computation_run_id": run["id"],
                "content_hash": content_hash(metric),
                "block_content": metric,
                "evidence_ids": metric["evidence_ids"],
                "verification_status": "verified",
            },
        )
        assert bound.status_code == 200, bound.text

        preview = client.post(
            f"/api/v1/writing/projects/{project['id']}/input-changes/preview",
            json={
                "document_id": document["id"],
                "changes": [{"fact_key": "rescue_available", "new_value": {"number": 400}, "reason": "现场资源更新"}],
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["impact"]["calculations"] == [
            {
                "previous_run_id": run["id"],
                "result_key": "rescue_gap",
                "label": "搜救人员缺口",
                "unit": "人",
                "old_value": 180,
                "new_value": 100,
                "formula": "max(0, required - available)",
                "dependencies": {"required": "rescue_required", "available": "rescue_available"},
            }
        ]
        before_apply = client.get(f"/api/v1/writing/projects/{project['id']}/facts").json()
        assert next(item for item in before_apply if item["fact_key"] == "rescue_available")["value"]["number"] == 320

        applied = client.post(
            f"/api/v1/writing/projects/{project['id']}/input-changes/apply",
            json={"preview_id": preview.json()["id"]},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["status"] == "applied"
        after_apply = client.get(f"/api/v1/writing/projects/{project['id']}/facts").json()
        assert next(item for item in after_apply if item["fact_key"] == "rescue_available")["value"]["number"] == 400
        current = client.get(f"/api/v1/writing/documents/{document['id']}").json()["current_version"]
        assert current["version"] == 2
        updated_metric = next(item for item in current["content"] if item["id"] == metric["id"])
        assert updated_metric["value"] == 100
        assert next(item for item in current["content"] if item["id"] == "untouched") == untouched
        old = db.get(WritingDocumentVersion, document["current_version"]["id"])
        assert next(item for item in old.content if item["id"] == metric["id"])["value"] == 180

        # Simulate an older production document whose binding still points to
        # a previous immutable run. Impact detection must follow the logical
        # result key instead of hiding the affected report block.
        historical_binding = db.query(WritingBlockBinding).filter_by(
            document_id=document["id"], block_id=metric["id"]
        ).one()
        historical_binding.computation_run_id = run["id"]
        db.commit()

        restored_preview = client.post(
            f"/api/v1/writing/projects/{project['id']}/input-changes/preview",
            json={
                "document_id": document["id"],
                "changes": [{"fact_key": "rescue_available", "new_value": {"number": 320}, "reason": "恢复原始资源数"}],
            },
        )
        assert restored_preview.status_code == 200, restored_preview.text
        restored_impact = restored_preview.json()["impact"]
        assert restored_impact["calculations"][0]["old_value"] == 100
        assert restored_impact["calculations"][0]["new_value"] == 180
        assert restored_impact["report_blocks"] == [{
            "block_id": "rescue-gap-block",
            "section": "报告正文",
            "impact_type": "definite",
            "dependency_reasons": ["依赖重新计算结果"],
        }]
        restored = client.post(
            f"/api/v1/writing/projects/{project['id']}/input-changes/apply",
            json={"preview_id": restored_preview.json()["id"]},
        )
        assert restored.status_code == 200, restored.text
        restored_current = client.get(f"/api/v1/writing/documents/{document['id']}").json()["current_version"]
        assert next(item for item in restored_current["content"] if item["id"] == metric["id"])["value"] == 180
        restored_facts = client.get(f"/api/v1/writing/projects/{project['id']}/facts").json()
        assert next(item for item in restored_facts if item["fact_key"] == "rescue_gap")["value"]["number"] == 180

        second_preview = client.post(
            f"/api/v1/writing/projects/{project['id']}/input-changes/preview",
            json={
                "document_id": document["id"],
                "changes": [{"fact_key": "rescue_available", "new_value": {"number": 400}, "reason": "再次验证当前计算选择"}],
            },
        )
        assert second_preview.status_code == 200, second_preview.text
        second_impact = second_preview.json()["impact"]
        assert second_impact["calculations"][0]["old_value"] == 180
        assert second_impact["calculations"][0]["new_value"] == 100
        assert second_impact["report_blocks"] == [{
            "block_id": "rescue-gap-block",
            "section": "报告正文",
            "impact_type": "definite",
            "dependency_reasons": ["依赖重新计算结果"],
        }]
        second_applied = client.post(
            f"/api/v1/writing/projects/{project['id']}/input-changes/apply",
            json={"preview_id": second_preview.json()["id"]},
        )
        assert second_applied.status_code == 200, second_applied.text
        second_current = client.get(f"/api/v1/writing/documents/{document['id']}").json()["current_version"]
        assert next(item for item in second_current["content"] if item["id"] == metric["id"])["value"] == 100
        second_facts = client.get(f"/api/v1/writing/projects/{project['id']}/facts").json()
        assert next(item for item in second_facts if item["fact_key"] == "rescue_gap")["value"]["number"] == 100


def test_input_change_accepts_bound_paragraphs_individually_without_overwriting_others() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        values = {
            "rescue_required": (500, "搜救人员需求"), "rescue_available": (320, "可用搜救人员"),
            "trauma_beds_required": (330, "创伤床位需求"), "county_trauma_beds": (110, "县域床位"),
            "callable_trauma_beds": (250, "全域床位"), "tents_required": (7000, "帐篷需求"),
            "tents_available": (5200, "可用帐篷"),
        }
        fact_ids = {}
        for key, (number, label) in values.items():
            response = client.post(f"/api/v1/writing/projects/{project['id']}/facts", json={
                "fact_key": key, "label": label, "fact_type": "official_brief", "value": {"number": number},
                "unit": "人" if key.startswith("rescue") else None, "source_type": "official_brief",
                "source_id": "test-source", "verification_status": "verified",
            })
            assert response.status_code == 200, response.text
            fact_ids[key] = response.json()["id"]
        baseline = client.post(f"/api/v1/writing/projects/{project['id']}/computations/run-baseline")
        assert baseline.status_code == 200, baseline.text
        run = next(item["run"] for item in baseline.json()["items"] if item["fact"]["fact_key"] == "rescue_gap")
        metric = {"id": "metric-gap", "type": "computed_metric", "label": "搜救人员缺口", "value": 180,
                  "unit": "人", "computation_run_id": run["id"],
                  "children": [{"text": "经核验与测算，搜救人员缺口为180人。"}]}
        paragraph = {"id": "paragraph-resource", "type": "p", "children": [{"text": "可用搜救人员320人，缺口180人。"}]}
        summary = {"id": "paragraph-summary", "type": "p", "children": [{"text": "当前缺口180人，需进一步协调。"}]}
        unbound = {"id": "paragraph-unbound", "type": "p", "children": [{"text": "其他工作保持不变。"}]}
        document_response = client.post("/api/v1/writing/documents", json={
            "project_id": project["id"], "title": "逐项接受测试", "content": [metric, paragraph, summary, unbound],
        })
        assert document_response.status_code == 200, document_response.text
        document = document_response.json()
        for node, metadata, run_id, fact_id in (
            (metric, {}, run["id"], None),
            (paragraph, {"input_keys": ["rescue_available"], "metric_keys": ["rescue_gap"],
                         "input_fact_ids": [fact_ids["rescue_available"]], "computation_run_ids": [run["id"]]}, run["id"], fact_ids["rescue_available"]),
            (summary, {"metric_keys": ["rescue_gap"], "computation_run_ids": [run["id"]]}, run["id"], None),
        ):
            bound = client.post(f"/api/v1/writing/documents/{document['id']}/bindings", json={
                "block_id": node["id"], "block_type": node["type"],
                "source_type": "computation" if run_id else "model_extraction",
                "computation_run_id": run_id, "fact_id": fact_id,
                "content_hash": content_hash(node), "block_content": node,
                "verification_status": "verified", "metadata": metadata,
            })
            assert bound.status_code == 200, bound.text
        formal_chunks = client.get(
            f"/api/v1/writing/documents/{document['id']}/chunks"
        )
        assert formal_chunks.status_code == 200, formal_chunks.text
        formal_by_id = {item["chunk_id"]: item for item in formal_chunks.json()}
        paragraph_dependencies = {
            (item["binding_type"], item["binding_id"])
            for item in formal_by_id["paragraph-resource"]["dependencies"]
        }
        assert ("project_fact", fact_ids["rescue_available"]) in paragraph_dependencies
        assert ("computation_run", run["id"]) in paragraph_dependencies
        assert {
            item["binding_type"] for item in formal_by_id["paragraph-summary"]["dependencies"]
        } == {"computation_run"}
        preview_response = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/preview", json={
            "document_id": document["id"],
            "changes": [{"fact_key": "rescue_available", "new_value": {"number": 400}, "reason": "资源清点更新"}],
        })
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        proposals = {item["block_id"]: item for item in preview["impact"]["content_proposals"]}
        assert proposals["metric-gap"]["new_text"].endswith("100人。")
        assert proposals["paragraph-resource"]["new_text"] == "可用搜救人员400人，缺口100人。"
        assert proposals["paragraph-summary"]["new_text"] == "当前缺口100人，需进一步协调。"
        assert proposals["paragraph-resource"]["impact_type"] == "definite"
        assert proposals["paragraph-resource"]["dependency_reasons"] == [
            "依赖重新计算结果", "直接引用变更事实",
        ]
        assert "paragraph-unbound" not in proposals
        invalid = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/apply", json={
            "preview_id": preview["id"], "accepted_block_ids": ["paragraph-unbound"],
        })
        assert invalid.status_code == 422
        applied = client.post(f"/api/v1/writing/projects/{project['id']}/input-changes/apply", json={
            "preview_id": preview["id"], "accepted_block_ids": ["metric-gap", "paragraph-resource"],
        })
        assert applied.status_code == 200, applied.text
        current = client.get(f"/api/v1/writing/documents/{document['id']}").json()["current_version"]
        by_id = {node["id"]: node for node in current["content"]}
        assert by_id["metric-gap"]["value"] == 100
        assert by_id["paragraph-resource"]["children"][0]["text"] == "可用搜救人员400人，缺口100人。"
        assert by_id["paragraph-summary"]["children"][0]["text"] == summary["children"][0]["text"]
        assert by_id["paragraph-summary"]["freshness_status"] == "stale"
        assert by_id["paragraph-unbound"] == unbound
        assert applied.json()["impact"]["pending_review_block_ids"] == ["paragraph-summary"]
        current_chunks = client.get(
            f"/api/v1/writing/documents/{document['id']}/chunks"
        ).json()
        current_chunk_by_id = {item["chunk_id"]: item for item in current_chunks}
        assert current_chunk_by_id["paragraph-resource"]["freshness_status"] == "current"
        assert current_chunk_by_id["paragraph-summary"]["freshness_status"] == "stale"
        assert any(
            item["binding_type"] == "project_fact"
            and item["binding_id"] != fact_ids["rescue_available"]
            for item in current_chunk_by_id["paragraph-resource"]["dependencies"]
        )
        old = db.get(WritingDocumentVersion, document["current_version"]["id"])
        assert next(node for node in old.content if node["id"] == "paragraph-resource") == paragraph
        old_chunks = client.get(
            f"/api/v1/writing/documents/{document['id']}/chunks",
            params={"version_id": old.id},
        ).json()
        assert next(item for item in old_chunks if item["chunk_id"] == "paragraph-resource")[
            "content"
        ] == paragraph


def test_real_customer_sample_profile_is_article_scoped_and_never_factual_evidence(monkeypatch) -> None:
    sample_path = Path("/Users/tianqi/Desktop/积石山县6.2级地震_本体驱动应急智能推演系统_完整升级版/样稿.pdf")
    if not sample_path.exists():
        pytest.skip("真实客户样稿未挂载")
    sample_bytes = sample_path.read_bytes()
    monkeypatch.setattr("apps.api.writing.object_storage.get_bytes", lambda key: sample_bytes)
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        document_response = client.post("/api/v1/writing/documents", json={
            "project_id": project["id"], "title": "新地区地震应急预案（讨论稿）", "document_type": "emergency_plan",
            "audience": "项目组业务审阅", "purpose": "依据新地区已确认资料形成讨论稿", "content": [],
        })
        assert document_response.status_code == 200, document_response.text
        article = document_response.json()
        space_id = db.scalar(select(KnowledgeProductReleaseItem.space_id).where(KnowledgeProductReleaseItem.product_release_id == release.id))
        user = db.scalar(select(User).where(User.tenant_id == project["tenant_id"]))
        sample_source = Document(tenant_id=project["tenant_id"], space_id=space_id,
                                 title="客户样稿", owner_id=user.id, status="ready")
        db.add(sample_source); db.flush()
        sample_version = DocumentVersion(
            tenant_id=project["tenant_id"], document_id=sample_source.id, version_number=1,
            filename="样稿.pdf", content_type="application/pdf", size=len(sample_bytes),
            sha256=hashlib.sha256(sample_bytes).hexdigest(), object_key="test/sample.pdf", status="processed",
        )
        db.add(sample_version); db.flush()
        sample_source.current_version_id = sample_version.id
        sample_material = WritingProjectMaterial(
            tenant_id=project["tenant_id"], project_id=project["id"], document_id=sample_source.id,
            version_id=sample_version.id, material_role="sample_style", status="active", added_by=user.id,
        )
        db.add(sample_material); db.commit()
        preview = client.post(f"/api/v1/writing/documents/{article['id']}/sample-profile/preview", json={
            "material_id": sample_material.id,
        })
        assert preview.status_code == 200, preview.text
        profile = preview.json()["profile"]
        assert [item["title"] for item in profile["chapters"]] == [
            "总则", "组织体系", "运行机制", "应急保障", "其他地震事件应急", "监督管理", "附则",
        ]
        assert profile["style"]["sample_is_not_factual_evidence"] is True
        assert profile["formula_candidates"] == []
        assert client.get(f"/api/v1/writing/documents/{article['id']}").json()["applicability"].get("sample_profile") is None
        applied = client.put(f"/api/v1/writing/documents/{article['id']}/sample-profile", json={
            "material_id": sample_material.id, "profile": profile,
        })
        assert applied.status_code == 200, applied.text
        assert applied.json()["profile"]["status"] == "confirmed"
        assert client.get(f"/api/v1/writing/documents/{article['id']}").json()["applicability"]["sample_profile"]["profile_hash"]
        sample_only = client.post(f"/api/v1/writing/projects/{project['id']}/generate-report", json={"document_id": article["id"]})
        assert sample_only.status_code == 409, sample_only.text
        assert "样稿只提供结构和文风" in sample_only.text


def test_project_knowledge_search_is_locked_to_product_release(monkeypatch) -> None:
    captured = {}

    def fake_search(_db, **kwargs):
        captured.update(kwargs)
        return {
            "query_id": "query-run",
            "normalized_query": kwargs["query"],
            "items": [],
            "channel_counts": {"keyword": 0, "vector": 0, "graph": 0},
            "warnings": [],
            "trace_summary": {},
        }

    monkeypatch.setattr("apps.api.writing.execute_hybrid_search", fake_search)
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        response = client.post(
            f"/api/v1/writing/projects/{project['id']}/knowledge/search",
            json={"query": "地震应急响应等级", "use_reranker": False},
        )
        assert response.status_code == 200, response.text
        assert response.json()["snapshot_locked"] is True
        assert captured["knowledge_release_ids"]
        assert captured["retrieval_context"] == {
            "writing_project_id": project["id"],
            "knowledge_product_release_id": release.id,
            "material_document_ids": [],
            "allowed_document_ids": [],
            "retrieval_scope": "knowledge_product_release",
        }


def test_project_knowledge_search_rejects_all_channels_disabled() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        response = client.post(
            f"/api/v1/writing/projects/{project['id']}/knowledge/search",
            json={
                "query": "地震应急响应等级",
                "use_keyword": False,
                "use_vector": False,
                "use_graph": False,
            },
        )
        assert response.status_code == 422


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
        assert client.get(f"/api/v1/writing/projects/{project['id']}").json()["status"] == "ready"
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
                            {"to": "综合通道", "minutes": 17.5, "risk": 3},
                        ],
                        "快速通道": [{"to": "震中", "minutes": 10, "risk": 7}],
                        "安全通道": [{"to": "震中", "minutes": 24, "risk": 1}],
                        "综合通道": [{"to": "震中", "minutes": 17.5, "risk": 3}],
                    },
                    "resources": [{"name": "搜救人员", "unit": "人", "required": 500, "available": 320}],
                },
            },
        )
        assert response.status_code == 200, response.text
        plans = response.json()
        assert len(plans) == 3
        assert len({tuple(item["result"]["route"]["path"]) for item in plans}) == 3
        assert all(item["unresolved_gaps"][0]["gap"] == 180 for item in plans)


def test_writing_agent_session_is_idempotent_and_release_scoped() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        document = client.post(
            "/api/v1/writing/documents",
            json={"project_id": project["id"], "title": "助手测试文稿", "content": []},
        ).json()
        first = client.post(
            f"/api/v1/writing/projects/{project['id']}/agent-sessions",
            json={"document_id": document["id"]},
        )
        assert first.status_code == 200, first.text
        second = client.post(
            f"/api/v1/writing/projects/{project['id']}/agent-sessions",
            json={"document_id": document["id"]},
        )
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]
        fresh = client.post(
            f"/api/v1/writing/projects/{project['id']}/agent-sessions",
            json={"document_id": document["id"], "start_new": True},
        )
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["id"] != first.json()["id"]
        assert db.get(WritingAgentSession, first.json()["id"]).status == "archived"
        assert fresh.json()["conversation"]["settings"]["knowledge_product_release_id"] == release.id
        session = db.get(WritingAgentSession, fresh.json()["id"])
        conversation = db.get(Conversation, fresh.json()["conversation_id"])
        assert session.purpose == "editing"
        assert session.harness_session_id == conversation.harness_session_id
        assert conversation.settings["kind"] == "writing"
        assert conversation.settings["knowledge_product_release_id"] == release.id
        assert conversation.settings["knowledge_release_ids"]
        claims = {
            "conversation_id": conversation.id,
            "harness_session_id": conversation.harness_session_id,
            "tenant_id": conversation.tenant_id,
            "sub": conversation.user_id,
            "space_ids": conversation.settings["space_ids"],
        }
        # The production gateway signs a scoped credential. A manually built
        # token without that scope must fail, even when its user has access.
        for restricted in ({k: v for k, v in claims.items() if k != "space_ids"}, {**claims, "space_ids": []}):
            with pytest.raises(HTTPException) as denied:
                agent_writing_context(AgentWritingRequest(conversation_id=conversation.id), claims=restricted, db=db)
            assert denied.value.status_code == 403
        context = agent_writing_context(
            AgentWritingRequest(conversation_id=conversation.id), claims=claims, db=db
        )
        assert context["project"]["knowledge_product_release_id"] == release.id
        outline = agent_writing_document_outline(
            AgentWritingRequest(conversation_id=conversation.id), claims=claims, db=db
        )
        assert outline["document_title"] == "助手测试文稿"


def test_parallel_writing_tools_keep_overlapping_short_lived_credentials_valid() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        document = client.post(
            "/api/v1/writing/documents",
            json={"project_id": project["id"], "title": "并行工具凭据测试", "content": []},
        ).json()
        session_payload = client.post(
            f"/api/v1/writing/projects/{project['id']}/agent-sessions",
            json={"document_id": document["id"], "purpose": "report_generation"},
        ).json()
        request = AgentCredentialRequest(
            harness_session_id=session_payload["harness_session_id"],
        )

        first = issue_credential(request, _=None, db=db)
        second = issue_credential(request, _=None, db=db)

        assert first["access_token"] != second["access_token"]
        active = list(db.scalars(select(AgentCredential).where(
            AgentCredential.conversation_id == session_payload["conversation_id"],
            AgentCredential.revoked_at.is_(None),
        )))
        assert len(active) == 2


def test_report_generation_session_is_isolated_from_editor_assistant() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        document = client.post(
            "/api/v1/writing/documents",
            json={"project_id": project["id"], "title": "会话隔离测试", "content": []},
        ).json()
        report = client.post(
            f"/api/v1/writing/projects/{project['id']}/agent-sessions",
            json={
                "document_id": document["id"],
                "start_new": True,
                "purpose": "report_generation",
            },
        )
        assert report.status_code == 200, report.text
        editor = client.post(
            f"/api/v1/writing/projects/{project['id']}/agent-sessions",
            json={"document_id": document["id"]},
        )
        assert editor.status_code == 200, editor.text
        assert editor.json()["id"] != report.json()["id"]
        assert db.get(WritingAgentSession, report.json()["id"]).purpose == "report_generation"
        assert db.get(WritingAgentSession, editor.json()["id"]).purpose == "editing"
        listed = client.get(f"/api/v1/writing/projects/{project['id']}/agent-sessions")
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == [editor.json()["id"]]


def test_project_earthquake_reasoning_uses_deterministic_criteria_and_semantica() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        for key, label, value, unit in [
            ("event_name", "事件名称", {"text": "积石山县6.2级地震"}, None),
            ("magnitude", "震级", {"number": 6.2}, "级"),
            ("population_density", "人口密度", {"number": 305.6}, "人/km²"),
        ]:
            response = client.post(
                f"/api/v1/writing/projects/{project['id']}/facts",
                json={
                    "fact_key": key,
                    "label": label,
                    "fact_type": "official_brief",
                    "value": value,
                    "unit": unit,
                    "source_type": "official_brief",
                    "source_id": "brief-ground-truth",
                    "verification_status": "verified",
                },
            )
            assert response.status_code == 200, response.text
        criteria = client.post(f"/api/v1/writing/projects/{project['id']}/criteria/evaluate")
        assert criteria.status_code == 200, criteria.text
        assert all(item["value"]["boolean"] for item in criteria.json()["items"])
        reasoning = client.post(
            f"/api/v1/writing/projects/{project['id']}/reason", json={"mode": "preview"}
        )
        assert reasoning.status_code == 200, reasoning.text
        assert reasoning.json()["run"]["engine"] == "semantica-datalog"
        conclusion = reasoning.json()["conclusions"][0]
        assert conclusion["value"]["text"] == "重大地震灾害（Ⅱ级）"
        assert conclusion["verification_status"] == "unverified"
        assert reasoning.json()["requires_human_confirmation"] is True
        document = client.post(
            "/api/v1/writing/documents",
            json={
                "project_id": project["id"],
                "title": "推演绑定测试文稿",
                "content": [{"id": "inference-block", "type": "inference_conclusion", "children": [{"text": "灾害等级：重大地震灾害（Ⅱ级）"}]}],
            },
        )
        assert document.status_code == 200, document.text
        unconfirmed = client.post(
            f"/api/v1/writing/documents/{document.json()['id']}/bindings",
            json={
                "block_id": "inference-block",
                "block_type": "inference_conclusion",
                "source_type": "semantica_inference",
                "fact_id": conclusion["id"],
                "content_hash": "d" * 64,
                "verification_status": "verified",
            },
        )
        assert unconfirmed.status_code == 409
        confirmed = client.post(
            f"/api/v1/writing/projects/{project['id']}/facts/{conclusion['id']}/confirm",
            json={"decision": "confirm", "reason": "已核验推演前提与规则"},
        )
        assert confirmed.status_code == 200, confirmed.text
        binding = client.post(
            f"/api/v1/writing/documents/{document.json()['id']}/bindings",
            json={
                "block_id": "inference-block",
                "block_type": "inference_conclusion",
                "source_type": "semantica_inference",
                "source_id": confirmed.json()["source_id"],
                "fact_id": confirmed.json()["id"],
                "evidence_ids": [item["source_fact_id"] for item in confirmed.json()["source_locator"]["evidence"]],
                "content_hash": "d" * 64,
                "verification_status": "verified",
            },
        )
        assert binding.status_code == 200, binding.text
        assert binding.json()["fact_id"] == confirmed.json()["id"]


def test_collaboration_token_is_short_lived_and_room_scoped(monkeypatch) -> None:
    secret = "collaboration-test-secret-at-least-thirty-two-bytes"
    monkeypatch.setattr("packages.platform.security._collaboration_secret", lambda: secret)
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        document = client.post(
            "/api/v1/writing/documents",
            json={
                "project_id": project["id"],
                "title": "协同测试文稿",
                "content": [{"id": "paragraph-1", "type": "p", "children": [{"text": "协同正文"}]}],
            },
        ).json()
        response = client.post(f"/api/v1/writing/documents/{document['id']}/collaboration-token")
        assert response.status_code == 200, response.text
        payload = response.json()
        claims = jwt.decode(
            payload["token"], secret, algorithms=["HS256"], audience="miaobi-collaboration"
        )
        assert claims["room"] == payload["room"]
        assert claims["document_id"] == document["id"]
        assert claims["project_id"] == project["id"]
        assert claims["role"] == "owner"
        assert payload["role"] == "owner"
        assert payload["read_only"] is False
        assert claims["exp"] - claims["iat"] == 15 * 60


def test_document_comments_bind_to_real_blocks_and_resolve_threads() -> None:
    with writing_client() as (client, _, release):
        project = _create_project(client, release.id)
        document = client.post(
            "/api/v1/writing/documents",
            json={
                "project_id": project["id"],
                "title": "评论测试文稿",
                "content": [{"id": "paragraph-1", "type": "p", "children": [{"text": "需要复核"}]}],
            },
        ).json()
        invalid = client.post(
            f"/api/v1/writing/documents/{document['id']}/comments",
            json={"block_id": "missing-block", "content": "不存在的正文块"},
        )
        assert invalid.status_code == 422
        root = client.post(
            f"/api/v1/writing/documents/{document['id']}/comments",
            json={"block_id": "paragraph-1", "content": "请复核响应等级"},
        )
        assert root.status_code == 200, root.text
        reply = client.post(
            f"/api/v1/writing/documents/{document['id']}/comments",
            json={"parent_id": root.json()["id"], "content": "已核对来源"},
        )
        assert reply.status_code == 200, reply.text
        assert reply.json()["thread_id"] == root.json()["thread_id"]
        rows = client.get(f"/api/v1/writing/documents/{document['id']}/comments").json()
        assert [row["content"] for row in rows] == ["请复核响应等级", "已核对来源"]
        resolved = client.post(
            f"/api/v1/writing/comments/{root.json()['id']}/resolve",
            json={"resolved": True},
        )
        assert resolved.status_code == 200, resolved.text
        assert {row["status"] for row in resolved.json()} == {"resolved"}
        assert client.get(
            f"/api/v1/writing/documents/{document['id']}/comments?include_resolved=false"
        ).json() == []


def test_collaboration_roles_enforce_read_only_comment_and_review_boundaries(monkeypatch) -> None:
    secret = "collaboration-role-secret-at-least-thirty-two-bytes"
    monkeypatch.setattr("packages.platform.security._collaboration_secret", lambda: secret)
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        document = client.post(
            "/api/v1/writing/documents",
            json={
                "project_id": project["id"],
                "title": "角色权限测试",
                "content": [{"id": "paragraph-1", "type": "p", "children": [{"text": "受控正文"}]}],
            },
        ).json()
        tenant_id = db.get(KnowledgeProductRelease, release.id).tenant_id
        admin_id = db.query(User).filter(User.tenant_id == tenant_id, User.is_admin.is_(True)).one().id
        roles = {}
        for role in ("viewer", "commenter", "editor", "reviewer"):
            user = User(
                tenant_id=tenant_id,
                username=f"writing-{role}",
                password_hash=hash_password("RoleBoundary@123"),
                display_name=f"{role}用户",
                is_admin=False,
                enabled=True,
            )
            db.add(user)
            db.flush()
            db.add(WritingProjectMember(
                tenant_id=tenant_id,
                project_id=project["id"],
                user_id=user.id,
                role=role,
                created_by=admin_id,
            ))
            roles[role] = {"Authorization": f"Bearer {create_access_token(user.id, tenant_id, False)}"}
        stranger = User(
            tenant_id=tenant_id,
            username="writing-stranger",
            password_hash=hash_password("RoleBoundary@123"),
            display_name="无权限用户",
            is_admin=False,
            enabled=True,
        )
        db.add(stranger)
        db.commit()
        stranger_headers = {"Authorization": f"Bearer {create_access_token(stranger.id, tenant_id, False)}"}

        viewer_access = client.post(
            f"/api/v1/writing/documents/{document['id']}/collaboration-token", headers=roles["viewer"]
        )
        assert viewer_access.status_code == 200
        assert viewer_access.json()["read_only"] is True
        assert client.post(
            f"/api/v1/writing/documents/{document['id']}/comments",
            headers=roles["viewer"],
            json={"content": "越权评论"},
        ).status_code == 403

        commenter_access = client.post(
            f"/api/v1/writing/documents/{document['id']}/collaboration-token", headers=roles["commenter"]
        )
        assert commenter_access.status_code == 200
        assert commenter_access.json()["read_only"] is True
        comment = client.post(
            f"/api/v1/writing/documents/{document['id']}/comments",
            headers=roles["commenter"],
            json={"block_id": "paragraph-1", "content": "请复核正文"},
        )
        assert comment.status_code == 200
        assert client.post(
            f"/api/v1/writing/comments/{comment.json()['id']}/resolve",
            headers=roles["commenter"],
            json={"resolved": True},
        ).status_code == 403

        editor_access = client.post(
            f"/api/v1/writing/documents/{document['id']}/collaboration-token", headers=roles["editor"]
        )
        assert editor_access.status_code == 200
        assert editor_access.json()["read_only"] is False
        assert client.post(
            f"/api/v1/writing/comments/{comment.json()['id']}/resolve",
            headers=roles["editor"],
            json={"resolved": True},
        ).status_code == 403
        assert client.post(
            f"/api/v1/writing/comments/{comment.json()['id']}/resolve",
            headers=roles["reviewer"],
            json={"resolved": True},
        ).status_code == 200
        assert client.post(
            f"/api/v1/writing/documents/{document['id']}/collaboration-token", headers=stranger_headers
        ).status_code == 403


def test_document_delete_is_soft_and_releases_the_business_title() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        payload = {
            "project_id": project["id"],
            "title": "可回收文稿",
            "content": [{"id": "paragraph-1", "type": "p", "children": [{"text": "需要保留版本"}]}],
        }
        document = client.post("/api/v1/writing/documents", json=payload).json()
        deleted = client.delete(f"/api/v1/writing/documents/{document['id']}")
        assert deleted.status_code == 200, deleted.text
        assert client.get(f"/api/v1/writing/documents/{document['id']}").status_code == 404
        assert client.get(f"/api/v1/writing/projects/{project['id']}/documents").json() == []
        retained = db.get(WritingDocument, document["id"])
        assert retained is not None and retained.deleted_at is not None
        assert db.get(WritingDocumentVersion, document["current_version"]["id"]) is not None
        recreated = client.post("/api/v1/writing/documents", json=payload)
        assert recreated.status_code == 200, recreated.text


def test_draft_project_can_explicitly_rebase_to_a_new_immutable_knowledge_release() -> None:
    with writing_client() as (client, db, release):
        project = _create_project(client, release.id)
        document = client.post(
            "/api/v1/writing/documents",
            json={
                "project_id": project["id"],
                "title": "知识基线切换",
                "content": [{"id": "paragraph-1", "type": "p", "children": [{"text": "受控正文"}]}],
            },
        ).json()
        old_version = document["current_version"]
        new_release = KnowledgeProductRelease(
            tenant_id=release.tenant_id,
            product_id=release.product_id,
            version=2,
            manifest={},
            checksum="c" * 64,
            status="published",
            created_by=release.created_by,
        )
        db.add(new_release)
        db.flush()
        source_item = db.query(KnowledgeProductReleaseItem).filter(
            KnowledgeProductReleaseItem.product_release_id == release.id
        ).one()
        db.add(
            KnowledgeProductReleaseItem(
                tenant_id=release.tenant_id,
                product_release_id=new_release.id,
                space_id=source_item.space_id,
                knowledge_release_id="knowledge-release-v2",
                checksum="d" * 64,
            )
        )
        db.commit()

        rebased = client.post(
            f"/api/v1/writing/projects/{project['id']}/knowledge-release",
            json={"knowledge_product_release_id": new_release.id, "reason": "采用最新已发布应急知识"},
        )
        assert rebased.status_code == 200, rebased.text
        assert rebased.json()["knowledge_product_release_id"] == new_release.id
        version = client.post(
            f"/api/v1/writing/documents/{document['id']}/versions",
            json={"content": old_version["content"], "change_summary": "更新知识基线"},
        )
        assert version.status_code == 200, version.text
        assert version.json()["version"] == 2
        assert version.json()["knowledge_product_release_id"] == new_release.id
        assert db.get(WritingDocumentVersion, old_version["id"]).knowledge_product_release_id == release.id
