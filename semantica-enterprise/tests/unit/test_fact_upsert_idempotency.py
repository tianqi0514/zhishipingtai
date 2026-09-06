from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.worker.tasks import _upsert_extracted_fact
from packages.platform.database import Base
from packages.platform.models import Fact


def _session(*, foreign_keys: bool = False) -> Session:
    engine = create_engine("sqlite:///:memory:")
    if foreign_keys:
        event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    # This matches the production SessionLocal setting that originally made
    # a pending duplicate invisible to the following SELECT.
    return Session(engine, autoflush=False, expire_on_commit=False)


def _upsert(db: Session, identity_map: dict, **overrides):
    values = {
        "tenant_id": "tenant",
        "space_id": "space",
        "subject_entity_id": "subject",
        "predicate": "供应",
        "object_entity_id": "object",
        "source_chunk_id": "chunk",
        "confidence": 0.72,
        "scope_tokens": ["space:read"],
    }
    values.update(overrides)
    return _upsert_extracted_fact(db, identity_map=identity_map, **values)


def test_same_extraction_batch_collapses_duplicate_pending_facts() -> None:
    db = _session()
    try:
        identity_map: dict = {}
        first, first_created = _upsert(db, identity_map, confidence=0.72)
        duplicate, duplicate_created = _upsert(db, identity_map, confidence=0.91)

        # With autoflush disabled neither row has reached SQLite yet.  The
        # transaction-local identity map must still return one ORM object.
        assert first is duplicate
        assert first_created is True
        assert duplicate_created is False
        assert first.confidence == pytest.approx(0.91)

        db.commit()
        assert db.scalar(select(func.count(Fact.id))) == 1
        persisted = db.scalar(select(Fact))
        assert persisted is not None
        assert persisted.source_chunk_id == "chunk"
        assert persisted.scope_tokens == ["space:read"]
    finally:
        db.close()


def test_reprocessing_reuses_existing_fact_and_restores_current_projection() -> None:
    db = _session()
    try:
        original = Fact(
            id="stable-fact",
            tenant_id="tenant",
            space_id="space",
            subject_entity_id="subject",
            predicate="供应",
            object_entity_id="object",
            source_chunk_id="chunk",
            confidence=0.81,
            scope_tokens=["old-scope"],
            status="superseded",
            deleted_at=datetime.now(timezone.utc),
        )
        db.add(original)
        db.commit()
        created_at = original.created_at

        restored, created = _upsert(
            db,
            {},
            confidence=0.77,
            scope_tokens=["current-scope"],
        )
        db.commit()

        assert created is False
        assert restored.id == "stable-fact"
        assert restored.created_at == created_at
        assert restored.source_chunk_id == "chunk"
        assert restored.confidence == pytest.approx(0.81)
        assert restored.scope_tokens == ["current-scope"]
        assert restored.status == "published"
        assert restored.deleted_at is None
        assert db.scalar(select(func.count(Fact.id))) == 1
    finally:
        db.close()


def test_upsert_does_not_swallow_unrelated_integrity_errors() -> None:
    db = _session(foreign_keys=True)
    try:
        # Deliberately omit all referenced rows.  Identity deduplication must
        # not turn a foreign-key/data-integrity defect into apparent success.
        _upsert(db, {})
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()
