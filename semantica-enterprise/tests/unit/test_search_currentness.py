from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.platform import models  # noqa: F401
from packages.platform.database import Base
from packages.platform.knowledge_search import (
    _canonicalize_chunk_identity,
    _is_current_chunk_item,
)
from packages.platform.models import Chunk, Document


def test_external_search_hits_are_authorized_against_current_document_state() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        document = Document(
            id="10000000-0000-0000-0000-000000000001",
            tenant_id="tenant-a",
            space_id="space-a",
            title="current document",
            current_version_id="20000000-0000-0000-0000-000000000001",
            tags=["verified"],
        )
        chunk = Chunk(
            id="30000000-0000-0000-0000-000000000001",
            tenant_id="tenant-a",
            space_id="space-a",
            document_id=document.id,
            version_id=document.current_version_id,
            chunk_policy_id="policy",
            chunk_id="semantic-current",
            ordinal=0,
            text="verified fact",
            content_hash="hash",
            structural_path="body",
            status="published",
        )
        db.add_all([document, chunk])
        db.flush()

        hit = _canonicalize_chunk_identity(db, {"id": chunk.id, "score": 0.9})
        assert _is_current_chunk_item(
            db,
            hit,
            tenant_id="tenant-a",
            space_ids=["space-a"],
        )
        assert hit["title"] == "current document"
        assert hit["document_tags"] == ["verified"]

        document.current_version_id = "20000000-0000-0000-0000-000000000002"
        db.flush()
        assert not _is_current_chunk_item(
            db,
            hit,
            tenant_id="tenant-a",
            space_ids=["space-a"],
        )

        document.current_version_id = chunk.version_id
        document.deleted_at = datetime.now(timezone.utc)
        db.flush()
        assert not _is_current_chunk_item(
            db,
            hit,
            tenant_id="tenant-a",
            space_ids=["space-a"],
        )


def test_external_search_hit_cannot_cross_tenant_or_space_scope() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        document = Document(
            tenant_id="tenant-a",
            space_id="space-a",
            title="isolated",
            current_version_id="version-a",
        )
        db.add(document)
        db.flush()
        chunk = Chunk(
            tenant_id="tenant-a",
            space_id="space-a",
            document_id=document.id,
            version_id="version-a",
            chunk_policy_id="policy",
            chunk_id="semantic-isolated",
            ordinal=0,
            text="private",
            content_hash="hash",
            structural_path="body",
            status="published",
        )
        db.add(chunk)
        db.flush()
        hit = {"id": chunk.id, "chunk_db_id": chunk.id}

        assert not _is_current_chunk_item(
            db,
            hit,
            tenant_id="tenant-b",
            space_ids=["space-a"],
        )
        assert not _is_current_chunk_item(
            db,
            hit,
            tenant_id="tenant-a",
            space_ids=["space-b"],
        )
