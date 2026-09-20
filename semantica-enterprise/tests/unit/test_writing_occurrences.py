from copy import deepcopy

import pytest

from packages.platform.writing_impact import propose_bound_text_change
from packages.platform.writing_occurrences import (
    bind_numeric_occurrences,
    iter_numeric_occurrences,
    rebind_numeric_occurrences,
    render_numeric_occurrence,
    validate_numeric_occurrences,
    validate_numeric_occurrence_edit,
)


def binding(**overrides):
    return {
        "occurrence_id": "position-1", "fact_key": "available", "fact_id": "fact-v1",
        "fact_version": 1, "evidence_ids": ["evidence-1"], "value": 320, "unit": "人", **overrides,
    }


def leaf(text="320", **overrides):
    return {"text": text, "writing_binding": binding(**overrides)}


def changes(**overrides):
    return [{"fact_key": "available", "fact_id": "fact-v1", "version": 1,
             "old_value": {"number": 320}, "new_value": {"number": 400}, "unit": "人", **overrides}]


def test_repeated_bound_occurrences_do_not_change_unbound_equal_number():
    node = {"id": "paragraph", "type": "p", "children": [
        {"text": "现有人数"}, leaf(), {"text": "人，汇总"}, leaf(occurrence_id="position-2"),
        {"text": "人；另一组320人。"},
        {"type": "knowledge_citation", "id": "citation", "children": [{"text": "[1]"}]},
    ]}
    baseline = deepcopy(node)
    result = propose_bound_text_change(node, changes())
    assert result["selectable"]
    assert result["new_text"] == "现有人数400人，汇总400人；另一组320人。[1]"
    assert len(result["occurrence_updates"]) == 2
    assert result["new_node"]["children"][-1] == node["children"][-1]
    assert result["occurrence_updates"][0]["evidence_ids"] == ["evidence-1"]
    assert node == baseline


def test_same_number_from_different_fact_is_not_updated():
    node = {"id": "p", "type": "p", "children": [leaf(), {"text": "/"},
        leaf(occurrence_id="other", fact_key="other", fact_id="other-v1")]}
    result = propose_bound_text_change(node, changes())
    assert result["new_text"] == "400/320"
    assert len(result["occurrence_updates"]) == 1


def test_table_cells_and_inline_nodes_use_same_exact_binding():
    node = {"id": "table", "type": "table", "children": [{"type": "tr", "children": [
        {"id": "cell-1", "type": "td", "children": [{"type": "p", "children": [leaf()]}]},
        {"id": "cell-2", "type": "td", "children": [{"type": "p", "children": [
            {"type": "controlled_value", "writing_binding": binding(occurrence_id="inline"),
             "children": [{"text": "320", "bold": True}]}]}]},
        {"id": "cell-3", "type": "td", "children": [{"type": "p", "children": [{"text": "320"}]}]},
    ]}]}
    result = propose_bound_text_change(node, changes())
    assert result["new_text"] == "400400320"
    assert result["new_node"]["children"][0]["children"][1]["children"][0]["children"][0]["children"][0]["bold"]


def test_money_display_conversion_and_rounding_are_not_authority_changes():
    meta = binding(value=12345678, unit="元", display_unit="万元", decimal_places=2, scale="0.0001", show_unit=True)
    node = {"id": "p", "type": "p", "children": [{"text": "1234.57万元", "writing_binding": meta}]}
    result = propose_bound_text_change(node, changes(old_value=12345678, new_value=14345678, unit="元"))
    assert result["new_text"] == "1434.57万元"
    assert result["new_node"]["children"][0]["writing_binding"]["value"] == 14345678


@pytest.mark.parametrize(("value", "metadata", "expected"), [
    (320, {}, "320"),
    (12345, {"grouping": True}, "12,345"),
    (12345.675, {"decimal_places": 2}, "12345.68"),
    (0.1289, {"unit": "比例", "display_unit": "%", "decimal_places": 2, "show_unit": True}, "12.89%"),
    (52000000, {"unit": "元", "display_unit": "亿元", "decimal_places": 3}, "0.520"),
])
def test_display_formats(value, metadata, expected):
    assert render_numeric_occurrence(binding(value=value, **metadata)) == expected


