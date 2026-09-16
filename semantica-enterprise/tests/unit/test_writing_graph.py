from __future__ import annotations

import re
import threading

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from packages.platform.database import Base
from packages.platform.models import (
    Chunk,
    ContentElement,
    Document,
    DocumentVersion,
    KnowledgeSpace,
    Tenant,
    User,
    WritingClaim,
    WritingEntityCandidate,
    WritingEvidence,
    WritingFact,
    WritingGraphReleaseItem,
    WritingRelation,
)
from packages.platform.writing_graph import (
    detect_writing_fact_conflicts,
    govern_writing_object,
    process_writing_graph_version,
    publish_writing_graph,
    writing_evidence_segments,
    writing_graph_release_payload,
)


def test_writing_evidence_segments_preserve_exact_source_spans() -> None:
    text = "# 事件信息\n\n测试地区发生6.2级地震。\n\n## 资源\n\n可用人员320人，需求500人。"
    segments = writing_evidence_segments(text, max_chars=120)
    assert [item["text"] for item in segments] == [
        "# 事件信息\n\n测试地区发生6.2级地震。",
        "## 资源\n\n可用人员320人，需求500人。",
    ]
    for item in segments:
        assert text[item["start"]:item["end"]] == item["text"]


def graph_fixture() -> tuple[Session, Tenant, User, KnowledgeSpace, Document, DocumentVersion]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(code="writing-graph", name="写作图谱测试")
    db.add(tenant); db.flush()
    user = User(
        tenant_id=tenant.id, username="writer", password_hash="unused",
        display_name="写作治理员", is_admin=True, enabled=True,
    )
    db.add(user); db.flush()
    space = KnowledgeSpace(
        tenant_id=tenant.id, code="earthquake", name="地震材料",
        owner_id=user.id, enabled=True,
    )
    db.add(space); db.flush()
    document = Document(
        tenant_id=tenant.id, space_id=space.id, title="灾情简报.md",
        owner_id=user.id, status="ready", tags=["task_data"],
    )
    db.add(document); db.flush()
    version = DocumentVersion(
        tenant_id=tenant.id, document_id=document.id, version_number=1,
        filename="灾情简报.md", content_type="text/markdown", size=100,
        sha256="a" * 64, object_key="test/brief.md", status="ready",
        parse_summary={},
    )
    db.add(version); db.flush()
    document.current_version_id = version.id
    element = ContentElement(
        tenant_id=tenant.id, space_id=space.id, document_id=document.id,
        version_id=version.id, element_id="element-1", element_type="paragraph",
        ordinal=0, text="东方救援队可用搜救人员为320人。",
        structural_path="事件情况/资源", page_number=2,
    )
    db.add(element); db.flush()
    chunk = Chunk(
        tenant_id=tenant.id, space_id=space.id, document_id=document.id,
        version_id=version.id, element_id=element.id, chunk_policy_id="policy",
        chunk_id="chunk-1", ordinal=0, text=element.text,
        content_hash="b" * 64, structural_path=element.structural_path,
        page_number=2, status="published",
    )
    db.add(chunk); db.commit()
    return db, tenant, user, space, document, version


def joint_result(evidence_id: str) -> dict:
    return {
        "entities": [
            {
                "mention_text": "东方救援队", "canonical_name": "东方救援队",
                "entity_type": "组织", "aliases": [], "evidence_ids": [evidence_id],
                "confidence": 0.99, "needs_confirmation": False,
            },
            {
                "mention_text": "搜救人员", "canonical_name": "搜救人员",
                "entity_type": "资源", "aliases": [], "evidence_ids": [evidence_id],
                "confidence": 0.98, "needs_confirmation": False,
            },
        ],
        "claims": [{
            "subject": "东方救援队", "predicate": "可用搜救人员",
            "object_value": 320, "claim_type": "assertion", "value_type": "number",
            "unit": "人", "time_scope": {}, "applicable_scope": {"region": "测试地区"},
            "qualifiers": {}, "evidence_ids": [evidence_id], "confidence": 0.99,
            "needs_confirmation": False,
        }],
        "relations": [{
            "subject": "东方救援队", "predicate": "拥有", "object": "搜救人员",
            "time_scope": {}, "applicable_scope": {"region": "测试地区"},
            "evidence_ids": [evidence_id], "confidence": 0.95,
            "needs_confirmation": False,
        }],
        "metrics": [{
            "name": "可用搜救人员", "value": 320, "value_type": "integer", "unit": "人",
            "time_scope": {}, "applicable_scope": {"region": "测试地区"},
            "evidence_ids": [evidence_id], "confidence": 0.99,
            "needs_confirmation": False,
        }],
        "sample_profile": None,
        "ambiguities": [],
    }


