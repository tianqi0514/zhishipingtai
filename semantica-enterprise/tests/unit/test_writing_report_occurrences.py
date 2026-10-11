from packages.platform.writing_report_occurrences import bind_report_numeric_positions
from packages.platform.writing_occurrences import validate_numeric_occurrences
from packages.platform.writing_impact import propose_bound_text_change


def table():
    return {"id": "cost-table", "type": "table", "children": [
        {"type": "tr", "children": [{"type": "td", "children": [{"text": value}]} for value in row]}
        for row in [["费用项目", "金额（万元）", "公式"],
                    ["建安工程费用", "10000.00", "建筑面积×单价÷10000"]]]}


def metric():
    return {"id": "cost-run", "result": {"value": 10000,
        "output_fact": {"fact_key": "cost", "label": "建安工程费用", "unit": "万元"}}}


def test_structured_amount_binds_without_touching_equal_conversion_constant():
    original = table()
    bound = bind_report_numeric_positions(original, {"metric_keys": ["cost"]},
        inputs_by_key={}, metrics_by_key={"cost": metric()})
    occurrences = validate_numeric_occurrences(bound)
    assert len(occurrences) == 1 and occurrences[0]["leaf_path"] == [1, 1, 0]
    proposal = propose_bound_text_change(bound, [{"result_key": "cost", "previous_run_id": "cost-run",
        "old_value": 10000, "new_value": 11000, "unit": "万元"}])
    assert proposal["binding_mode"] == "occurrence"
    assert proposal["new_node"]["children"][1]["children"][1]["children"][0]["text"] == "11000.00"
    assert proposal["new_node"]["children"][1]["children"][2]["children"][0]["text"].endswith("÷10000")
    assert original["children"][1]["children"][1]["children"][0]["text"] == "10000.00"


def test_legacy_table_and_formula_do_not_update_constants_by_false_unique_match():
    proposal = propose_bound_text_change(table(), [{"old_value": 10000, "new_value": 11000}])
    assert not proposal["selectable"] and "唯一" in proposal["reason"]
    formula = {"id": "formula", "type": "p", "children": [{"text": "投资=面积×单价÷10000。"}]}
    proposal = propose_bound_text_change(formula, [{"old_value": 10000, "new_value": 11000}])
    assert not proposal["selectable"] and "公式" in proposal["reason"]


def test_prose_binds_only_unambiguous_value_unit_not_formula():
    fact = {"id": "unit-fact", "fact_key": "unit_cost", "label": "单价", "version": 1,
        "value": {"number": 5000}, "unit": "元/平方米", "verification_status": "verified"}
    node = {"id": "prose", "type": "p", "children": [{"text": "单价为5000元/平方米，换算除以5000。"}]}
    bound = bind_report_numeric_positions(node, {"input_keys": ["unit_cost"]},
        inputs_by_key={"unit_cost": fact}, metrics_by_key={})
    proposal = propose_bound_text_change(bound, [{"fact_key": "unit_cost", "fact_id": "unit-fact",
        "old_value": 5000, "new_value": 5500, "unit": "元/平方米"}])
    assert proposal["new_text"] == "单价为5500元/平方米，换算除以5000。"
    assert len(validate_numeric_occurrences(bound)) == 1


def test_wrong_unit_or_ambiguous_authority_does_not_bind():
    node = table()
    other = {**metric(), "id": "other-run"}
    assert not validate_numeric_occurrences(bind_report_numeric_positions(node,
        {"metric_keys": ["cost", "other"]}, inputs_by_key={}, metrics_by_key={"cost": metric(), "other": other}))
    node["children"][0]["children"][1]["children"][0]["text"] = "金额（元）"
    assert not validate_numeric_occurrences(bind_report_numeric_positions(node,
        {"metric_keys": ["cost"]}, inputs_by_key={}, metrics_by_key={"cost": metric()}))


