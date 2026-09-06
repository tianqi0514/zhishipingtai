from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.worker.tasks import _restore_soft_deleted_entity
from packages.semantica_adapter.indexing import SearchIndexer
from packages.semantica_adapter.resilience import (
    classify_external_failure,
    retry_transient_call,
)


class ResponseHandlingException(Exception):
    pass


def test_wrapped_qdrant_timeout_is_retryable_and_secret_safe() -> None:
    secret = "private-key-must-not-leak"
    cause = TimeoutError(f"timed out while using {secret}")
    error = ResponseHandlingException(f"transport failed with {secret}")
    error.__cause__ = cause

    failure = classify_external_failure(error, secrets=[secret])

    assert failure.category == "timeout"
    assert failure.retryable is True
    assert failure.error_type == "ResponseHandlingException"
    assert secret not in failure.message


def test_transient_call_retries_with_bounded_backoff() -> None:
    operation = Mock(
        side_effect=[
            ResponseHandlingException("timed out"),
            ResponseHandlingException("connection reset"),
            "published",
        ]
    )
    sleeper = Mock()

    result = retry_transient_call(
        operation,
        max_attempts=3,
        initial_delay_seconds=0.25,
        sleeper=sleeper,
    )

    assert result == "published"
    assert operation.call_count == 3
    assert [call.args[0] for call in sleeper.call_args_list] == [0.25, 0.5]


def test_permanent_validation_failure_is_not_retried() -> None:
    operation = Mock(side_effect=ValueError("unknown vector dimension"))

    with pytest.raises(ValueError, match="unknown vector dimension"):
        retry_transient_call(operation, max_attempts=5, sleeper=Mock())

    operation.assert_called_once_with()


def test_search_indexer_retries_qdrant_response_timeout() -> None:
    sleeper = Mock()
    indexer = SearchIndexer(
        opensearch_url="http://opensearch:9200",
        qdrant_url="http://qdrant:6333",
        qdrant_timeout_seconds=60,
        qdrant_max_attempts=3,
        retry_sleeper=sleeper,
    )
    operation = Mock(side_effect=[ResponseHandlingException("timed out"), {"ok": True}])

    assert indexer._qdrant_call("写入测试向量", operation) == {"ok": True}
    assert operation.call_count == 2
    sleeper.assert_called_once_with(0.25)


def test_search_indexer_preserves_permanent_qdrant_failure_context() -> None:
    indexer = SearchIndexer(
        opensearch_url="http://opensearch:9200",
        qdrant_url="http://qdrant:6333",
        retry_sleeper=Mock(),
    )

    with pytest.raises(RuntimeError, match="Qdrant 创建失败（unknown）") as caught:
        indexer._qdrant_call("创建", Mock(side_effect=ValueError("bad collection")))

    assert isinstance(caught.value.__cause__, ValueError)


def test_failed_publish_entity_is_revived_without_changing_identity() -> None:
    deleted_at = datetime.now(timezone.utc)
    entity = SimpleNamespace(
        id="stable-entity-id",
        canonical_name="集团采购制度",
        confidence=0.7,
        scope_tokens=[],
        status="superseded",
        deleted_at=deleted_at,
    )

    restored = _restore_soft_deleted_entity(
        entity,
        status="staged",
        canonical_name="集团采购制度",
        confidence=0.85,
        scope_tokens=["space:demo"],
    )

    assert restored is True
    assert entity.id == "stable-entity-id"
    assert entity.deleted_at is None
    assert entity.status == "staged"
    assert entity.confidence == 0.85
    assert entity.scope_tokens == ["space:demo"]
    assert _restore_soft_deleted_entity(entity, status="staged") is False


def test_worker_marks_provisional_graph_inactive_after_downstream_failure() -> None:
    worker = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "apps/worker/tasks.py"
    ).read_text(encoding="utf-8")

    assert 'attempted_graph.status = "failed"' in worker
    assert "attempted_graph.deleted_at = now()" in worker
    assert '"activation_status": "failed"' in worker
    assert "KnowledgeRelease.graph_release_id == attempt_graph_release_id" in worker
