from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.writing import _select_current_plan_rows, router
from apps.api.agent_internal import agent_writing_context, agent_writing_document_outline
from apps.api.writing_schemas import AgentWritingRequest
from packages.platform.database import Base, get_db
from packages.platform.models import (
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
        assert restored_impact["report_blocks"] == [{"block_id": "rescue-gap-block", "section": "报告正文"}]
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
        assert second_impact["report_blocks"] == [{"block_id": "rescue-gap-block", "section": "报告正文"}]
        second_applied = client.post(
            f"/api/v1/writing/projects/{project['id']}/input-changes/apply",
            json={"preview_id": second_preview.json()["id"]},
        )
        assert second_applied.status_code == 200, second_applied.text
        second_current = client.get(f"/api/v1/writing/documents/{document['id']}").json()["current_version"]
        assert next(item for item in second_current["content"] if item["id"] == metric["id"])["value"] == 100
        second_facts = client.get(f"/api/v1/writing/projects/{project['id']}/facts").json()
        assert next(item for item in second_facts if item["fact_key"] == "rescue_gap")["value"]["number"] == 100


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
