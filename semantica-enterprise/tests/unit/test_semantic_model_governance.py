from __future__ import annotations

from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.semantic_models import router
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
