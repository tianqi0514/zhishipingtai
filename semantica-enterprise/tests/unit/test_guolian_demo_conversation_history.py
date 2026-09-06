from __future__ import annotations

import json

from scripts.demo.prepare_guolian_demo_conversations import (
    GRAPH_TOOLS,
    SPECS,
    _parse_sse,
)


class _Response:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def iter_lines(self):
        return iter(self._lines)


def test_demo_history_has_unique_expected_titles_and_real_multiturn_case() -> None:
    assert [spec.title for spec in SPECS] == [
        "演示01｜不使用图谱",
        "演示02｜使用图谱增强",
        "演示03｜制度与经营数据",
        "演示04｜多模态项目风险",
    ]
    assert len({spec.title for spec in SPECS}) == len(SPECS)
    assert len(SPECS[-1].questions) == 3
    assert SPECS[-1].required_source_title_fragments == (
        "项目总体架构图",
        "智慧流程中枢项目例会（演示版）",
        "智慧流程中枢项目介绍",
    )


def test_graph_comparison_uses_the_same_question_and_distinct_tool_contracts() -> None:
    without_graph, with_graph = SPECS[:2]
    assert without_graph.questions == with_graph.questions
    assert without_graph.use_graph is False
    assert with_graph.use_graph is True
    assert not (set(without_graph.required_tools) & GRAPH_TOOLS)
    assert GRAPH_TOOLS.issubset(with_graph.required_tools)


def test_demo_history_sse_parser_preserves_terminal_event() -> None:
    response = _Response([
        "event: turn_started",
        f"data: {json.dumps({'turn': 1})}",
        "",
        "event: turn_completed",
        f"data: {json.dumps({'duration_ms': 12})}",
        "",
    ])
    assert _parse_sse(response) == [
        ("turn_started", {"turn": 1}),
        ("turn_completed", {"duration_ms": 12}),
    ]
