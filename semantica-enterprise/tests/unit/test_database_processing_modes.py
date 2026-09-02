from datetime import datetime, timezone

from apps.worker.tasks import _database_processing_modes
from packages.platform.models import SourceConnector


def _source(source_type: str, config: dict | None = None) -> SourceConnector:
    return SourceConnector(
        id=f"source-{source_type}",
        tenant_id="tenant",
        space_id="space",
        name="fixture",
        source_type=source_type,
        config=config or {},
    )


def test_non_database_documents_preserve_model_processing() -> None:
    assert _database_processing_modes(None) == (False, True, True)
    assert _database_processing_modes(_source("web")) == (False, True, True)


def test_database_snapshots_default_to_deterministic_processing() -> None:
    assert _database_processing_modes(_source("database")) == (True, False, False)


def test_database_model_processing_requires_explicit_opt_in() -> None:
    source = _source("database", {
        "database_profile_model_enabled": True,
        "generic_semantic_extraction_enabled": True,
    })
    assert _database_processing_modes(source) == (True, True, True)


def test_deleted_database_source_is_not_processed_as_a_live_snapshot() -> None:
    source = _source("database")
    source.deleted_at = datetime.now(timezone.utc)
    assert _database_processing_modes(source) == (False, True, True)
