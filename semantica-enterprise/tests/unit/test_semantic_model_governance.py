from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.semantic_models import router
from packages.platform.curation import create_decision, rollback_decision
from packages.platform.database import Base, get_db
from packages.platform.models import (
    CanonicalEntity,
    Chunk,
    ChunkPolicy,
    Document,
    DocumentVersion,
    EntityMention,
    ExtractionPolicy,
    ExtractionRun,
    Fact,
    KnowledgeSpace,
    ModelConfig,
    Ontology,
    OntologySuggestion,
    OntologyTerm,
    OntologyVersion,
    Tenant,
    User,
)
from packages.platform.security import create_access_token, hash_password


@contextmanager
def semantic_model_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(code="semantic-model", name="语义模型测试")
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        username="semantic-model-admin",
        password_hash=hash_password("SemanticModel@123"),
        display_name="语义模型管理员",
        is_admin=True,
        enabled=True,
    )
    db.add(admin)
    db.flush()
    space = KnowledgeSpace(
        tenant_id=tenant.id, code="emergency", name="应急知识", owner_id=admin.id, enabled=True
    )
    db.add(space)
    db.flush()
    ontology = Ontology(
        tenant_id=tenant.id,
        space_id=space.id,
        code="emergency-model",
        name="应急语义模型",
        namespace="urn:test:emergency",
        status="draft",
    )
    db.add(ontology)
    db.flush()
    document = Document(
        tenant_id=tenant.id, space_id=space.id, title="应急预案", status="published"
    )
    db.add(document)
    db.flush()
    version = DocumentVersion(
        tenant_id=tenant.id,
        document_id=document.id,
        version_number=1,
        filename="应急预案.md",
        content_type="text/markdown",
        size=128,
        sha256="a" * 64,
        object_key="tests/plan.md",
        status="processed",
    )
    db.add(version)
    db.flush()
    document.current_version_id = version.id
    model = ModelConfig(
        tenant_id=tenant.id,
        name="测试模型",
        model_kind="llm",
        provider="test",
        model_name="test",
        enabled=True,
    )
    chunk_policy = ChunkPolicy(tenant_id=tenant.id, name="测试切片", enabled=True)
    extraction_policy = ExtractionPolicy(
        tenant_id=tenant.id, name="测试抽取", model_config_id=model.id, enabled=True
    )
    db.add_all([model, chunk_policy, extraction_policy])
    db.flush()
    chunk = Chunk(
        tenant_id=tenant.id,
        space_id=space.id,
        document_id=document.id,
        version_id=version.id,
        chunk_policy_id=chunk_policy.id,
        chunk_id="semantic-model-chunk",
        ordinal=0,
        text="东方智造供应应急通信设备。",
        content_hash="b" * 64,
        structural_path="paragraphs/0",
        status="published",
    )
    db.add(chunk)
    db.flush()
    run = ExtractionRun(
        tenant_id=tenant.id,
        space_id=space.id,
        version_id=version.id,
        policy_id=extraction_policy.id,
        model_config_id=model.id,
        status="succeeded",
    )
    db.add(run)
    db.flush()
    mention = EntityMention(
        tenant_id=tenant.id,
        space_id=space.id,
        run_id=run.id,
        chunk_id=chunk.id,
        mention_id="supplier-mention",
        text="东方智造",
        normalized_name="东方智造",
        entity_type="供应商",
        confidence=0.95,
        status="published",
    )
    subject = CanonicalEntity(
        tenant_id=tenant.id,
        space_id=space.id,
        canonical_name="东方智造",
        normalized_name="东方智造",
        entity_type="供应商",
        confidence=0.95,
        status="published",
    )
    product = CanonicalEntity(
        tenant_id=tenant.id,
        space_id=space.id,
        canonical_name="应急通信设备",
        normalized_name="应急通信设备",
        entity_type="设备",
        confidence=0.94,
        status="published",
    )
    db.add_all([mention, subject, product])
    db.flush()
    db.add(
        Fact(
            tenant_id=tenant.id,
            space_id=space.id,
            subject_entity_id=subject.id,
            predicate="供应",
            object_entity_id=product.id,
            source_chunk_id=chunk.id,
            confidence=0.93,
            status="published",
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
        yield client, db, ontology
    db.close()


def test_document_derived_candidates_require_decision_before_version_publish() -> None:
    with semantic_model_client() as (client, db, ontology):
        generated = client.post(f"/api/v1/ontologies/{ontology.id}/suggestions/generate")
        assert generated.status_code == 200, generated.text
        assert generated.json()["candidate_count"] == 3
        suggestions = client.get(f"/api/v1/ontologies/{ontology.id}/suggestions").json()
        assert {item["label"] for item in suggestions} == {"供应商", "设备", "供应"}
        assert all(item["evidence_count"] > 0 for item in suggestions)
        assert any(item["evidence"] for item in suggestions)
        assert db.query(OntologyTerm).filter_by(ontology_id=ontology.id).count() == 0

        for suggestion in suggestions:
            accepted = client.put(
                f"/api/v1/ontologies/{ontology.id}/suggestions/{suggestion['id']}",
                json={"decision": "accept"},
            )
            assert accepted.status_code == 200, accepted.text
        validation = client.post(f"/api/v1/ontologies/{ontology.id}/validate-draft")
        assert validation.status_code == 200, validation.text
        assert validation.json()["valid"] is True

        published = client.post(f"/api/v1/ontologies/{ontology.id}/publish")
        assert published.status_code == 200, published.text
        assert published.json()["published_candidate_count"] == 3
        assert published.json()["term_count"] == 3
        version = db.query(OntologyVersion).filter_by(ontology_id=ontology.id).one()
        assert version.manifest["terms"]
        assert len(version.checksum) == 64
        assert db.get(Ontology, ontology.id).config["current_version_id"] == version.id

        regenerated = client.post(f"/api/v1/ontologies/{ontology.id}/suggestions/generate")
        assert regenerated.status_code == 200
        assert regenerated.json()["candidate_count"] == 0


def _govern(db, ontology, target_type, target_id, field_path, value, operation="override"):
    admin = db.query(User).filter_by(tenant_id=ontology.tenant_id).one()
    decision, _, _ = create_decision(
        db, user=admin, space_id=ontology.space_id, target_type=target_type,
        target_id=target_id, field_path=field_path, operation=operation,
        value=value, scope="space", reason_note="单元测试人工核实",
    )
    db.flush()
    return decision


def _generate(client, ontology):
    response = client.post(f"/api/v1/ontologies/{ontology.id}/suggestions/generate")
    assert response.status_code == 200, response.text
    rows = client.get(f"/api/v1/ontologies/{ontology.id}/suggestions").json()
    return {item["label"]: item for item in rows}


def test_candidates_follow_curated_types_relations_evidence_and_rollback(monkeypatch) -> None:
    monkeypatch.setattr("packages.platform.curation.track_curation_decision", lambda *args, **kwargs: None)
    with semantic_model_client() as (client, db, ontology):
        original = _generate(client, ontology)
        subject = db.query(CanonicalEntity).filter_by(entity_type="供应商").one()
        fact = db.query(Fact).one()
        mention = db.query(EntityMention).one()
        chunk = db.query(Chunk).one()
        document = db.get(Document, chunk.document_id)
        document.status = "ready"
        db.get(DocumentVersion, chunk.version_id).status = "ready"
        replacement = Chunk(
            tenant_id=ontology.tenant_id, space_id=ontology.space_id,
            document_id=document.id, version_id=chunk.version_id,
            chunk_policy_id=chunk.chunk_policy_id, chunk_id="corrected-evidence", ordinal=1,
            text="核实后的原始证据。", content_hash="c" * 64, structural_path="paragraphs/1",
            status="published",
        )
        db.add(replacement)
        db.flush()
        decisions = [
            _govern(db, ontology, "entity", subject.id, "entity_type", "保障单位"),
            _govern(db, ontology, "entity", subject.id, "canonical_name", "东方保障中心"),
            _govern(db, ontology, "fact", fact.id, "predicate", "负责保障"),
            _govern(db, ontology, "fact", fact.id, "source_chunk_id", replacement.id),
            _govern(db, ontology, "chunk", replacement.id, "text", "人工核实的保障关系证据。"),
        ]
        db.commit()

        curated = _generate(client, ontology)
        assert set(curated) == {"保障单位", "设备", "负责保障"}
        evidence = curated["保障单位"]["evidence"][0]
        assert curated["保障单位"]["evidence_count"] == 1
        assert evidence["entity_id"] == subject.id
        assert evidence["mention_id"] == mention.id
        assert evidence["chunk_id"] == chunk.id
        assert evidence["label"] == "东方保障中心"
        assert evidence["entity_type"] == "保障单位"
        assert evidence["field_origins"]["entity_type"] == "manual"
        relation_evidence = curated["负责保障"]["evidence"][0]
        assert relation_evidence["source_id"] == fact.id
        assert relation_evidence["chunk_id"] == replacement.id
        assert relation_evidence["snippet"] == "人工核实的保障关系证据。"
        assert relation_evidence["label"] == "负责保障"
        assert subject.entity_type == "供应商"
        assert fact.predicate == "供应"
        assert fact.source_chunk_id == chunk.id

        admin = db.query(User).filter_by(tenant_id=ontology.tenant_id).one()
        for decision in reversed(decisions):
            rollback_decision(db, user=admin, decision=decision)
        db.commit()
        restored = _generate(client, ontology)
        assert set(restored) == set(original)
        assert restored["供应商"]["id"] == original["供应商"]["id"]
        assert restored["供应"]["id"] == original["供应"]["id"]
        assert restored["供应"]["evidence"][0]["chunk_id"] == chunk.id
        assert restored["供应商"]["evidence"][0]["label"] == "东方智造"


def test_candidates_exclude_merged_entities_and_invalid_effective_endpoints(monkeypatch) -> None:
    monkeypatch.setattr("packages.platform.curation.track_curation_decision", lambda *args, **kwargs: None)
    with semantic_model_client() as (client, db, ontology):
        winner = db.query(CanonicalEntity).filter_by(entity_type="供应商").one()
        product = db.query(CanonicalEntity).filter_by(entity_type="设备").one()
        chunk = db.query(Chunk).one()
        loser = CanonicalEntity(
            tenant_id=ontology.tenant_id, space_id=ontology.space_id,
            canonical_name="东方公司", normalized_name="东方公司", entity_type="供应商",
            confidence=0.9, status="published",
        )
        deleted = CanonicalEntity(
            tenant_id=ontology.tenant_id, space_id=ontology.space_id,
            canonical_name="已删除对象", normalized_name="已删除对象", entity_type="旧类型",
            status="published", deleted_at=datetime.now(timezone.utc),
        )
        db.add_all([loser, deleted])
        db.flush()
        redirected = Fact(
            tenant_id=ontology.tenant_id, space_id=ontology.space_id, subject_entity_id=loser.id,
            predicate="保障", object_entity_id=product.id, source_chunk_id=chunk.id,
            confidence=0.9, status="published",
        )
        invalid_facts = [
            Fact(
                tenant_id=ontology.tenant_id, space_id=ontology.space_id, subject_entity_id=subject_id,
                predicate=predicate, object_entity_id=object_id, confidence=0.9, status="published",
            )
            for subject_id, predicate, object_id in [
                (loser.id, "失效主体", product.id), (winner.id, "失效客体", loser.id),
                (winner.id, "已删除客体", deleted.id), (winner.id, "缺失客体", "missing"),
            ]
        ]
        db.add_all([redirected, *invalid_facts])
        db.flush()
        _govern(db, ontology, "entity", loser.id, "status", "suppressed", "reject")
        _govern(db, ontology, "fact", redirected.id, "subject_entity_id", winner.id)
        _govern(db, ontology, "fact", db.query(Fact).filter_by(predicate="供应").one().id,
                "status", "suppressed", "reject")
        db.commit()

        rows = _generate(client, ontology)
        assert set(rows) == {"供应商", "设备", "保障"}
        assert rows["供应商"]["evidence_count"] == 1
        assert rows["保障"]["evidence_count"] == 1
        assert rows["保障"]["evidence"][0]["subject_entity_id"] == winner.id


def test_candidate_regeneration_preserves_reviewed_decisions_and_scope(monkeypatch) -> None:
    monkeypatch.setattr("packages.platform.curation.track_curation_decision", lambda *args, **kwargs: None)
    with semantic_model_client() as (client, db, ontology):
        original = _generate(client, ontology)
        accepted = client.put(
            f"/api/v1/ontologies/{ontology.id}/suggestions/{original['供应商']['id']}",
            json={"decision": "accept", "label": "人工确认的单位类型"},
        )
        assert accepted.status_code == 200
        rejected = client.put(
            f"/api/v1/ontologies/{ontology.id}/suggestions/{original['设备']['id']}",
            json={"decision": "reject"},
        )
        assert rejected.status_code == 200
        other_space = KnowledgeSpace(
            tenant_id=ontology.tenant_id, code="other-space", name="其他空间", enabled=True,
        )
        db.add(other_space)
        db.flush()
        other_pending = OntologySuggestion(
            tenant_id=ontology.tenant_id, ontology_id=ontology.id, space_id=other_space.id,
            suggestion_kind="class", code="other_pending", label="其他空间候选",
            source_fingerprint="d" * 64, payload={"source": "published_entities"}, status="pending",
        )
        db.add(other_pending)
        for row in db.query(CanonicalEntity).filter_by(space_id=ontology.space_id):
            _govern(db, ontology, "entity", row.id, "status", "suppressed", "reject")
        db.commit()

        rows = _generate(client, ontology)
        assert rows["人工确认的单位类型"]["status"] == "accepted"
        assert rows["设备"]["status"] == "rejected"
        assert "供应" not in rows
        assert rows["其他空间候选"]["status"] == "pending"
        assert other_pending.deleted_at is None


@pytest.mark.parametrize("endpoint", ["subject_entity_id", "object_entity_id"])
def test_candidates_reject_cross_space_curated_endpoints(monkeypatch, endpoint) -> None:
    monkeypatch.setattr("packages.platform.curation.track_curation_decision", lambda *args, **kwargs: None)
    with semantic_model_client() as (client, db, ontology):
        other_space = KnowledgeSpace(
            tenant_id=ontology.tenant_id, code="external", name="其他空间", enabled=True,
        )
        db.add(other_space)
        db.flush()
        other_entity = CanonicalEntity(
            tenant_id=ontology.tenant_id, space_id=other_space.id, canonical_name="范围外",
            normalized_name="范围外", entity_type="范围外类型", status="published",
        )
        db.add(other_entity)
        db.flush()
        _govern(db, ontology, "fact", db.query(Fact).one().id, endpoint, other_entity.id)
        db.commit()
        assert set(_generate(client, ontology)) == {"供应商", "设备"}


def test_candidate_generation_keeps_admin_and_tenant_boundaries() -> None:
    with semantic_model_client() as (client, db, ontology):
        reader = User(
            tenant_id=ontology.tenant_id, username="reader", password_hash="unused",
            display_name="非管理员", is_admin=False, enabled=True,
        )
        tenant = Tenant(code="other-tenant", name="其他租户")
        db.add_all([reader, tenant])
        db.flush()
        other_admin = User(
            tenant_id=tenant.id, username="other-admin", password_hash="unused",
            display_name="其他管理员", is_admin=True, enabled=True,
        )
        other_space = KnowledgeSpace(tenant_id=tenant.id, code="private", name="其他租户空间")
        db.add_all([other_admin, other_space])
        db.commit()
        path = f"/api/v1/ontologies/{ontology.id}/suggestions/generate"
        assert client.post(path, headers={
            "Authorization": f"Bearer {create_access_token(reader.id, reader.tenant_id, False)}",
        }).status_code == 403
        assert client.post(path, headers={
            "Authorization": f"Bearer {create_access_token(other_admin.id, tenant.id, True)}",
        }).status_code == 404
        assert client.post(path, params={"space_id": other_space.id}).status_code == 404
