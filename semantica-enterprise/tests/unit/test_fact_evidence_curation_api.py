from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.routes import router as platform_router
from packages.platform.curation import effective_fact
from packages.platform.database import Base, get_db
from packages.platform.models import (
    AuditEvent,
    CanonicalEntity,
    Chunk,
    ChunkPolicy,
    CurationDecision,
    Document,
    DocumentVersion,
    Fact,
    GraphRelease,
    IndexRelease,
    KnowledgeRelease,
    KnowledgeSpace,
    ModelConfig,
    SpaceGrant,
    Tenant,
    User,
)
from packages.platform.security import create_access_token, hash_password


@contextmanager
def fact_evidence_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(code="fact-evidence", name="事实证据测试")
    other_tenant = Tenant(code="fact-evidence-other", name="其他租户")
    db.add_all([tenant, other_tenant])
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        username="fact-admin",
        password_hash=hash_password("FactEvidence@123"),
        display_name="事实管理员",
        is_admin=True,
        enabled=True,
    )
    reader = User(
        tenant_id=tenant.id,
        username="fact-reader",
        password_hash=hash_password("FactEvidence@123"),
        display_name="事实只读用户",
        enabled=True,
    )
    other_admin = User(
        tenant_id=other_tenant.id,
        username="other-fact-admin",
        password_hash=hash_password("FactEvidence@123"),
        display_name="其他租户管理员",
        is_admin=True,
        enabled=True,
    )
    db.add_all([admin, reader, other_admin])
    db.flush()
    target_space = KnowledgeSpace(
        tenant_id=tenant.id,
        code="target-space",
        name="目标空间",
        owner_id=admin.id,
    )
    sibling_space = KnowledgeSpace(
        tenant_id=tenant.id,
        code="sibling-space",
        name="同租户其他空间",
        owner_id=admin.id,
    )
    foreign_space = KnowledgeSpace(
        tenant_id=other_tenant.id,
        code="foreign-space",
        name="其他租户空间",
        owner_id=other_admin.id,
    )
    db.add_all([target_space, sibling_space, foreign_space])
    db.flush()
    db.add(
        SpaceGrant(
            tenant_id=tenant.id,
            space_id=target_space.id,
            subject_type="user",
            subject_id=reader.id,
            permission="read",
            effect="allow",
        )
    )
    policy = ChunkPolicy(
        tenant_id=tenant.id,
        name="事实证据切片策略",
        enabled=True,
    )
    db.add(policy)
    db.flush()

    def add_evidence(
        *,
        space: KnowledgeSpace,
        owner: User,
        title: str,
        marker: str,
    ) -> Chunk:
        document = Document(
            tenant_id=space.tenant_id,
            space_id=space.id,
            title=title,
            owner_id=owner.id,
            status="ready",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            tenant_id=space.tenant_id,
            document_id=document.id,
            version_number=1,
            filename=f"{marker}.txt",
            content_type="text/plain",
            size=20,
            sha256=(marker[0] if marker else "a") * 64,
            object_key=f"fixtures/{marker}.txt",
            status="ready",
        )
        db.add(version)
        db.flush()
        document.current_version_id = version.id
        chunk = Chunk(
            tenant_id=space.tenant_id,
            space_id=space.id,
            document_id=document.id,
            version_id=version.id,
            chunk_policy_id=policy.id,
            chunk_id=(marker[-1] if marker else "b") * 64,
            ordinal=0,
            text=f"{title} 的可核验证据",
            content_hash="c" * 64,
            structural_path="document",
            status="published",
        )
        db.add(chunk)
        db.flush()
        return chunk

    old_chunk = add_evidence(
        space=target_space,
        owner=admin,
        title="旧证据",
        marker="a1",
    )
    correct_chunk = add_evidence(
        space=target_space,
        owner=admin,
        title="正确证据",
        marker="b2",
    )
    sibling_chunk = add_evidence(
        space=sibling_space,
        owner=admin,
        title="跨空间证据",
        marker="d4",
    )
    foreign_chunk = add_evidence(
        space=foreign_space,
        owner=other_admin,
        title="跨租户证据",
        marker="e5",
    )
    subject = CanonicalEntity(
        tenant_id=tenant.id,
        space_id=target_space.id,
        canonical_name="数字科技公司",
        normalized_name="数字科技公司",
        entity_type="组织",
        confidence=1,
    )
    obj = CanonicalEntity(
        tenant_id=tenant.id,
        space_id=target_space.id,
        canonical_name="智慧流程中枢项目",
        normalized_name="智慧流程中枢项目",
        entity_type="项目",
        confidence=1,
    )
    db.add_all([subject, obj])
    db.flush()
    fact = Fact(
        tenant_id=tenant.id,
        space_id=target_space.id,
        subject_entity_id=subject.id,
        predicate="负责",
        object_entity_id=obj.id,
        source_chunk_id=old_chunk.id,
        confidence=1,
        status="published",
    )
    db.add(fact)
    db.commit()

    app = FastAPI()
    app.include_router(platform_router, prefix="/api/v1")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr("apps.api.routes.publish_curation_task.delay", lambda _job_id: None)
    tokens = {
        "admin": create_access_token(admin.id, tenant.id, True),
        "reader": create_access_token(reader.id, tenant.id, False),
    }
    with tempfile.TemporaryDirectory(prefix="fact-evidence-provenance-") as temporary:
        monkeypatch.setattr(
            "packages.platform.curation.get_settings",
            lambda: SimpleNamespace(
                provenance_storage_path=Path(temporary) / "provenance.db"
            ),
        )
        with TestClient(app) as client:
            yield client, db, tokens, fact, correct_chunk, sibling_chunk, foreign_chunk
    db.close()
    engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_fact_evidence_update_requires_write_permission_and_same_space(monkeypatch) -> None:
    with fact_evidence_client(monkeypatch) as (
        client,
        db,
        tokens,
        fact,
        correct_chunk,
        sibling_chunk,
        foreign_chunk,
    ):
        denied = client.put(
            f"/api/v1/knowledge/facts/{fact.id}",
            headers=_auth(tokens["reader"]),
            json={"source_chunk_id": correct_chunk.id, "reason_note": "不应被接受"},
        )
        assert denied.status_code == 403

        missing_reason = client.put(
            f"/api/v1/knowledge/facts/{fact.id}",
            headers=_auth(tokens["admin"]),
            json={"source_chunk_id": correct_chunk.id},
        )
        assert missing_reason.status_code == 400
        assert missing_reason.json()["detail"] == "修正证据片段必须填写治理原因"

        for chunk in (sibling_chunk, foreign_chunk):
            crossed = client.put(
                f"/api/v1/knowledge/facts/{fact.id}",
                headers=_auth(tokens["admin"]),
                json={"source_chunk_id": chunk.id, "reason_note": "跨边界证据"},
            )
            assert crossed.status_code == 400
            assert crossed.json()["detail"] == "证据片段必须是当前知识空间内已发布的片段"

        generic_bypass = client.post(
            "/api/v1/curation/decisions",
            headers=_auth(tokens["admin"]),
            json={
                "space_id": fact.space_id,
                "target_type": "fact",
                "target_id": fact.id,
                "field_path": "source_chunk_id",
                "operation": "override",
                "value": sibling_chunk.id,
                "scope": "space",
                "reason_note": "尝试从通用治理入口跨空间绑定",
                "auto_publish": False,
            },
        )
        assert generic_bypass.status_code == 409
        assert generic_bypass.json()["detail"] == "证据片段必须是当前知识空间内已发布的片段"

        correct_chunk.status = "staged"
        db.commit()
        unpublished = client.put(
            f"/api/v1/knowledge/facts/{fact.id}",
            headers=_auth(tokens["admin"]),
            json={"source_chunk_id": correct_chunk.id, "reason_note": "未发布证据"},
        )
        assert unpublished.status_code == 400
        assert unpublished.json()["detail"] == "证据片段必须是当前知识空间内已发布的片段"

        correct_chunk.status = "published"
        document = db.get(Document, correct_chunk.document_id)
        assert document is not None
        newer_version = DocumentVersion(
            tenant_id=document.tenant_id,
            document_id=document.id,
            version_number=2,
            filename="new-current.txt",
            content_type="text/plain",
            size=20,
            sha256="f" * 64,
            object_key="fixtures/new-current.txt",
            status="ready",
        )
        db.add(newer_version)
        db.flush()
        document.current_version_id = newer_version.id
        db.commit()
        stale = client.put(
            f"/api/v1/knowledge/facts/{fact.id}",
            headers=_auth(tokens["admin"]),
            json={"source_chunk_id": correct_chunk.id, "reason_note": "旧版本证据"},
        )
        assert stale.status_code == 400
        assert stale.json()["detail"] == "证据片段不是文档当前版本"