def test_real_evidence_joint_extraction_governance_and_release() -> None:
    db, tenant, user, space, document, version = graph_fixture()
    first_evidence_id: list[str] = []

    def generator(prompt: str) -> dict:
        evidence = db.scalar(select(WritingEvidence))
        first_evidence_id.append(evidence.id)
        assert evidence.id in prompt
        return joint_result(evidence.id)

    metrics = process_writing_graph_version(
        db,
        document=document,
        version=version,
        model_config_id="model",
        api_key="unused",
        model="test",
        base_url=None,
        actor_id=user.id,
        generator=generator,
    )
    db.commit()
    assert metrics["evidence"] == 1
    assert metrics["entities"] == 2
    assert metrics["claims"] == 1
    assert metrics["facts"] == 3
    assert metrics["relations"] == 1

    # Repeating the same version and strategy is idempotent and performs no
    # second model request.
    reused = process_writing_graph_version(
        db,
        document=document,
        version=version,
        model_config_id="model",
        api_key="unused",
        model="test",
        base_url=None,
        actor_id=user.id,
        generator=lambda _prompt: (_ for _ in ()).throw(AssertionError("must reuse")),
    )
    assert reused["model_requests"] == 0
    assert reused["reused_batches"] == 1

    for entity in db.scalars(select(WritingEntityCandidate)):
        govern_writing_object(
            db, tenant_id=tenant.id, space_id=space.id, target_type="entity",
            target_id=entity.id, action="accept", actor_id=user.id, reason="核对原文",
        )
    for claim in db.scalars(select(WritingClaim)):
        govern_writing_object(
            db, tenant_id=tenant.id, space_id=space.id, target_type="claim",
            target_id=claim.id, action="accept", actor_id=user.id, reason="核对原文",
        )
    for fact in db.scalars(select(WritingFact)):
        govern_writing_object(
            db, tenant_id=tenant.id, space_id=space.id, target_type="fact",
            target_id=fact.id, action="accept", actor_id=user.id, reason="业务确认",
        )
    relation = db.scalar(select(WritingRelation))
    govern_writing_object(
        db, tenant_id=tenant.id, space_id=space.id, target_type="relation",
        target_id=relation.id, action="accept", actor_id=user.id, reason="关系确认",
    )
    release = publish_writing_graph(
        db, tenant_id=tenant.id, space_id=space.id, actor_id=user.id,
    )
    db.commit()
    payload = writing_graph_release_payload(db, release)
    assert payload["counts"] == {
        "evidence": 1, "entities": 2, "claims": 1, "facts": 3, "relations": 1,
    }
    assert db.scalar(select(WritingGraphReleaseItem).where(
        WritingGraphReleaseItem.release_id == release.id,
        WritingGraphReleaseItem.object_type == "evidence",
    )).snapshot["id"] == first_evidence_id[0]


def test_independent_evidence_batches_extract_concurrently_and_persist_serially() -> None:
    db, _tenant, user, _space, document, version = graph_fixture()
    chunk = db.scalar(select(Chunk).where(Chunk.version_id == version.id))
    chunk.text = "第一段明确事实。\n\n第二段另一项明确事实。"
    chunk.content_hash = "c" * 64
    db.commit()
    barrier = threading.Barrier(2)
    thread_names: set[str] = set()

    def generator(prompt: str) -> dict:
        thread_names.add(threading.current_thread().name)
        barrier.wait(timeout=3)
        assert re.search(r'"evidence_id":\s*"[^"]+"', prompt)
        return {
            "entities": [], "claims": [], "relations": [], "metrics": [],
            "sample_profile": None, "ambiguities": [],
        }

    metrics = process_writing_graph_version(
        db,
        document=document,
        version=version,
        model_config_id="model",
        api_key="unused",
        model="test",
        base_url=None,
        actor_id=user.id,
        generator=generator,
        concurrency=2,
    )
    db.commit()
    assert metrics["model_requests"] == 2
    assert metrics["concurrency"] == 2
    assert len(thread_names) == 2


def test_fact_without_evidence_cannot_be_verified() -> None:
    db, tenant, user, space, _document, _version = graph_fixture()
    fact = WritingFact(
        tenant_id=tenant.id, space_id=space.id, fact_key="unsupported",
        subject={"name": "模型"}, predicate="声称", object_value={"value": "无依据"},
        evidence_ids=[], claim_ids=[], version=1,
    )
    db.add(fact); db.flush()
    try:
        govern_writing_object(
            db, tenant_id=tenant.id, space_id=space.id, target_type="fact",
            target_id=fact.id, action="accept", actor_id=user.id, reason="错误尝试",
        )
    except ValueError as exc:
        assert "缺少来源依据" in str(exc)
    else:
        raise AssertionError("unsupported fact must be rejected")


def test_conflicting_candidate_never_silently_replaces_verified_fact() -> None:
    db, tenant, user, space, _document, _version = graph_fixture()
    evidence = db.scalar(select(WritingEvidence))
    if evidence is None:
        # Evidence projection normally happens before extraction.  The
        # conflict detector itself needs only stable source ids.
        evidence = WritingEvidence(
            tenant_id=tenant.id, space_id=space.id, document_id=_document.id,
            document_version_id=_version.id, evidence_key="c" * 64,
            filename=_version.filename, file_version=1, locator={}, text="原始值",
            content_hash="d" * 64,
        )
        db.add(evidence); db.flush()
    current = WritingFact(
        tenant_id=tenant.id, space_id=space.id, fact_key="all-area-bed-gap",
        subject={"name": "测试地区"}, predicate="全域床位缺口",
        object_value={"value": 80}, value_type="number", unit="张",
        evidence_ids=[evidence.id], verification_status="verified",
        verified_by=user.id, version=1,
    )
    candidate = WritingFact(
        tenant_id=tenant.id, space_id=space.id, fact_key="all-area-bed-gap",
        subject={"name": "测试地区"}, predicate="全域床位缺口",
        object_value={"value": 0}, value_type="number", unit="张",
        evidence_ids=[evidence.id], verification_status="candidate", version=2,
    )
    db.add_all([current, candidate]); db.flush()

    assert detect_writing_fact_conflicts(
        db, space_id=space.id, fact_keys=["all-area-bed-gap"],
    ) == 1
    assert current.verification_status == "verified"
    assert candidate.verification_status == "conflicted"