@pytest.mark.parametrize("value", [None, True, float("inf"), float("nan"), "320", {"number": None, "value": 320}])
def test_unknown_or_non_finite_value_is_rejected(value):
    with pytest.raises(ValueError):
        render_numeric_occurrence(binding(value=value))


def test_explicit_missing_override_is_not_the_existing_value():
    with pytest.raises(ValueError):
        render_numeric_occurrence(binding(), None)


@pytest.mark.parametrize("metadata", [
    {"display_unit": "万元"}, {"unit": "元", "display_unit": "万元", "scale": "10000"},
    {"scale": True}, {"decimal_places": 100}, {"rounding": "floor"},
    {"fact_version": None}, {"fact_id": None}, {"unknown": "ignored"},
])
def test_invalid_metadata_and_unit_changes_are_rejected(metadata):
    with pytest.raises(ValueError):
        render_numeric_occurrence(binding(**metadata))


@pytest.mark.parametrize("change", [
    {"fact_id": "later"}, {"version": 2}, {"old_value": 321}, {"unit": "张"}, {"new_value": None},
])
def test_stale_or_missing_change_is_not_selectable(change):
    node = {"id": "p", "type": "p", "children": [leaf()]}
    result = propose_bound_text_change(node, changes(**change))
    assert not result["selectable"]
    assert "new_node" not in result


def test_explicit_binding_does_not_fall_back_to_string_search():
    node = {"id": "p", "type": "p", "children": [leaf(), {"text": "320"}]}
    result = propose_bound_text_change(node, [{"old_value": 320, "new_value": 400}])
    assert not result["selectable"]
    assert "标识" in result["reason"]


def test_duplicate_occurrence_paste_and_changed_text_fail_validation():
    with pytest.raises(ValueError, match="标识重复"):
        validate_numeric_occurrences({"children": [leaf(), leaf()]})
    with pytest.raises(ValueError, match="不一致"):
        validate_numeric_occurrences({"children": [leaf(text="400")]})


def test_binding_splits_only_explicit_ranges_and_preserves_marks_and_citation():
    node = {"id": "p", "type": "p", "children": [
        {"text": "可用320人，参考320人", "bold": True},
        {"type": "knowledge_citation", "children": [{"text": "[1]"}]},
    ]}
    spec = {"leaf_path": [0], "start": 2, "end": 5, "binding": binding()}
    result = bind_numeric_occurrences(node, [spec])
    assert result["children"][0] == {"text": "可用", "bold": True}
    assert result["children"][1]["writing_binding"]["occurrence_id"] == "position-1"
    assert result["children"][1]["bold"]
    assert result["children"][2]["text"] == "人，参考320人"
    assert result["children"][3] == node["children"][1]
    assert "writing_binding" not in node["children"][0]


def test_multiple_ranges_are_processed_from_original_positions():
    node = {"id": "p", "type": "p", "children": [{"text": "320、320"}, {"text": "320"}]}
    specs = [
        {"leaf_path": [0], "start": 0, "end": 3, "binding": binding(occurrence_id="a")},
        {"leaf_path": [0], "start": 4, "end": 7, "binding": binding(occurrence_id="b")},
        {"leaf_path": [1], "start": 0, "end": 3, "binding": binding(occurrence_id="c")},
    ]
    result = bind_numeric_occurrences(node, specs)
    assert [item[0]["writing_binding"]["occurrence_id"] for item in iter_numeric_occurrences(result)] == ["a", "b", "c"]
    assert propose_bound_text_change(result, changes())["new_text"] == "400、400400"


def test_occurrence_id_generated_deterministically_but_differs_by_location():
    node = {"id": "p", "type": "p", "children": [{"text": "320320"}]}
    meta = binding()
    meta.pop("occurrence_id")
    specs = [{"leaf_path": [0], "start": start, "end": start + 3, "binding": meta} for start in [0, 3]]
    first, second = bind_numeric_occurrences(node, specs), bind_numeric_occurrences(node, specs)
    ids = [item[0]["writing_binding"]["occurrence_id"] for item in iter_numeric_occurrences(first)]
    assert len(set(ids)) == 2
    assert first == second


@pytest.mark.parametrize("spec", [
    {"leaf_path": [1], "start": 0, "end": 3},
    {"leaf_path": [0], "start": True, "end": 3},
    {"leaf_path": [0], "start": 0, "end": 9},
    {"leaf_path": [], "start": 0, "end": 3},
    {"leaf_path": [0], "start": 1, "end": 3},
])
def test_invalid_paths_and_ranges_rejected(spec):
    with pytest.raises(ValueError):
        bind_numeric_occurrences({"id": "p", "children": [{"text": "320"}]}, [{**spec, "binding": binding()}])