def test_superseded_fact_can_be_restored_without_losing_evidence(monkeypatch) -> None:
    with fact_evidence_client(monkeypatch) as (client, db, tokens, fact, _correct, _sibling, _foreign):
        fact.status = "superseded"
        db.commit()
        monkeypatch.setattr(
            "apps.api.routes._publish_graph_snapshot",
            lambda _db, _tenant_id, _space_id: SimpleNamespace(release_number=77),
        )

        restored = client.post(
            "/api/v1/knowledge/facts",
            headers=_auth(tokens["admin"]),
            json={
                "space_id": fact.space_id,
                "subject_entity_id": fact.subject_entity_id,
                "predicate": fact.predicate,
                "object_entity_id": fact.object_entity_id,
                "source_chunk_id": fact.source_chunk_id,
                "confidence": 1,
                "status": "published",
            },
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["restored"] is True
        assert restored.json()["graph_release"] == 77
        db.expire_all()
        assert db.get(Fact, fact.id).status == "published"
        assert "knowledge.fact.restore" in set(db.scalars(select(AuditEvent.action)))


def test_current_graph_and_index_can_be_published_as_one_immutable_release(monkeypatch) -> None:
    with fact_evidence_client(monkeypatch) as (client, db, tokens, fact, _correct, _sibling, _foreign):
        model = ModelConfig(
            tenant_id=fact.tenant_id,
            name="本地向量",
            model_kind="embedding",
            provider="bge",
            model_name="fixture",
            enabled=True,
        )
        db.add(model)
        db.flush()
        graph = GraphRelease(
            tenant_id=fact.tenant_id,
            space_id=fact.space_id,
            release_number=1,
            graph_name="fact-evidence-graph-1",
            entity_count=2,
            fact_count=1,
            validation_report={"valid": True},
        )
        db.add(graph)
        db.flush()
        index = IndexRelease(
            tenant_id=fact.tenant_id,
            space_id=fact.space_id,
            release_number=1,
            opensearch_index="fact-evidence-index-1",
            qdrant_collection="fact-evidence-vector-1",
            graph_release_id=graph.id,
            model_config_id=model.id,
            embedding_dimension=8,
            document_count=1,
            chunk_count=2,
            checksums={"chunks": "a" * 64},
        )
        db.add(index)
        db.commit()

        published = client.post(
            f"/api/v1/knowledge/releases/publish?space_id={fact.space_id}",
            headers=_auth(tokens["admin"]),
        )
        assert published.status_code == 200, published.text
        assert published.json()["unchanged"] is False
        assert published.json()["graph_release_id"] == graph.id
        assert published.json()["index_release_id"] == index.id

        repeated = client.post(
            f"/api/v1/knowledge/releases/publish?space_id={fact.space_id}",
            headers=_auth(tokens["admin"]),
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["unchanged"] is True
        assert db.scalar(select(KnowledgeRelease).where(KnowledgeRelease.space_id == fact.space_id)) is not None


def test_fact_evidence_update_is_audited_overlay_and_legacy_direction_is_soft_suppressed(
    monkeypatch,
) -> None:
    with fact_evidence_client(monkeypatch) as (
        client,
        db,
        tokens,
        fact,
        correct_chunk,
        _sibling_chunk,
        _foreign_chunk,
    ):
        repaired = client.put(
            f"/api/v1/knowledge/facts/{fact.id}",
            headers=_auth(tokens["admin"]),
            json={
                "source_chunk_id": correct_chunk.id,
                "reason_note": "演示 Ground Truth 证据对齐：FACT-DEMO-008",
            },
        )
        assert repaired.status_code == 200, repaired.text
        assert repaired.json()["source_chunk_id"] == correct_chunk.id
        db.expire_all()
        persisted = db.get(Fact, fact.id)
        assert persisted is not None
        assert persisted.source_chunk_id != correct_chunk.id
        assert effective_fact(db, persisted)["source_chunk_id"] == correct_chunk.id

        suppressed = client.put(
            f"/api/v1/knowledge/facts/{fact.id}",
            headers=_auth(tokens["admin"]),
            json={
                "status": "suppressed",
                "reason_note": "演示 Ground Truth 关系方向纠正：责任主体应指向所负责项目",
            },
        )
        assert suppressed.status_code == 200, suppressed.text
        db.expire_all()
        persisted = db.get(Fact, fact.id)
        assert persisted is not None and persisted.deleted_at is None
        assert persisted.status == "published"
        assert effective_fact(db, persisted)["status"] == "suppressed"

        reasons = set(db.scalars(select(CurationDecision.reason_note)))
        assert "演示 Ground Truth 证据对齐：FACT-DEMO-008" in reasons
        assert "演示 Ground Truth 关系方向纠正：责任主体应指向所负责项目" in reasons
        audit_actions = set(db.scalars(select(AuditEvent.action)))
        assert "knowledge.fact.update" in audit_actions
