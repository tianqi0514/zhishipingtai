from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from packages.platform.database import Base
from packages.platform.knowledge_search import _canonicalize_chunk_identity
from packages.platform.migrations import run_migrations
from packages.platform import models  # noqa: F401
from packages.platform.models import Chunk
from packages.semantica_adapter.indexing import search_point_id


def test_schema_migrations_are_repeatable_on_fresh_database() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        engine = create_engine(f"sqlite:///{Path(temporary_directory) / 'platform.db'}")
        Base.metadata.create_all(engine)
        run_migrations(engine)
        run_migrations(engine)
        with engine.connect() as connection:
            versions = connection.execute(text("SELECT version FROM schema_migrations")).scalars().all()
            profile_table = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='document_profiles'")
            ).scalar_one()
    assert versions == [
        "0011_document_profiles",
        "0012_agent_conversations",
        "0013_space_grant_uniqueness",
        "0014_knowledge_analysis",
        "0015_human_curation",
        "0016_application_foundation",
        "0017_structured_semantic_query",
        "0018_schema_fingerprint_history",
        "0019_multimodal_media",
    ]
    assert profile_table == "document_profiles"


def test_search_point_id_is_stable_and_qdrant_compatible() -> None:
    first = search_point_id("a" * 64)
    second = search_point_id("a" * 64)
    other = search_point_id("b" * 64)
    assert first == second
    assert first != other
    assert len(first) == 36


def test_legacy_search_result_resolves_to_database_chunk_uuid() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    semantic_id = "a" * 64
    with Session(engine) as db:
        chunk = Chunk(
            tenant_id="tenant",
            space_id="space",
            document_id="document",
            version_id="version",
            chunk_policy_id="policy",
            chunk_id=semantic_id,
            ordinal=0,
            text="事实内容",
            content_hash="b" * 64,
            structural_path="document",
        )
        db.add(chunk)
        db.flush()
        result = _canonicalize_chunk_identity(
            db,
            {
                "id": chunk.id,
                "chunk_id": semantic_id,
                "document_id": "document",
                "version_id": "version",
            },
        )
        assert result["id"] == chunk.id
        assert result["chunk_db_id"] == chunk.id
        assert result["chunk_id"] == semantic_id
