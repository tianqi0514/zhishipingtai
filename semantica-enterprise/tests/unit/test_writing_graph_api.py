from __future__ import annotations

from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.writing_graph import router
from packages.platform.database import Base, get_db
from packages.platform.models import (
    Document,
    DocumentVersion,
    KnowledgeSpace,
    Tenant,
    User,
    WritingClaim,
    WritingEntityCandidate,
    WritingEvidence,
    WritingExtractionRun,
    WritingFact,
    WritingRelation,
)
from packages.platform.security import create_access_token, hash_password


@contextmanager
def writing_graph_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(code="writing-graph", name="写作图谱测试")
    db.add(tenant); db.flush()
    owner = User(
        tenant_id=tenant.id, username="graph-owner",
        password_hash=hash_password("WritingGraph@123"), display_name="治理人员",
        is_admin=False, enabled=True,
    )
    stranger = User(
        tenant_id=tenant.id, username="graph-stranger",
        password_hash=hash_password("WritingGraph@123"), display_name="无权人员",
        is_admin=False, enabled=True,
    )
    db.add_all([owner, stranger]); db.flush()
    space = KnowledgeSpace(
        tenant_id=tenant.id, code="writing-space", name="写作验收空间",
        owner_id=owner.id, enabled=True,
    )
    db.add(space); db.flush()
    document = Document(
        tenant_id=tenant.id, space_id=space.id, title="业务材料.md", owner_id=owner.id,
    )
    db.add(document); db.flush()
    version = DocumentVersion(
        tenant_id=tenant.id, document_id=document.id, version_number=1,
        filename="业务材料.md", content_type="text/markdown", size=32,
        sha256="1" * 64, object_key="test/business.md", status="ready",
    )
    db.add(version); db.flush(); document.current_version_id = version.id
    evidence = WritingEvidence(
        tenant_id=tenant.id, space_id=space.id, document_id=document.id,
        document_version_id=version.id, evidence_key="2" * 64,
        filename=version.filename, file_version=1, locator={"page": 1},
        text="东方智造供应 NexusOne。", content_hash="3" * 64,
    )
    db.add(evidence); db.flush()
    run = WritingExtractionRun(
        tenant_id=tenant.id, space_id=space.id, document_version_id=version.id,
        batch_key="4" * 64, evidence_ids=[evidence.id], status="succeeded",
    )
    db.add(run); db.flush()
    supplier = WritingEntityCandidate(
        tenant_id=tenant.id, space_id=space.id, extraction_run_id=run.id,
        candidate_key="supplier", entity_type="供应商", canonical_name="东方智造",
        normalized_name="东方智造", mention_text="东方智造", evidence_ids=[evidence.id],
        confidence=0.99, verification_status="verified",
    )
    product = WritingEntityCandidate(
        tenant_id=tenant.id, space_id=space.id, extraction_run_id=run.id,
        candidate_key="product", entity_type="产品", canonical_name="NexusOne",
        normalized_name="nexusone", mention_text="NexusOne", evidence_ids=[evidence.id],
        confidence=0.99, verification_status="verified",
    )
    db.add_all([supplier, product]); db.flush()
    claim = WritingClaim(
        tenant_id=tenant.id, space_id=space.id, extraction_run_id=run.id,
        document_version_id=version.id, claim_key="claim", subject={"name": "东方智造"},
        predicate="供应", object_value={"value": "NexusOne"}, claim_type="assertion",
        evidence_ids=[evidence.id], confidence=0.99, verification_status="verified",
    )
    db.add(claim); db.flush()
    fact = WritingFact(
        tenant_id=tenant.id, space_id=space.id, fact_key="supplier-product",
        subject={"name": "东方智造"}, subject_candidate_id=supplier.id,
        predicate="供应", object_value={"value": "NexusOne"},
        object_candidate_id=product.id, value_type="entity", claim_ids=[claim.id],
        evidence_ids=[evidence.id], verification_status="verified", verified_by=owner.id,
        version=1,
    )
    db.add(fact); db.flush()
    relation = WritingRelation(
        tenant_id=tenant.id, space_id=space.id, subject_candidate_id=supplier.id,
        predicate="供应", object_candidate_id=product.id, fact_id=fact.id,
        claim_ids=[claim.id], evidence_ids=[evidence.id], verification_status="verified",
        version=1,
    )
    db.add(relation); db.commit()

    app = FastAPI(); app.include_router(router, prefix="/api/v1")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    owner_token = create_access_token(owner.id, tenant.id, False)
    stranger_token = create_access_token(stranger.id, tenant.id, False)
    with TestClient(app, headers={"Authorization": f"Bearer {owner_token}"}) as client:
        yield client, db, space, fact, owner_token, stranger_token
    db.close()


def test_governance_release_and_two_graph_views_are_real() -> None:
    with writing_graph_client() as (client, _db, space, fact, _owner, _stranger):
        summary = client.get(
            "/api/v1/writing-graph/governance/summary", params={"space_id": space.id},
        )
        assert summary.status_code == 200, summary.text
        assert summary.json()["counts"]["fact"]["verified"] == 1

        detail = client.get(f"/api/v1/writing-graph/governance/items/fact/{fact.id}")
        assert detail.status_code == 200
        assert detail.json()["evidence"][0]["text"] == "东方智造供应 NexusOne。"

        published = client.post(
            "/api/v1/writing-graph/releases", json={"space_id": space.id},
        )
        assert published.status_code == 200, published.text
        release_id = published.json()["id"]
        assert published.json()["counts"]["facts"] == 1

        business = client.get(f"/api/v1/writing-graph/releases/{release_id}/graph")
        assert business.status_code == 200
        assert len(business.json()["nodes"]) == 2
        assert business.json()["edges"][0]["label"] == "供应"

        evidence = client.get(
            f"/api/v1/writing-graph/releases/{release_id}/graph", params={"view": "evidence"},
        )
        assert evidence.status_code == 200
        assert {item["type"] for item in evidence.json()["nodes"]} >= {
            "evidence", "claim", "fact", "entity", "relation",
        }


def test_writing_graph_endpoints_enforce_space_permission() -> None:
    with writing_graph_client() as (client, _db, space, _fact, _owner, stranger):
        response = client.get(
            "/api/v1/writing-graph/governance/summary",
            params={"space_id": space.id},
            headers={"Authorization": f"Bearer {stranger}"},
        )
        assert response.status_code == 403
