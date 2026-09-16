from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from packages.platform.database import Base
from packages.platform.models import KnowledgeSpace, Tenant, User, WritingGraphRelease, WritingGraphReleaseItem
from packages.platform.security import hash_password
from packages.platform.writing_graph_query import (
    get_release_object,
    search_writing_graph_release,
    writing_relation_path,
)


def test_search_and_relation_path_use_release_snapshots_only() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    tenant = Tenant(code="graph-query", name="写作图谱查询")
    db.add(tenant); db.flush()
    user = User(
        tenant_id=tenant.id, username="writer", password_hash=hash_password("Writer@123"),
        display_name="作者", enabled=True, is_admin=True,
    )
    db.add(user); db.flush()
    space = KnowledgeSpace(
        tenant_id=tenant.id, code="graph-query", name="写作图谱空间",
        owner_id=user.id, enabled=True,
    )
    db.add(space); db.flush()
    release = WritingGraphRelease(
        tenant_id=tenant.id, space_id=space.id, release_number=1,
        graph_name="writing_graph_query_r1", evidence_count=1, entity_count=2,
        claim_count=1, fact_count=1, relation_count=1, checksum="a" * 64,
        status="published", created_by=user.id,
    )
    db.add(release); db.flush()
    snapshots = [
        ("entity", "supplier", {"id": "supplier", "canonical_name": "东方智造", "entity_type": "供应商"}),
        ("entity", "product", {"id": "product", "canonical_name": "NexusOne", "entity_type": "产品"}),
        ("evidence", "evidence", {"id": "evidence", "filename": "供应材料.md", "text": "东方智造供应 NexusOne。"}),
        ("claim", "claim", {"id": "claim", "subject": {"name": "东方智造"}, "predicate": "供应", "object_value": {"value": "NexusOne"}, "evidence_ids": ["evidence"]}),
        ("fact", "fact", {"id": "fact", "fact_key": "supplier-product", "predicate": "供应", "claim_ids": ["claim"], "evidence_ids": ["evidence"], "subject_candidate_id": "supplier", "object_candidate_id": "product"}),
        ("relation", "relation", {"id": "relation", "predicate": "供应", "fact_id": "fact", "subject_candidate_id": "supplier", "object_candidate_id": "product"}),
    ]
    for object_type, object_id, snapshot in snapshots:
        db.add(WritingGraphReleaseItem(
            tenant_id=tenant.id, release_id=release.id, object_type=object_type,
            object_id=object_id, object_version=1, content_hash=(object_id[0] * 64), snapshot=snapshot,
        ))
    db.commit()

    found = search_writing_graph_release(db, release, query="东方智造 NexusOne", limit=10)
    assert {item["object_type"] for item in found} >= {"entity", "fact", "relation"}
    assert get_release_object(db, release, object_type="evidence", object_id="evidence")["filename"] == "供应材料.md"
    path = writing_relation_path(
        db, release, start_entity_id="supplier", end_entity_id="product", max_hops=2,
    )
    assert len(path) == 1
    assert path[0]["relation"]["predicate"] == "供应"
    db.close()
