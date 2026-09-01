from __future__ import annotations

from packages.platform.application_services import (
    aggregate_evaluation_metrics,
    evaluation_gate_passed,
    retrieval_metrics,
)


def test_retrieval_metrics_are_rank_sensitive_and_deterministic() -> None:
    metrics = retrieval_metrics(
        ["noise", "expected-b", "expected-a"],
        ["expected-a", "expected-b"],
        k=3,
    )

    assert metrics["recall_at_k"] == 1.0
    assert metrics["mrr"] == 0.5
    assert 0 < metrics["ndcg_at_k"] < 1
    assert metrics["passed"] is True


def test_empty_expected_evidence_does_not_create_a_false_failure() -> None:
    metrics = retrieval_metrics([], [], k=8)

    assert metrics["recall_at_k"] is None
    assert metrics["ndcg_at_k"] is None
    assert metrics["mrr"] == 0.0
    assert metrics["passed"] is True


def test_aggregate_and_quality_gate_use_declared_thresholds() -> None:
    aggregate = aggregate_evaluation_metrics([
        {"recall_at_k": 1.0, "mrr": 1.0, "ndcg_at_k": 1.0, "passed": True},
        {"recall_at_k": 0.5, "mrr": 0.5, "ndcg_at_k": 0.6, "passed": True},
    ])

    assert aggregate == {
        "case_count": 2,
        "passed_count": 2,
        "recall_at_k": 0.75,
        "mrr": 0.75,
        "ndcg_at_k": 0.8,
    }
    assert evaluation_gate_passed(aggregate, {"recall_at_k": 0.7, "mrr": 0.7}) is True
    assert evaluation_gate_passed(aggregate, {"ndcg_at_k": 0.9}) is False
