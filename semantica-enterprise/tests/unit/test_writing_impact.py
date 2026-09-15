from packages.platform.writing_impact import propose_bound_text_change


def test_bound_metric_updates_one_registered_number_and_keeps_citation() -> None:
    node = {
        "id": "paragraph-1", "type": "p", "children": [
            {"text": "可用搜救人员320人，缺口180人"},
            {"id": "citation-1", "type": "knowledge_citation", "children": [{"text": ""}]},
            {"text": "。"},
        ],
    }
    result = propose_bound_text_change(node, [
        {"old_value": 320, "new_value": 400},
        {"old_value": 180, "new_value": 100},
    ])
    assert result["selectable"] is True
    assert result["new_text"] == "可用搜救人员400人，缺口100人。"
    assert result["new_node"]["children"][1] == node["children"][1]
    assert node["children"][0]["text"] == "可用搜救人员320人，缺口180人"


def test_ambiguous_or_unbound_value_does_not_receive_a_false_proposal() -> None:
    node = {"id": "paragraph-1", "type": "p", "children": [{"text": "320人加上320人"}]}
    result = propose_bound_text_change(node, [{"old_value": 320, "new_value": 400}])
    assert result["selectable"] is False
    assert "唯一" in result["reason"]
    assert "new_node" not in result


def test_non_numeric_claim_is_left_for_human_review() -> None:
    node = {"id": "paragraph-2", "type": "p", "children": [{"text": "由原单位负责。"}]}
    result = propose_bound_text_change(node, [{"old_value": {"text": "原单位"}, "new_value": {"text": "新单位"}}])
    assert result["selectable"] is False
    assert "人工" in result["reason"]