def test_rebinding_advances_source_versions_and_preserves_evidence():
    node = {"id": "p", "children": [leaf(computation_run_id="run-1")]}
    result = rebind_numeric_occurrences(node, fact_replacements={"fact-v1": {"id": "fact-v2", "version": 2}}, run_replacements={"run-1": "run-2"})
    meta = result["children"][0]["writing_binding"]
    assert (meta["fact_id"], meta["fact_version"], meta["computation_run_id"]) == ("fact-v2", 2, "run-2")
    assert meta["evidence_ids"] == ["evidence-1"]
    assert node["children"][0]["writing_binding"]["fact_id"] == "fact-v1"


def test_computation_identity_prevents_reusing_stale_run():
    node = {"id": "p", "type": "p", "children": [leaf(fact_id=None, fact_version=None, computation_run_id="run-1")]}
    result = propose_bound_text_change(node, changes(previous_run_id="run-2"))
    assert not result["selectable"]
    assert "计算版本" in result["reason"]


def test_legacy_updates_do_not_consume_previous_replacement():
    node = {"id": "p", "type": "p", "children": [{"text": "100人、200人"}]}
    result = propose_bound_text_change(node, [{"old_value": 100, "new_value": 200}, {"old_value": 200, "new_value": 300}])
    assert result["new_text"] == "200人、300人"
    assert result["binding_mode"] == "legacy_unique_number"


def test_legacy_same_location_with_conflicting_changes_is_rejected():
    node = {"id": "p", "type": "p", "children": [{"text": "100人"}]}
    result = propose_bound_text_change(node, [{"old_value": 100, "new_value": 200}, {"old_value": 100, "new_value": 300}])
    assert not result["selectable"]


def test_position_dependencies_exist_without_legacy_binding_and_keep_all_occurrences():
    from packages.platform.writing_chunks import _dependency_specs

    node = {"id": "p", "children": [leaf(), leaf(occurrence_id="position-2")]}
    specs = _dependency_specs(None, node)
    fact = next(item for item in specs if item["binding_type"] == "project_fact")
    assert fact["binding_id"] == "fact-v1"
    assert fact["binding_version"] == "1"
    assert len(fact["metadata"]["occurrences"]) == 2
    assert {item["binding_type"] for item in specs} == {"project_fact", "writing_evidence"}


def test_native_source_chunk_evidence_is_not_mislabelled_as_writing_evidence():
    from packages.platform.writing_chunks import _dependency_specs

    specs = _dependency_specs(None, {"id": "p", "children": [leaf(evidence_type="source_chunk")]})
    assert {item["binding_type"] for item in specs} == {"project_fact", "source_chunk"}


def test_ordinary_save_cannot_forge_consistent_value_and_metadata():
    previous = [{"id": "p", "type": "p", "children": [leaf()]}]
    changed = deepcopy(previous)
    changed[0]["children"][0].update({"text": "400", "writing_binding": binding(value=400)})
    with pytest.raises(ValueError, match="指标变更"):
        validate_numeric_occurrence_edit(previous, changed)


def test_ordinary_save_cannot_create_fake_authority_or_duplicate_across_blocks():
    with pytest.raises(ValueError, match="不能新增"):
        validate_numeric_occurrence_edit([], [{"id": "p", "children": [leaf()]}])
    with pytest.raises(ValueError, match="标识重复"):
        validate_numeric_occurrence_edit([], [{"id": "a", "children": [leaf()]}, {"id": "b", "children": [leaf()]}])


def test_ordinary_save_allows_moving_formatting_and_reports_deletion():
    previous = [{"id": "p", "children": [leaf(), {"text": "备注"}]}]
    moved = [{"id": "p", "children": [{"text": "备注"}, {**leaf(), "bold": True}]}]
    assert validate_numeric_occurrence_edit(previous, moved) == {"removed_occurrence_ids": [], "moved_occurrence_ids": ["position-1"]}
    assert validate_numeric_occurrence_edit(previous, [{"id": "p", "children": [{"text": "备注"}]}])["removed_occurrence_ids"] == ["position-1"]