def test_computed_fact_and_its_run_are_one_authority_in_prose_and_table():
    fact = {"id": "output-fact", "label": "建安工程费用", "version": 1,
            "value": {"number": 10000}, "unit": "万元", "verification_status": "verified"}
    metadata = {"input_keys": ["cost"], "metric_keys": ["cost"]}
    for node in [table(), {"id": "summary", "type": "p", "children": [{"text": "建安工程费用为10,000万元。"}]}]:
        bound = bind_report_numeric_positions(node, metadata, inputs_by_key={"cost": fact}, metrics_by_key={"cost": metric()})
        occurrences = validate_numeric_occurrences(bound)
        assert len(occurrences) == 1
        assert occurrences[0]["computation_run_id"] == "cost-run"
        proposal = propose_bound_text_change(bound, [{"result_key": "cost", "previous_run_id": "cost-run",
            "old_value": 10000, "new_value": 11000, "unit": "万元"}])
        assert proposal["selectable"]
        assert "11,000万元" in proposal["new_text"] or "11000.00" in proposal["new_text"]


def test_bad_grouping_is_not_registered_as_a_number():
    node = {"id": "summary", "type": "p", "children": [{"text": "建安工程费用为10,00万元。"}]}
    bound = bind_report_numeric_positions(node, {"metric_keys": ["cost"]}, inputs_by_key={}, metrics_by_key={"cost": metric()})
    assert not validate_numeric_occurrences(bound)


def test_serial_number_column_does_not_hide_the_authoritative_amount():
    node = table()
    for ri, row in enumerate(node["children"]):
        row["children"].insert(0, {"type": "td", "children": [{"text": "序号" if ri == 0 else "1"}]})
    bound = bind_report_numeric_positions(node, {"metric_keys": ["cost"]},
        inputs_by_key={}, metrics_by_key={"cost": metric()})
    occurrences = validate_numeric_occurrences(bound)
    assert len(occurrences) == 1 and occurrences[0]["leaf_path"] == [1, 2, 0]
    proposal = propose_bound_text_change(bound, [{"result_key": "cost", "previous_run_id": "cost-run",
        "old_value": 10000, "new_value": 11000, "unit": "万元"}])
    assert "11000.00" in proposal["new_text"]
    cells = proposal["new_node"]["children"][1]["children"]
    assert cells[0]["children"][0]["text"] == "1"
    assert cells[3]["children"][0]["text"].endswith("÷10000")


def test_multiple_equal_authorities_in_one_row_remain_ambiguous():
    node = table()
    node["children"][1]["children"].append({"type": "td", "children": [{"text": "另一费用"}]})
    other = {"id": "other-run", "result": {"value": 10000,
        "output_fact": {"fact_key": "other", "label": "另一费用", "unit": "万元"}}}
    bound = bind_report_numeric_positions(node, {"metric_keys": ["cost", "other"]},
        inputs_by_key={}, metrics_by_key={"cost": metric(), "other": other})
    assert not validate_numeric_occurrences(bound)


def test_conflicting_fact_and_computation_snapshots_do_not_bind():
    fact = {"id": "output-fact", "label": "建安工程费用", "version": 1,
            "value": {"number": 9000}, "unit": "万元", "verification_status": "verified"}
    bound = bind_report_numeric_positions(table(), {"input_keys": ["cost"], "metric_keys": ["cost"]},
        inputs_by_key={"cost": fact}, metrics_by_key={"cost": metric()})
    assert not validate_numeric_occurrences(bound)


def test_proposed_transition_is_not_rebound_as_current_fact():
    node = {"id": "candidate", "type": "p", "children": [{"text": "拟将建安费由10000万元调整为11000万元，仍待确认。"}]}
    bound = bind_report_numeric_positions(node, {"metric_keys": ["cost"]}, inputs_by_key={}, metrics_by_key={"cost": metric()})
    assert not validate_numeric_occurrences(bound)
    change = propose_bound_text_change(bound, [{"result_key": "cost", "old_value": 10000, "new_value": 11000}])
    assert not change["selectable"] and "历史或候选" in change["reason"]
    assert "由10000万元调整为11000万元" in change["old_text"]


