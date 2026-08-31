from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.platform import models  # noqa: F401
from packages.platform.curation import (
    create_decision,
    effective_chunk_payloads,
    effective_elements,
    effective_entity,
    effective_fact,
    effective_profile,
    rollback_decision,
)
from packages.platform.database import Base
from packages.platform.models import (
    CanonicalEntity,
    Chunk,
    ContentElement,
    Document,
    DocumentProfile,
    DocumentVersion,
    Fact,
    KnowledgeSpace,
    Tenant,
    User,
)
from packages.semantica_adapter.governance import govern_entities


def curation_fixture(db: Session) -> dict:
    tenant = Tenant(id="tenant", code="tenant", name="租户")
    user = User(id="user", tenant_id=tenant.id, username="curator", password_hash="x", display_name="治理员")
    space = KnowledgeSpace(id="space", tenant_id=tenant.id, code="space", name="知识空间", owner_id=user.id)
    document = Document(id="document", tenant_id=tenant.id, space_id=space.id, title="产品手册", status="ready")
    version = DocumentVersion(
        id="version", tenant_id=tenant.id, document_id=document.id, version_number=1,
        filename="manual.txt", content_type="text/plain", size=20, sha256="a" * 64,
        object_key="fixture/manual.txt", status="ready",
    )
    document.current_version_id = version.id
    profile = DocumentProfile(
        id="profile", tenant_id=tenant.id, space_id=space.id, document_id=document.id,
        version_id=version.id, summary="自动摘要", classification="产品资料",
        document_type="手册", quality_score=90, completeness_score=90,
        readability_score=90, structure_score=90, policy_id="policy",
    )
    element = ContentElement(
        id="element-row", tenant_id=tenant.id, space_id=space.id, document_id=document.id,
        version_id=version.id, element_id="element-stable", element_type="paragraph",
        ordinal=0, text="自动解析正文", structural_path="document/paragraph[1]",
    )
    chunk = Chunk(
        id="chunk-row", tenant_id=tenant.id, space_id=space.id, document_id=document.id,
        version_id=version.id, element_id=element.id, chunk_policy_id="chunk-policy",
        chunk_id="b" * 64, ordinal=0, text="自动切片正文", content_hash="c" * 64,
        structural_path="document/paragraph[1]", status="published",
    )
    first = CanonicalEntity(
        id="entity-a", tenant_id=tenant.id, space_id=space.id,
        canonical_name="自动实体 A", normalized_name="自动实体 a", entity_type="产品",
        confidence=0.8,
    )
    second = CanonicalEntity(
        id="entity-b", tenant_id=tenant.id, space_id=space.id,
        canonical_name="自动实体 B", normalized_name="自动实体 b", entity_type="产品",
        confidence=0.7,
    )
    fact = Fact(
        id="fact", tenant_id=tenant.id, space_id=space.id,
        subject_entity_id=first.id, predicate="支持", object_entity_id=second.id,
        source_chunk_id=chunk.id, confidence=0.8,
    )
    db.add_all([tenant, user, space, document, version, profile, element, chunk, first, second, fact])
    db.flush()
    return locals()


def test_profile_decision_is_overlay_and_can_be_rolled_back(monkeypatch) -> None:
    monkeypatch.setattr("packages.platform.curation.track_curation_decision", lambda *args, **kwargs: None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        data = curation_fixture(db)
        decision, _, _ = create_decision(
            db, user=data["user"], space_id="space", target_type="document_profile",
            target_id="version", field_path="classification", operation="override",
            value="人工分类", version_id="version", scope="version_only",
        )
        db.flush()
        projected = effective_profile(db, data["version"])
        assert data["profile"].classification == "产品资料"
        assert projected["classification"] == "人工分类"
        assert projected["automatic"]["classification"] == "产品资料"
        assert projected["field_origins"]["classification"] == "manual"

        rollback_decision(db, user=data["user"], decision=decision)
        db.flush()
        restored = effective_profile(db, data["version"])
        assert restored["classification"] == "产品资料"
        assert restored["field_origins"]["classification"] == "automatic"


def test_content_chunk_entity_and_fact_use_effective_projection(monkeypatch) -> None:
    monkeypatch.setattr("packages.platform.curation.track_curation_decision", lambda *args, **kwargs: None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        data = curation_fixture(db)
        for target_type, target_id, field_path, value, scope in [
            ("content_element", "element-stable", "text", "人工正文", "version_only"),
            ("chunk", "b" * 64, "boost", 1.8, "version_only"),
            ("entity", "entity-a", "canonical_name", "人工实体 A", "space"),
            ("fact", "fact", "predicate", "人工支持", "space"),
        ]:
            create_decision(
                db, user=data["user"], space_id="space", target_type=target_type,
                target_id=target_id, field_path=field_path, operation="override",
                value=value, version_id="version", scope=scope,
            )
        db.flush()

        assert data["element"].text == "自动解析正文"
        assert effective_elements(db, [data["element"]], "version")[0].text == "人工正文"
        chunk = effective_chunk_payloads(db, [data["chunk"]])[0]
        assert data["chunk"].text == "自动切片正文"
        assert chunk["boost"] == 1.8
        assert chunk["curation_decision_id"]
        assert effective_entity(db, data["first"])["canonical_name"] == "人工实体 A"
        assert data["first"].canonical_name == "自动实体 A"
        assert effective_fact(db, data["fact"])["predicate"] == "人工支持"
        assert data["fact"].predicate == "支持"


def test_fact_projection_can_clear_nullable_object_field(monkeypatch) -> None:
    monkeypatch.setattr("packages.platform.curation.track_curation_decision", lambda *args, **kwargs: None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        data = curation_fixture(db)
        create_decision(
            db,
            user=data["user"],
            space_id="space",
            target_type="fact",
            target_id="fact",
            field_path="object_entity_id",
            operation="override",
            value=None,
            scope="space",
        )
        create_decision(
            db,
            user=data["user"],
            space_id="space",
            target_type="fact",
            target_id="fact",
            field_path="object_value",
            operation="override",
            value="人工客体值",
            scope="space",
        )
        db.flush()

        projected = effective_fact(db, data["fact"])
        assert projected["object_entity_id"] is None
        assert projected["object_value"] == "人工客体值"


def test_semantica_entity_governance_honors_must_and_cannot_link() -> None:
    mentions = [
        {"mention_id": "m1", "text": "Nexus One", "entity_type": "产品", "confidence": 0.9},
        {"mention_id": "m2", "text": "NexusOne", "entity_type": "产品", "confidence": 0.8},
    ]
    separated, _ = govern_entities(
        mentions,
        similarity_threshold=0.5,
        constraints={"must_link": [], "cannot_link": [{"left_name": "nexus one", "right_name": "nexusone"}]},
    )
    assert len(separated) == 2

    merged, _ = govern_entities(
        mentions,
        similarity_threshold=1.0,
        constraints={"must_link": [{"left_name": "nexus one", "right_name": "nexusone"}], "cannot_link": []},
    )
    assert len(merged) == 1
    assert sorted(merged[0].mention_ids) == ["m1", "m2"]


def test_reprocessing_preserves_citation_chunk_rows() -> None:
    source = (Path(__file__).resolve().parents[2] / "apps/worker/tasks.py").read_text(encoding="utf-8")
    assert "delete(Chunk)" not in source
    assert 'old_chunk.status = "superseded"' in source
    assert "existing_chunks.get(item.chunk_id)" in source
    assert "rollback_chunk_state" in source
    assert '"curation_status": "publish_failed"' in source
