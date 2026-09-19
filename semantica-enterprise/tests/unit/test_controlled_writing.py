from __future__ import annotations

import pytest

from packages.platform.controlled_writing import (
    PropagationEdge,
    align_inheritance,
    build_value_free_skeleton,
    classify_edit_operations,
    corpus_artifact_manifest,
    propagation_closure,
    skeletonize_untrusted_sample,
)


def test_skeleton_borrows_shape_without_historical_values() -> None:
    result = build_value_free_skeleton(
        "本项目建筑面积为12345平方米，总投资为67890万元。",
        [
            {"key": "building_area", "historical_value": 12345, "source_text": "12345", "unit": "平方米"},
            {"key": "total_investment", "historical_value": 67890, "source_text": "67890", "unit": "万元"},
        ],
    )
    assert result["text"] == "本项目建筑面积为{{node:building_area}}平方米，总投资为{{node:total_investment}}万元。"
    assert "12345" not in result["text"]
    assert "67890" not in result["text"]


def test_skeleton_rejects_unlocatable_value() -> None:
    with pytest.raises(ValueError, match="does not occur"):
        build_value_free_skeleton("正文没有该数值", [{"key": "cost", "historical_value": 100}])


def test_untrusted_sample_never_exposes_numeric_literals() -> None:
    result = skeletonize_untrusted_sample("历史项目面积12000平方米，投资8.5亿元。", source_id="chunk-1")
    assert "12000" not in result["text"]
    assert "8.5" not in result["text"]
    assert len(result["slots"]) == 2


def test_corpus_manifest_maps_artifacts_without_duplicate_authority() -> None:
    result = corpus_artifact_manifest(
        package_id="p1", version=1, source_ids=["d2", "d1"],
        outline=[{"title": "项目概况"}], skeletons=[{"text": "{{node:area}}"}],
        graph_release_id="g1", created_at="2026-09-19T00:00:00Z",
    )
    assert result["artifact_mapping"]["evidence"] == "postgres:writing_evidence"
    assert result["artifact_mapping"]["assets"] == "minio:document-assets"
    assert result["authority_principle"] == "one-current-authority-versioned-projections"


def test_inheritance_never_copies_historical_values() -> None:
    result = align_inheritance(
        [
            {"key": "building_area", "label": "建筑面积", "unit": "平方米", "required": True},
            {"key": "construction_cost", "label": "建安工程费", "unit": "万元", "required": True,
             "formula": "quantity * unit_price", "dependencies": ["quantity", "unit_price"]},
            {"key": "interest", "label": "建设期利息", "unit": "万元", "required": True},
        ],
        [
            {"id": "f1", "fact_key": "building_area", "label": "建筑面积", "value": {"number": 22000}, "unit": "㎡"},
            {"id": "f2", "fact_key": "quantity", "label": "土建工程量", "value": {"number": 10}, "unit": "平方米"},
            {"id": "f3", "fact_key": "unit_price", "label": "土建单价", "value": {"number": 4}, "unit": "万元"},
        ],
    )
    assert result["alignments"][0]["decision"] == "use_current_project_value"
    assert result["alignments"][1]["decision"] == "recompute"
    assert result["alignments"][2]["status"] == "blocking"
    assert result["historical_values_inherited"] == 0


def test_inheritance_semantic_candidate_requires_human_confirmation() -> None:
    result = align_inheritance(
        [{"key": "construction_area", "label": "建筑总面积", "unit": "平方米", "required": True}],
        [{"id": "f-area", "fact_key": "gross_floor_area", "label": "总建筑面积", "value": {"number": 22000}, "unit": "㎡"}],
    )
    item = result["alignments"][0]
    assert item["match_method"] == "semantic_candidate"
    assert item["decision"] == "semantic_match_requires_confirmation"
    assert item["status"] == "needs_confirmation"
    assert item["historical_value_inherited"] is False


def test_inheritance_never_applies_unverified_current_fact() -> None:
    result = align_inheritance(
        [{"key": "total_cost", "label": "总投资", "unit": "万元", "required": True}],
        [{"id": "f-cost", "fact_key": "total_cost", "label": "总投资", "value": {"number": 9000}, "unit": "万元", "verification_status": "unverified"}],
    )
    item = result["alignments"][0]
    assert item["status"] == "needs_confirmation"
    assert item["decision"] == "current_fact_requires_confirmation"
    assert item["fact_verified"] is False


def test_editor_diff_blocks_unbound_precise_number() -> None:
    verdicts = classify_edit_operations(
        [{"operation": "ADD", "block_id": "b2", "after": "总投资为12345万元"}],
        {"b1": [{"binding_type": "project_fact", "binding_id": "f1"}]},
    )
    assert verdicts[0]["semantic_relation"] == "UNBOUND"
    assert verdicts[0]["confidence"] == "blocking"


def test_editor_diff_distinguishes_wording_and_bound_value_change() -> None:
    bindings = {"b1": [{"binding_type": "project_fact", "binding_id": "f1"}]}
    value_change, wording = classify_edit_operations(
        [
            {"operation": "MOD", "block_id": "b1", "before": "可用320人", "after": "可用400人"},
            {"operation": "MOD", "block_id": "b1", "before": "应立即组织", "after": "应当立即组织"},
        ],
        bindings,
    )
    assert value_change["propagation_required"] is True
    assert value_change["confidence"] == "deterministic"
    assert wording["propagation_required"] is False


def test_propagation_closure_tracks_direct_indirect_and_cycle() -> None:
    result = propagation_closure(
        ["fact:available"],
        [
            PropagationEdge("fact:available", "run:gap", "DERIVES_FROM", {}),
            PropagationEdge("run:gap", "fact:gap", "DERIVES_FROM", {}),
            PropagationEdge("fact:gap", "chunk:summary", "RESTATES", {}),
            PropagationEdge("chunk:summary", "fact:available", "REFERENCES", {}),
        ],
    )
    assert [item["node_id"] for item in result["impacts"]] == ["run:gap", "fact:gap", "chunk:summary"]
    assert result["direct_count"] == 1
    assert result["indirect_count"] == 2
    assert result["cycles"]


def test_propagation_rejects_unknown_relation() -> None:
    with pytest.raises(ValueError, match="unsupported propagation relation"):
        propagation_closure(["a"], [{"source": "a", "target": "b", "relation": "GUESSES"}])