def test_table_formula_input_has_exact_binding_but_conversion_constant_does_not():
    node = table()
    node["children"][1]["children"][2]["children"][0]["text"] = "建筑面积20000平方米×建筑安装单价5000元/平方米÷10000"
    fact = {"id": "unit-fact", "label": "建筑安装单价", "version": 1,
        "value": {"number": 5000}, "unit": "元/平方米", "verification_status": "verified"}
    bound = bind_report_numeric_positions(node, {"input_keys": ["unit_cost"], "metric_keys": ["cost"]},
        inputs_by_key={"unit_cost": fact}, metrics_by_key={"cost": metric()})
    assert len(validate_numeric_occurrences(bound)) == 2
    proposal = propose_bound_text_change(bound, [
        {"fact_key": "unit_cost", "fact_id": "unit-fact", "old_value": 5000, "new_value": 5500},
        {"result_key": "cost", "previous_run_id": "cost-run", "old_value": 10000, "new_value": 11000}])
    cells = proposal["new_node"]["children"][1]["children"]
    assert cells[1]["children"][0]["text"] == "11000.00"
    assert "".join(leaf["text"] for leaf in cells[2]["children"]) == "建筑面积20000平方米×建筑安装单价5500元/平方米÷10000"


def test_registered_formula_only_block_requires_review_not_number_search():
    node = {"id": "formula", "type": "p", "children": [{"text": "建安费用=面积×单价÷10000。"}]}
    bound = bind_report_numeric_positions(node, {"metric_keys": ["cost"]}, inputs_by_key={}, metrics_by_key={"cost": metric()})
    proposal = propose_bound_text_change(bound, [{"result_key": "cost", "previous_run_id": "cost-run", "old_value": 10000, "new_value": 11000}])
    assert proposal["selectable"] and proposal["review_only"]
    assert proposal["new_text"] == proposal["old_text"] and proposal["new_text"].endswith("÷10000。")


def test_export_resolves_nested_and_metadata_authorities_not_other_articles():
    from packages.platform.writing_export import bound_authority_ids
    bound = bind_report_numeric_positions(table(), {"metric_keys": ["cost"]}, inputs_by_key={}, metrics_by_key={"cost": metric()})
    facts, runs = bound_authority_ids([bound], {
        "cost-table": {"metadata_json": {"computation_run_ids": ["metadata-run"], "input_fact_ids": ["input-version"]}},
        "other-article": {"computation_run_id": "foreign-run", "fact_id": "foreign-fact"}})
    assert facts == {"input-version"}
    assert runs == {"metadata-run", "cost-run"}


def test_compact_report_uses_real_table_occurrences_not_duplicate_metric_cards():
    from packages.platform.writing_flow import assemble_report_content, report_quality_review
    sections = [{"key": "investment", "title": "投资估算", "toolbox_outputs": ["cost"]}]
    nodes, bindings = assemble_report_content(run_id="report", title="报告", section_plan=sections,
        agent_sections=[{"section_key": "investment", "content_nodes": [{"type": "table",
            "items": [["费用项目", "金额（万元）", "公式"], ["建安工程费用", "10000.00", "面积×单价÷10000"]],
            "input_refs": [], "metric_refs": ["cost"]}]}], citations=[], computations=[metric()],
        inference_facts=[], selected_plan=None, compact_metrics=True)
    assert not any(node["type"] == "computed_metric" for node in nodes)
    assert len(validate_numeric_occurrences(nodes[2])) == 1
    report = report_quality_review(nodes, section_plan=sections,
        bindings={row["block_id"]: row for row in bindings}, expected_computation_count=1,
        expected_computation_ids={"cost-run"}, require_citations=False)
    assert report["ok"] and report["metrics"]["represented_computation_count"] == 1
    denied = report_quality_review(nodes, section_plan=sections,
        bindings={row["block_id"]: row for row in bindings}, expected_computation_count=1,
        expected_computation_ids={"unbound-run"}, require_citations=False)
    assert not denied["ok"] and any(row["code"] == "missing_computation_nodes" for row in denied["issues"])
