from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from apps.worker.tasks import _upsert_extracted_fact
from packages.platform.models import (
    CanonicalEntity,
    Chunk,
    ChunkPolicy,
    Document,
    DocumentVersion,
    Fact,
    KnowledgeSpace,
    Tenant,
)


DATABASE_URL = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://")),
    reason="requires the Docker PostgreSQL integration database",
)


def test_postgresql_fact_unique_constraint_is_idempotent_with_autoflush_disabled() -> None:
    """Exercise the production dialect and ``uq_fact_source`` in one rollback-only transaction."""

    suffix = uuid.uuid4().hex
    tenant_id = str(uuid.uuid4())
    space_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    policy_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    subject_id = str(uuid.uuid4())
    object_id = str(uuid.uuid4())
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        transaction = db.begin()
        try:
            # These models intentionally expose IDs rather than ORM
            # relationships. Flush each FK layer explicitly so this remains a
            # focused Fact integration test instead of relying on UoW ordering.
            db.add(Tenant(id=tenant_id, code=f"fact-upsert-{suffix}", name="事实幂等验收"))
            db.flush()
            db.add(
                KnowledgeSpace(
                    id=space_id,
                    tenant_id=tenant_id,
                    code=f"fact-upsert-{suffix}",
                    name="事实幂等验收空间",
                )
            )
            db.flush()
            db.add_all(
                [
                    Document(
                        id=document_id,
                        tenant_id=tenant_id,
                        space_id=space_id,
                        title="事实幂等验收文档",
                        status="processing",
                    ),
                    ChunkPolicy(
                        id=policy_id,
                        tenant_id=tenant_id,
                        name=f"幂等策略-{suffix}",
                    ),
                    CanonicalEntity(
                        id=subject_id,
                        tenant_id=tenant_id,
                        space_id=space_id,
                        canonical_name="东方智造",
                        normalized_name=f"东方智造-{suffix}",
                        entity_type="供应商",
                    ),
                    CanonicalEntity(
                        id=object_id,
                        tenant_id=tenant_id,
                        space_id=space_id,
                        canonical_name="NexusOne",
                        normalized_name=f"nexusone-{suffix}",
                        entity_type="产品",
                    ),
                ]
            )
            db.flush()
            db.add(
                DocumentVersion(
                    id=version_id,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_number=1,
                    filename="fact.txt",
                    content_type="text/plain",
                    size=1,
                    sha256=uuid.uuid4().hex * 2,
                    object_key=f"integration/{suffix}/fact.txt",
                    status="ready",
                )
            )
            db.flush()
            db.add(
                Chunk(
                    id=chunk_id,
                    tenant_id=tenant_id,
                    space_id=space_id,
                    document_id=document_id,
                    version_id=version_id,
                    chunk_policy_id=policy_id,
                    chunk_id=uuid.uuid4().hex * 2,
                    ordinal=0,
                    text="东方智造供应 NexusOne。",
                    content_hash=uuid.uuid4().hex * 2,
                    structural_path="document/paragraph[1]",
                )
            )
            db.flush()

            identity_map: dict = {}
            common = {
                "tenant_id": tenant_id,
                "space_id": space_id,
                "subject_entity_id": subject_id,
                "predicate": "供应",
                "object_entity_id": object_id,
                "source_chunk_id": chunk_id,
                "scope_tokens": [f"space:{space_id}:read"],
                "identity_map": identity_map,
            }
            first, first_created = _upsert_extracted_fact(db, confidence=0.73, **common)
            second, second_created = _upsert_extracted_fact(db, confidence=0.94, **common)
            db.flush()

            assert first is second
            assert first_created is True and second_created is False
            assert first.confidence == pytest.approx(0.94)
            assert db.scalar(
                select(func.count(Fact.id)).where(
                    Fact.space_id == space_id,
                    Fact.source_chunk_id == chunk_id,
                )
            ) == 1
        finally:
            transaction.rollback()
    engine.dispose()
