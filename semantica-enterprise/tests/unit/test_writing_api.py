from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.writing import router
from apps.api.agent_internal import agent_writing_context, agent_writing_document_outline
from apps.api.writing_schemas import AgentWritingRequest
from packages.platform.database import Base, get_db
from packages.platform.models import (
    Conversation,
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
    WritingDocument,
    WritingDocumentVersion,
    WritingProjectMember,
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
        assert session.harness_session_id == conversation.harness_session_id
        assert conversation.settings["kind"] == "writing"
        assert conversation.settings["knowledge_product_release_id"] == release.id
        assert conversation.settings["knowledge_release_ids"]
        claims = {
            "conversation_id": conversation.id,
            "harness_session_id": conversation.harness_session_id,
            "tenant_id": conversation.tenant_id,
            "sub": conversation.user_id,
        }
        context = agent_writing_context(
            AgentWritingRequest(conversation_id=conversation.id), claims=claims, db=db
        )
        assert context["project"]["knowledge_product_release_id"] == release.id
        outline = agent_writing_document_outline(
            AgentWritingRequest(conversation_id=conversation.id), claims=claims, db=db
        )
        assert outline["document_title"] == "助手测试文稿"


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
